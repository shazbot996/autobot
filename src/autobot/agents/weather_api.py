"""The weather agent's lookup: zip code to coordinates, Google's Weather API, the rate.

Three parts, none of which draws or speaks:

- `locate` turns a zip code into a latitude and longitude from the Census
  gazetteer of zip-code centroids, a table `make fetch-models` puts under
  `models/geo/`. It is read locally, so no second API is called and the key
  stays restricted to the one API.
- `GoogleWeather` is the `Lookup` over Google's Weather API. It calls two
  endpoints of `weather.googleapis.com` and refuses any other host. The key
  is a query parameter, built as a string only for the call. Every failure
  is a `WeatherError` whose message is one line and never holds the key:
  nothing of a request's URL or of a transport error's text goes into it.
- `RateControl` is the agent's own cap on its lookups (AG-6): it keeps the
  moments of the calls in a file under `run/` and refuses a call past the
  minute limit or the daily limit with one line. The cap on the API in
  Google Cloud (GC-10) is the backstop.

What a lookup sends:

- `today` makes two requests: `currentConditions:lookup` for now (condition,
  temperature, feels-like, humidity, wind), and `forecast/days:lookup` with
  `days=1` for today's high, low and chance of precipitation, which the
  current conditions do not carry (they hold the last 24 hours' range). When
  the second answers with no day, the last 24 hours' range stands in.
- `days` makes one request, `forecast/days:lookup` with `days=5`. Its
  `Forecast` is today's as forecast, not as measured: `temperature` is
  today's high and `feels_like` the high's feels-like. `days` holds a `Day`
  for each day answered, today first.
"""

from __future__ import annotations

import csv
import datetime
import json
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

# The gazetteer of zip-code centroids, relative to the models root: a
# tab-separated table with a header naming at least the three columns below.
GEO_TABLE = "models/geo/zcta.tsv"
GEO_ZIP_COLUMN = "GEOID"
GEO_LATITUDE_COLUMN = "INTPTLAT"
GEO_LONGITUDE_COLUMN = "INTPTLONG"

WEATHER_HOST = "weather.googleapis.com"
CURRENT_URL = f"https://{WEATHER_HOST}/v1/currentConditions:lookup"
DAYS_URL = f"https://{WEATHER_HOST}/v1/forecast/days:lookup"
# How many days `days` asks for.
FORECAST_DAYS = 5
# How long one call may take, in seconds.
TIMEOUT = 20.0

MINUTE = 60.0
DAY = 24 * 60 * 60.0

UNKNOWN = "Unknown"


class WeatherError(Exception):
    """A weather lookup failed or was refused. One line, never the key."""

    def __init__(self, message: str) -> None:
        super().__init__(" ".join(str(message).split()))


@dataclass(frozen=True)
class Day:
    """One day of the days ahead."""

    # "Saturday"
    name: str
    # "Light rain"
    condition: str
    # Degrees Fahrenheit.
    high: int
    low: int
    # Percent chance.
    precipitation: int


@dataclass(frozen=True)
class Forecast:
    """The weather at a zip code, as plain data."""

    # The zip code looked up.
    place: str
    # Now, or today's: "Partly cloudy".
    condition: str
    # Now, degrees Fahrenheit.
    temperature: int
    feels_like: int
    # Today's.
    high: int
    low: int
    # Percent chance today.
    precipitation: int
    # Percent.
    humidity: int
    # "8 mph NW"
    wind: str
    # The days ahead; empty for `today`.
    days: tuple[Day, ...] = ()


class Lookup(Protocol):
    """What the weather agent asks: today's weather, and the days ahead.

    `key` is the API key's bytes. The lookup does not clear it: the caller
    does, after the call.
    """

    def today(self, zip_code: str, key: bytearray) -> Forecast: ...

    def days(self, zip_code: str, key: bytearray) -> Forecast: ...


# --- the zip code's coordinates -------------------------------------------------


def locate(zip_code: str, models_root: Path) -> tuple[float, float]:
    """The (latitude, longitude) of a zip code, from the local gazetteer.

    `WeatherError` in one line when the table is missing (it names
    `make fetch-models`) and when the zip code is not in it (it names the
    zip code).
    """
    wanted = zip_code.strip()
    table = models_root / GEO_TABLE
    try:
        with table.open(encoding="utf-8", newline="") as handle:
            rows = csv.reader(handle, delimiter="\t")
            header = [name.strip() for name in next(rows, [])]
            try:
                columns = [
                    header.index(name)
                    for name in (
                        GEO_ZIP_COLUMN,
                        GEO_LATITUDE_COLUMN,
                        GEO_LONGITUDE_COLUMN,
                    )
                ]
            except ValueError:
                raise WeatherError(
                    f"{GEO_TABLE} is not the zip code table: "
                    "delete it and run 'make fetch-models'"
                ) from None
            for row in rows:
                if len(row) <= max(columns) or row[columns[0]].strip() != wanted:
                    continue
                try:
                    latitude = float(row[columns[1]])
                    longitude = float(row[columns[2]])
                except ValueError:
                    break
                if math.isfinite(latitude) and math.isfinite(longitude):
                    return latitude, longitude
                break
    except (OSError, UnicodeDecodeError, csv.Error):
        raise WeatherError(
            f"no zip code table at {GEO_TABLE}: run 'make fetch-models'"
        ) from None
    if not wanted:
        raise WeatherError("no zip code was given to look up")
    raise WeatherError(f"zip code {wanted} is not in {GEO_TABLE}")


# --- Google's Weather API -------------------------------------------------------


class GoogleWeather:
    """`Lookup` over Google's Weather API, with an API key.

    `session` needs one method, `get(url, params=..., timeout=...)`,
    answering with an object that has `status_code` and `json()`: a
    `requests.Session` in a real run, made on the first call, and a fake in
    the tests.
    """

    def __init__(self, models_root: Path, session: Any | None = None) -> None:
        self.models_root = models_root
        self.session = session

    def today(self, zip_code: str, key: bytearray) -> Forecast:
        latitude, longitude = locate(zip_code, self.models_root)
        now = self._get(CURRENT_URL, latitude, longitude, key)
        ahead = self._get(DAYS_URL, latitude, longitude, key, {"days": "1"})
        temperature = _degrees(now.get("temperature"))
        if temperature is None:
            raise WeatherError(f"{WEATHER_HOST} answered without the temperature")
        history = _mapping(now.get("currentConditionsHistory"))
        days = _days(ahead)
        if days:
            high = _or(_degrees(days[0].get("maxTemperature")), temperature)
            low = _or(_degrees(days[0].get("minTemperature")), temperature)
            precipitation = _day_precipitation(days[0])
        else:
            high = _or(_degrees(history.get("maxTemperature")), temperature)
            low = _or(_degrees(history.get("minTemperature")), temperature)
            precipitation = _percent(now.get("precipitation"))
        return Forecast(
            place=zip_code.strip(),
            condition=_condition(now),
            temperature=temperature,
            feels_like=_or(_degrees(now.get("feelsLikeTemperature")), temperature),
            high=max(high, temperature),
            low=min(low, temperature),
            precipitation=precipitation,
            humidity=_whole(now.get("relativeHumidity")),
            wind=_wind(now.get("wind")),
        )

    def days(self, zip_code: str, key: bytearray) -> Forecast:
        latitude, longitude = locate(zip_code, self.models_root)
        ahead = self._get(
            DAYS_URL,
            latitude,
            longitude,
            key,
            {"days": str(FORECAST_DAYS), "pageSize": str(FORECAST_DAYS)},
        )
        days = _days(ahead)
        if not days:
            raise WeatherError(f"{WEATHER_HOST} answered without a day of forecast")
        lines = tuple(_day(day) for day in days)
        today = days[0]
        part = _day_part(today)
        high = lines[0].high
        return Forecast(
            place=zip_code.strip(),
            condition=lines[0].condition,
            temperature=high,
            feels_like=_or(_degrees(today.get("feelsLikeMaxTemperature")), high),
            high=high,
            low=lines[0].low,
            precipitation=lines[0].precipitation,
            humidity=_whole(part.get("relativeHumidity")),
            wind=_wind(part.get("wind")),
            days=lines,
        )

    # --- the one way out ------------------------------------------------------

    def _get(
        self,
        url: str,
        latitude: float,
        longitude: float,
        key: bytearray,
        more: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """One call. `WeatherError` for any failure, never with the key in it."""
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.scheme != "https" or host != WEATHER_HOST or parts.query:
            raise WeatherError(
                f"refused: {host or 'that address'} is not Google's Weather API"
            )
        if not key:
            raise WeatherError("the weather lookup was given no key")
        try:
            # The key as a string exists for this call only.
            secret = key.decode("utf-8").strip()
        except UnicodeDecodeError:
            raise WeatherError("the weather key is not text") from None
        params = {
            "key": secret,
            "location.latitude": f"{latitude:.6f}",
            "location.longitude": f"{longitude:.6f}",
            "unitsSystem": "IMPERIAL",
            **(more or {}),
        }
        try:
            if self.session is None:
                import requests

                self.session = requests.Session()
            response = self.session.get(url, params=params, timeout=TIMEOUT)
            status = int(response.status_code)
        except Exception as error:  # noqa: BLE001
            # No network, a timeout: the kind alone. A transport error's text
            # holds the URL with its query, and the key is in the query.
            raise WeatherError(f"cannot reach {host}: {type(error).__name__}") from None
        finally:
            params.clear()
        try:
            data = response.json()
        except Exception:  # noqa: BLE001
            data = None
        if status >= 400:
            reason = _scrub(_error_message(data), secret)
            raise WeatherError(f"{host} answered {status}: {reason}")
        if not isinstance(data, dict):
            raise WeatherError(f"{host} answered {status} with nothing to read")
        return data


def _scrub(text: str, secret: str) -> str:
    """`text` without the key, however the server wrote it back."""
    return text.replace(secret, "[key]") if secret else text


def _error_message(data: object) -> str:
    """The message of a Google API error body, as one short line."""
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict) and error.get("message"):
            return " ".join(str(error["message"]).split())[:200]
        if isinstance(error, str) and error:
            return " ".join(error.split())[:200]
    return "no reason given"


# --- reading an answer ----------------------------------------------------------
#
# An answer is read field by field, and a field that is missing or of another
# shape gets a fallback: only the temperature now, and one day of a forecast,
# are things a lookup cannot do without.

_CARDINALS = {"NORTH": "N", "EAST": "E", "SOUTH": "S", "WEST": "W"}
_SPEED_UNITS = {"MILES_PER_HOUR": "mph", "KILOMETERS_PER_HOUR": "km/h"}


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _whole(value: object) -> int:
    """A count or a percentage, 0 when it is not there."""
    number = _number(value)
    return 0 if number is None else round(number)


def _or(value: int | None, fallback: int) -> int:
    return fallback if value is None else value


def _degrees(value: object) -> int | None:
    """A `Temperature` in whole degrees Fahrenheit; None when it is not there."""
    temperature = _mapping(value)
    degrees = _number(temperature.get("degrees"))
    if degrees is None:
        return None
    if temperature.get("unit") == "CELSIUS":
        degrees = degrees * 9 / 5 + 32
    return round(degrees)


def _condition(part: Mapping[str, Any]) -> str:
    """The words of a `weatherCondition`: "Partly cloudy"."""
    condition = _mapping(part.get("weatherCondition"))
    text = _mapping(condition.get("description")).get("text")
    if isinstance(text, str) and text.strip():
        return " ".join(text.split())
    kind = condition.get("type")
    if isinstance(kind, str) and kind.strip() and kind != "TYPE_UNSPECIFIED":
        return kind.strip().replace("_", " ").capitalize()
    return UNKNOWN


def _percent(precipitation: object) -> int:
    """The chance of a `Precipitation`, as a whole percentage from 0 to 100."""
    probability = _mapping(_mapping(precipitation).get("probability"))
    return max(0, min(100, _whole(probability.get("percent"))))


def _wind(value: object) -> str:
    """A `Wind` as "8 mph NW"; "calm" when there is none to speak of."""
    wind = _mapping(value)
    speed = _mapping(wind.get("speed"))
    amount = _number(speed.get("value"))
    if amount is None or round(amount) <= 0:
        return "calm"
    unit = _SPEED_UNITS.get(str(speed.get("unit", "MILES_PER_HOUR")), "mph")
    cardinal = _mapping(wind.get("direction")).get("cardinal")
    words = cardinal.split("_") if isinstance(cardinal, str) else []
    # NORTH_NORTHWEST is written NNW: each word is one or two of the four.
    letters = "".join(
        _CARDINALS.get(name, "") for word in words for name in _split_cardinal(word)
    )
    return f"{round(amount)} {unit} {letters}".strip()


def _split_cardinal(word: str) -> tuple[str, ...]:
    """ "NORTHWEST" as ("NORTH", "WEST"); a single direction as itself."""
    for first in ("NORTH", "SOUTH"):
        if word.startswith(first) and word != first:
            return first, word[len(first) :]
    return (word,)


def _days(answer: Mapping[str, Any]) -> list[dict[str, Any]]:
    days = answer.get("forecastDays")
    return (
        [day for day in days if isinstance(day, dict)] if isinstance(days, list) else []
    )


def _day_part(day: Mapping[str, Any]) -> dict[str, Any]:
    """The part of a day that says what it is like: the daytime when it has one."""
    for name in ("daytimeForecast", "fullDayForecast", "nighttimeForecast"):
        part = _mapping(day.get(name))
        if _mapping(part.get("weatherCondition")):
            return part
    return {}


def _day_precipitation(day: Mapping[str, Any]) -> int:
    """The chance of precipitation in a day: the higher of its day and its night."""
    return max(
        _percent(_mapping(day.get(name)).get("precipitation"))
        for name in ("daytimeForecast", "nighttimeForecast", "fullDayForecast")
    )


def _day_name(day: Mapping[str, Any]) -> str:
    """The weekday of a forecast day's `displayDate`: "Saturday"."""
    shown = _mapping(day.get("displayDate"))
    try:
        date = datetime.date(int(shown["year"]), int(shown["month"]), int(shown["day"]))
    except (KeyError, TypeError, ValueError):
        return UNKNOWN
    return date.strftime("%A")


def _day(day: Mapping[str, Any]) -> Day:
    high = _degrees(day.get("maxTemperature"))
    low = _degrees(day.get("minTemperature"))
    return Day(
        name=_day_name(day),
        condition=_condition(_day_part(day)),
        high=_or(high, _or(low, 0)),
        low=_or(low, _or(high, 0)),
        precipitation=_day_precipitation(day),
    )


# --- the agent's own cap --------------------------------------------------------


class RateControl:
    """The weather agent's cap on its calls: so many a minute, so many a day.

    The moments of the calls are kept as JSON at `path`
    (`run/weather-calls.json`), so the count outlives the application. A
    moment older than a day is dropped. A file that is missing or cannot be
    read counts as no calls.
    """

    def __init__(
        self,
        daily_limit: int,
        minute_limit: int,
        path: Path,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.daily_limit = daily_limit
        self.minute_limit = minute_limit
        self.path = path
        self.clock = clock
        # The calls this instance recorded, should the file not take them.
        self._recorded: list[float] = []

    def refusal(self) -> str:
        """Empty when a call may go out now; else one line: which limit, and when it clears."""
        now = self.clock()
        today = self._moments(now)
        if len(today) >= self.daily_limit:
            # The limit clears when enough of the oldest calls are a day old.
            clears = today[len(today) - self.daily_limit] + DAY - now
            return (
                f"the weather agent's daily limit of {_calls(self.daily_limit)} "
                f"is reached: it clears in {_duration(clears)}"
            )
        recent = [moment for moment in today if moment > now - MINUTE]
        if len(recent) >= self.minute_limit:
            clears = recent[len(recent) - self.minute_limit] + MINUTE - now
            return (
                f"the weather agent's minute limit of {_calls(self.minute_limit)} "
                f"is reached: it clears in {_duration(clears)}"
            )
        return ""

    def record(self) -> None:
        """Note that a call went out now."""
        now = self.clock()
        moments = [*self._moments(now), now]
        self._recorded = moments
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            writing = self.path.with_name(self.path.name + ".tmp")
            writing.write_text(json.dumps({"calls": moments}) + "\n", encoding="utf-8")
            writing.replace(self.path)
        except OSError:
            # A directory that cannot be written does not stop the weather:
            # the count is kept here for as long as the application runs.
            pass

    def _moments(self, now: float) -> list[float]:
        """The calls of the last day, oldest first."""

        def of_the_day(moments: list[float]) -> list[float]:
            return sorted(moment for moment in moments if now - DAY < moment <= now)

        stored = of_the_day(self._stored())
        recorded = of_the_day(self._recorded)
        # The file's, unless it did not take what this instance recorded.
        return stored if len(stored) >= len(recorded) else recorded

    def _stored(self) -> list[float]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        calls = data.get("calls") if isinstance(data, dict) else None
        if not isinstance(calls, list):
            return []
        return [
            float(moment)
            for moment in calls
            if isinstance(moment, int | float)
            and not isinstance(moment, bool)
            and math.isfinite(moment)
        ]


def _calls(count: int) -> str:
    return f"{count} call" if count == 1 else f"{count} calls"


def _duration(seconds: float) -> str:
    """A wait in words: "42 seconds", "3 minutes", "5 hours 10 minutes"."""
    whole = max(1, math.ceil(seconds))
    if whole < 60:
        return f"{whole} second" if whole == 1 else f"{whole} seconds"
    minutes = math.ceil(whole / 60)
    if minutes < 60:
        return f"{minutes} minute" if minutes == 1 else f"{minutes} minutes"
    hours, minutes = divmod(minutes, 60)
    text = f"{hours} hour" if hours == 1 else f"{hours} hours"
    if minutes:
        text += f" {minutes} minute" if minutes == 1 else f" {minutes} minutes"
    return text
