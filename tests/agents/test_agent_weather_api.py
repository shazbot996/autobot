"""`autobot.agents.weather_api`: the zip code table, the lookup, the rate. No network."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from autobot.agents import weather_api
from autobot.agents.weather_api import (
    GEO_TABLE,
    Day,
    Forecast,
    GoogleWeather,
    RateControl,
    WeatherError,
    locate,
)

# Not a key: a stand-in no service would take.
KEY = "stand-in-key-0123456789"
ZIP = "10001"
TABLE = (
    "GEOID\tINTPTLAT\tINTPTLONG\n"
    "00601\t18.180621\t-66.749931\n"
    "10001\t40.750649\t-73.997298\n"
)


def models_root(tmp_path: Path, table: str = TABLE) -> Path:
    """A models root holding a small zip code table."""
    path = tmp_path / GEO_TABLE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(table)
    return tmp_path


@pytest.fixture(autouse=True)
def _a_table(tmp_path: Path) -> None:
    """Every test here has the small zip code table under its `tmp_path`."""
    models_root(tmp_path)


def key() -> bytearray:
    return bytearray(KEY.encode())


# --- answers as the API gives them, in imperial units ---------------------------


def degrees(value: float) -> dict[str, Any]:
    return {"degrees": value, "unit": "FAHRENHEIT"}


def condition(text: str, kind: str) -> dict[str, Any]:
    return {
        "iconBaseUri": "https://maps.gstatic.com/weather/v1/icon",
        "description": {"text": text, "languageCode": "en"},
        "type": kind,
    }


def chance(percent: int) -> dict[str, Any]:
    return {
        "probability": {"percent": percent, "type": "RAIN"},
        "qpf": {"quantity": 0, "unit": "INCHES"},
    }


def wind(speed: float, cardinal: str) -> dict[str, Any]:
    return {
        "direction": {"degrees": 315, "cardinal": cardinal},
        "speed": {"value": speed, "unit": "MILES_PER_HOUR"},
        "gust": {"value": speed + 6, "unit": "MILES_PER_HOUR"},
    }


CURRENT: dict[str, Any] = {
    "currentTime": "2026-11-27T15:00:00Z",
    "timeZone": {"id": "America/New_York"},
    "isDaytime": True,
    "weatherCondition": condition("Partly cloudy", "PARTLY_CLOUDY"),
    "temperature": degrees(48.4),
    "feelsLikeTemperature": degrees(44.6),
    "dewPoint": degrees(36.1),
    "relativeHumidity": 62,
    "uvIndex": 1,
    "precipitation": chance(5),
    "thunderstormProbability": 0,
    "wind": wind(8, "NORTHWEST"),
    "cloudCover": 40,
    "currentConditionsHistory": {
        "temperatureChange": degrees(-2.1),
        "maxTemperature": degrees(55.0),
        "minTemperature": degrees(39.0),
        "qpf": {"quantity": 0, "unit": "INCHES"},
    },
}


def forecast_day(
    day: int, text: str, high: float, low: float, by_day: int, by_night: int
) -> dict[str, Any]:
    return {
        "interval": {
            "startTime": f"2026-11-{day}T12:00:00Z",
            "endTime": f"2026-11-{day + 1}T12:00:00Z",
        },
        "displayDate": {"year": 2026, "month": 11, "day": day},
        "daytimeForecast": {
            "weatherCondition": condition(text, "RAIN"),
            "relativeHumidity": 70,
            "precipitation": chance(by_day),
            "wind": wind(12.4, "NORTH_NORTHWEST"),
        },
        "nighttimeForecast": {
            "weatherCondition": condition("Clear", "CLEAR"),
            "relativeHumidity": 80,
            "precipitation": chance(by_night),
            "wind": wind(5, "WEST"),
        },
        "maxTemperature": degrees(high),
        "minTemperature": degrees(low),
        "feelsLikeMaxTemperature": degrees(high - 3),
        "feelsLikeMinTemperature": degrees(low - 4),
    }


FIVE_DAYS: dict[str, Any] = {
    "forecastDays": [
        forecast_day(27, "Mostly sunny", 52.3, 41.7, 10, 35),
        forecast_day(28, "Light rain", 49.6, 40.2, 65, 20),
        forecast_day(29, "Cloudy", 45.0, 33.0, 15, 15),
        {"displayDate": {"year": 2026, "month": 11, "day": 30}},
        forecast_day(31, "Never a date", 40.0, 30.0, 0, 0),
    ],
    "timeZone": {"id": "America/New_York"},
}
ONE_DAY: dict[str, Any] = {"forecastDays": FIVE_DAYS["forecastDays"][:1]}


class Response:
    def __init__(self, status: int, data: object) -> None:
        self.status_code = status
        self.data = data

    def json(self) -> object:
        if self.data is None:
            raise ValueError("no JSON")
        return self.data


class Session:
    """Answers each `get` from a list, and keeps what was asked."""

    def __init__(self, *answers: Response | Exception) -> None:
        self.answers = list(answers)
        self.requests: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> Response:
        # A copy: the lookup empties its parameters after the call.
        self.requests.append(
            {"url": url, **kwargs, "params": dict(kwargs.get("params", {}))}
        )
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def weather(
    tmp_path: Path, *answers: Response | Exception
) -> tuple[GoogleWeather, Session]:
    session = Session(*answers)
    return GoogleWeather(models_root(tmp_path), session), session


# --- today, and the days ahead --------------------------------------------------


def test_today_is_now_with_todays_high_low_and_chance(tmp_path: Path) -> None:
    lookup, session = weather(tmp_path, Response(200, CURRENT), Response(200, ONE_DAY))
    assert lookup.today(ZIP, key()) == Forecast(
        place="10001",
        condition="Partly cloudy",
        temperature=48,
        feels_like=45,
        high=52,
        low=42,
        # The higher of the day's and the night's.
        precipitation=35,
        humidity=62,
        wind="8 mph NW",
    )
    now, ahead = session.requests
    assert now["url"] == "https://weather.googleapis.com/v1/currentConditions:lookup"
    assert now["params"] == {
        "key": KEY,
        "location.latitude": "40.750649",
        "location.longitude": "-73.997298",
        "unitsSystem": "IMPERIAL",
    }
    assert now["timeout"] == weather_api.TIMEOUT
    assert ahead["url"] == "https://weather.googleapis.com/v1/forecast/days:lookup"
    assert ahead["params"]["days"] == "1"
    assert ahead["params"]["key"] == KEY


def test_days_is_a_line_for_each_day_today_first(tmp_path: Path) -> None:
    lookup, session = weather(tmp_path, Response(200, FIVE_DAYS))
    forecast = lookup.days(ZIP, key())
    assert forecast.days == (
        Day("Friday", "Mostly sunny", 52, 42, 35),
        Day("Saturday", "Light rain", 50, 40, 65),
        Day("Sunday", "Cloudy", 45, 33, 15),
        # A day with nothing in it, and a date that is not one: no crash.
        Day("Monday", "Unknown", 0, 0, 0),
        Day("Unknown", "Never a date", 40, 30, 0),
    )
    # The rest is today's, as forecast.
    assert (forecast.place, forecast.condition) == ("10001", "Mostly sunny")
    assert (forecast.temperature, forecast.feels_like) == (52, 49)
    assert (forecast.high, forecast.low, forecast.precipitation) == (52, 42, 35)
    assert (forecast.humidity, forecast.wind) == (70, "12 mph NNW")
    (request,) = session.requests
    assert request["url"] == "https://weather.googleapis.com/v1/forecast/days:lookup"
    assert request["params"] == {
        "key": KEY,
        "location.latitude": "40.750649",
        "location.longitude": "-73.997298",
        "unitsSystem": "IMPERIAL",
        "days": "5",
        "pageSize": "5",
    }


def test_missing_fields_get_fallbacks(tmp_path: Path) -> None:
    lookup, _ = weather(
        tmp_path,
        Response(200, {"temperature": {"degrees": 10, "unit": "CELSIUS"}}),
        Response(200, {}),
    )
    assert lookup.today(ZIP, key()) == Forecast(
        place="10001",
        condition="Unknown",
        temperature=50,
        feels_like=50,
        high=50,
        low=50,
        precipitation=0,
        humidity=0,
        wind="calm",
    )


def test_without_todays_forecast_the_last_days_range_stands_in(tmp_path: Path) -> None:
    lookup, _ = weather(
        tmp_path, Response(200, CURRENT), Response(200, {"forecastDays": []})
    )
    forecast = lookup.today(ZIP, key())
    assert (forecast.high, forecast.low, forecast.precipitation) == (55, 39, 5)


def test_an_answer_that_cannot_be_read_is_one_line(tmp_path: Path) -> None:
    for answers, line in (
        (
            [Response(200, {"weatherCondition": {}}), Response(200, ONE_DAY)],
            "weather.googleapis.com answered without the temperature",
        ),
        (
            [Response(200, ["not", "an", "object"])],
            "weather.googleapis.com answered 200 with nothing to read",
        ),
        (
            [Response(200, None)],
            "weather.googleapis.com answered 200 with nothing to read",
        ),
    ):
        lookup = GoogleWeather(tmp_path, Session(*answers))
        with pytest.raises(WeatherError) as raised:
            lookup.today(ZIP, key())
        assert str(raised.value) == line
    lookup = GoogleWeather(tmp_path, Session(Response(200, {"forecastDays": "x"})))
    with pytest.raises(WeatherError) as raised:
        lookup.days(ZIP, key())
    assert str(raised.value) == (
        "weather.googleapis.com answered without a day of forecast"
    )


# --- the key is in no error -----------------------------------------------------


def test_an_http_error_is_one_line_without_the_key(tmp_path: Path) -> None:
    told = f"API key {KEY} not valid.\nPlease pass a valid\n  API key."
    lookup = GoogleWeather(
        tmp_path, Session(Response(400, {"error": {"code": 400, "message": told}}))
    )
    with pytest.raises(WeatherError) as raised:
        lookup.today(ZIP, key())
    assert str(raised.value) == (
        "weather.googleapis.com answered 400: "
        "API key [key] not valid. Please pass a valid API key."
    )


def test_an_error_with_no_body_is_still_one_line(tmp_path: Path) -> None:
    lookup = GoogleWeather(tmp_path, Session(Response(503, None)))
    with pytest.raises(WeatherError) as raised:
        lookup.days(ZIP, key())
    assert str(raised.value) == "weather.googleapis.com answered 503: no reason given"


def test_no_network_is_one_line_without_the_url_or_the_key(tmp_path: Path) -> None:
    # A transport error's text holds the URL, and the key is in its query.
    error = ConnectionError(
        f"Max retries exceeded with url: /v1/currentConditions:lookup?key={KEY}\n"
        "(Caused by NameResolutionError)"
    )
    lookup = GoogleWeather(tmp_path, Session(error))
    with pytest.raises(WeatherError) as raised:
        lookup.today(ZIP, key())
    assert str(raised.value) == "cannot reach weather.googleapis.com: ConnectionError"


def test_the_key_is_in_no_error_and_is_left_for_the_caller_to_clear(
    tmp_path: Path,
) -> None:
    failures: list[list[Response | Exception]] = [
        [Response(403, {"error": {"message": f"denied for key={KEY}"}})],
        [Response(429, {"error": f"quota of {KEY}"})],
        [Response(500, None)],
        [TimeoutError(f"https://weather.googleapis.com/v1/x?key={KEY}")],
        [Response(200, [KEY])],
        [Response(200, CURRENT), Response(404, {"error": {"message": KEY}})],
    ]
    for answers in failures:
        for kind in ("today", "days"):
            lookup = GoogleWeather(tmp_path, Session(*answers))
            given = key()
            with pytest.raises(WeatherError) as raised:
                getattr(lookup, kind)(ZIP, given)
            line = str(raised.value)
            assert KEY not in line
            assert "\n" not in line
            assert "?" not in line
            # The lookup does not clear the key: the agent does, after it.
            assert given == KEY.encode()


def test_no_key_and_a_key_that_is_not_text_send_nothing(tmp_path: Path) -> None:
    session = Session()
    lookup = GoogleWeather(tmp_path, session)
    with pytest.raises(WeatherError) as raised:
        lookup.today(ZIP, bytearray())
    assert str(raised.value) == "the weather lookup was given no key"
    with pytest.raises(WeatherError) as raised:
        lookup.days(ZIP, bytearray(b"\xff\xfe"))
    assert str(raised.value) == "the weather key is not text"
    assert session.requests == []


# --- one host -------------------------------------------------------------------


def test_another_host_is_refused_before_any_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = Session()
    lookup = GoogleWeather(tmp_path, session)
    for url in (
        "https://example.com/v1/currentConditions:lookup",
        "https://geocoding.googleapis.com/v1/geocode",
        "https://weather.googleapis.com.example.com/v1/currentConditions:lookup",
        "http://weather.googleapis.com/v1/currentConditions:lookup",
        "https://weather.googleapis.com/v1/currentConditions:lookup?key=other",
    ):
        with pytest.raises(WeatherError) as raised:
            lookup._get(url, 40.75, -73.99, key())
        assert str(raised.value).startswith("refused: ")
        assert str(raised.value).endswith(" is not Google's Weather API")
        assert KEY not in str(raised.value)
    # A lookup pointed elsewhere is refused in the same way.
    monkeypatch.setattr(weather_api, "CURRENT_URL", "https://example.com/current")
    with pytest.raises(WeatherError) as raised:
        lookup.today(ZIP, key())
    assert str(raised.value) == "refused: example.com is not Google's Weather API"
    assert session.requests == []


# --- the zip code's coordinates -------------------------------------------------


def test_locate_reads_the_latitude_and_longitude_of_a_zip_code(tmp_path: Path) -> None:
    assert locate("10001", tmp_path) == (40.750649, -73.997298)
    assert locate(" 00601 ", tmp_path) == (18.180621, -66.749931)


def test_a_zip_code_not_in_the_table_is_one_line_naming_it(tmp_path: Path) -> None:
    with pytest.raises(WeatherError) as raised:
        locate("99999", tmp_path)
    assert str(raised.value) == "zip code 99999 is not in models/geo/zcta.tsv"
    with pytest.raises(WeatherError) as raised:
        locate("", tmp_path)
    assert str(raised.value) == "no zip code was given to look up"
    # Nothing is sent for a zip code that has no coordinates.
    session = Session()
    with pytest.raises(WeatherError, match="zip code 99999 is not in"):
        GoogleWeather(tmp_path, session).today("99999", key())
    assert session.requests == []


def test_a_missing_table_is_one_line_naming_make_fetch_models(tmp_path: Path) -> None:
    with pytest.raises(WeatherError) as raised:
        locate("10001", tmp_path / "elsewhere")
    assert str(raised.value) == (
        "no zip code table at models/geo/zcta.tsv: run 'make fetch-models'"
    )


def test_a_table_of_another_shape_is_one_line(tmp_path: Path) -> None:
    (tmp_path / GEO_TABLE).write_text("zip\tlat\tlong\n10001\t40.7\t-73.9\n")
    with pytest.raises(WeatherError) as raised:
        locate("10001", tmp_path)
    assert str(raised.value) == (
        "models/geo/zcta.tsv is not the zip code table: "
        "delete it and run 'make fetch-models'"
    )
    (tmp_path / GEO_TABLE).write_text(
        "GEOID\tINTPTLAT\tINTPTLONG\n10001\tnorth\twest\n10002\n"
    )
    with pytest.raises(WeatherError, match="zip code 10001 is not in"):
        locate("10001", tmp_path)


# --- the agent's own cap --------------------------------------------------------


class Clock:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def control(
    tmp_path: Path, clock: Clock, daily: int = 100, minute: int = 10
) -> RateControl:
    return RateControl(daily, minute, tmp_path / "run" / "weather-calls.json", clock)


def test_the_eleventh_call_in_a_minute_is_refused_naming_the_minute_limit(
    tmp_path: Path,
) -> None:
    clock = Clock()
    rate = control(tmp_path, clock)
    for _ in range(10):
        assert rate.refusal() == ""
        rate.record()
        clock.now += 2
    assert rate.refusal() == (
        "the weather agent's minute limit of 10 calls is reached: "
        "it clears in 40 seconds"
    )
    # It clears when the oldest call of the minute is a minute old.
    clock.now += 39
    assert rate.refusal().endswith("it clears in 1 second")
    clock.now += 1
    assert rate.refusal() == ""


def test_the_daily_limit_is_refused_naming_it(tmp_path: Path) -> None:
    clock = Clock()
    rate = control(tmp_path, clock, daily=3)
    for _ in range(3):
        assert rate.refusal() == ""
        rate.record()
        clock.now += 600
    assert rate.refusal() == (
        "the weather agent's daily limit of 3 calls is reached: "
        "it clears in 23 hours 30 minutes"
    )
    clock.now += 23 * 3600 + 29 * 60
    assert rate.refusal().endswith("it clears in 1 minute")
    clock.now += 60
    assert rate.refusal() == ""
    assert "\n" not in control(tmp_path, Clock(clock.now - 60), daily=1).refusal()


def test_moments_older_than_a_day_are_pruned(tmp_path: Path) -> None:
    clock = Clock()
    rate = control(tmp_path, clock, daily=2)
    rate.record()
    clock.now += 3600
    rate.record()
    assert rate.refusal() != ""
    clock.now += 23 * 3600 + 1
    # The first call is a day old: it no longer counts, and is not kept.
    assert rate.refusal() == ""
    rate.record()
    kept = json.loads(rate.path.read_text())["calls"]
    assert kept == [clock.now - 23 * 3600 - 1, clock.now]


def test_the_file_survives_a_new_instance(tmp_path: Path) -> None:
    clock = Clock()
    first = control(tmp_path, clock, minute=2)
    first.record()
    first.record()
    # The directory was made, and nothing half written is left in it.
    assert [path.name for path in (tmp_path / "run").iterdir()] == [
        "weather-calls.json"
    ]
    again = control(tmp_path, clock, minute=2)
    assert again.refusal().startswith("the weather agent's minute limit of 2 calls")
    # A check alone records nothing.
    assert len(json.loads(again.path.read_text())["calls"]) == 2


def test_a_corrupt_or_missing_file_counts_as_no_calls(tmp_path: Path) -> None:
    clock = Clock()
    rate = control(tmp_path, clock, minute=1)
    assert rate.refusal() == ""
    rate.path.parent.mkdir(parents=True)
    for text in ("{not json", "[1, 2, 3]", '{"calls": "many"}', ""):
        rate.path.write_text(text)
        assert rate.refusal() == ""
    # What is not a moment is not counted, and the next call rewrites the file.
    rate.path.write_text(json.dumps({"calls": ["soon", None, True, clock.now + 5]}))
    assert rate.refusal() == ""
    rate.record()
    assert json.loads(rate.path.read_text()) == {"calls": [clock.now]}
    assert rate.refusal().startswith("the weather agent's minute limit of 1 call ")


def test_a_run_directory_that_cannot_be_written_still_counts(tmp_path: Path) -> None:
    blocked = tmp_path / "file"
    blocked.write_text("")
    rate = RateControl(100, 1, blocked / "weather-calls.json", Clock())
    rate.record()
    assert rate.refusal().startswith("the weather agent's minute limit of 1 call ")
