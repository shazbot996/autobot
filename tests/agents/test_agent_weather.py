"""The `weather` agent: the key from Secret Manager, its own rate, the lookup, two parts."""

from datetime import datetime
from pathlib import Path

from agents.agent_fakes import AgentRig, FakeLookup
from autobot.agents.registry import AGENTS
from autobot.agents.weather import (
    CALLS_FILE,
    EMPTY_SECRET,
    NO_CLOUD,
    NO_ZIP_CODE,
    NOT_CONFIRMED,
    Weather,
    days_response,
    today_response,
)
from autobot.agents.weather_api import Day, Forecast, WeatherError
from autobot.config import AGENT_WEATHER, Config
from autobot.gcp.client import GcpError, GoogleCloud
from autobot.tui.face import Seams
from gcp.gcp_fakes import FakeGoogleCloud

SECRET = "autobot-weather-api-key"
KEY = b"AIza-the-weather-key"
ZIP = '[[agents]]\nname = "weather"\nzip_code = "43215"\n'
TODAY = Forecast(
    place="43215",
    condition="Partly cloudy",
    temperature=54,
    feels_like=51,
    high=58,
    low=41,
    precipitation=20,
    humidity=60,
    wind="8 mph NW",
)
AHEAD = Forecast(
    place="43215",
    condition="Partly cloudy",
    temperature=54,
    feels_like=51,
    high=58,
    low=41,
    precipitation=20,
    humidity=60,
    wind="8 mph NW",
    days=(
        Day("Saturday", "Light rain", 58, 41, 60),
        Day("Sunday", "Sunny", 61, 40, 5),
        Day("Monday", "Cloudy", 55, 39, 20),
        Day("Tuesday", "Snow", 33, 25, 80),
    ),
)
NOON = datetime(2026, 11, 27, 12, 0, 0)


class Cloud:
    """The way to Google Cloud: a fake holding the key, or a refusal."""

    def __init__(self, key: bytes | None = KEY, refusal: str = "") -> None:
        self.cloud = FakeGoogleCloud()
        if key is not None:
            self.cloud.values[SECRET] = key
        self.refusal = refusal
        self.connects = 0

    def __call__(self, config: Config, repo: Path) -> GoogleCloud:
        self.connects += 1
        if self.refusal:
            raise GcpError(self.refusal)
        return self.cloud


def rig_with(
    tmp_path: Path,
    lookup: FakeLookup[Forecast],
    cloud: Cloud | None = None,
    extra: str = ZIP,
) -> AgentRig:
    seams = Seams(wall_clock=lambda: NOON, connect=cloud or Cloud(), weather=lookup)
    return AgentRig(tmp_path, extra=extra, seams=seams)


def test_today_is_one_lookup_and_one_spoken_sentence(tmp_path: Path) -> None:
    lookup = FakeLookup(TODAY)
    cloud = Cloud()
    rig = rig_with(tmp_path, lookup, cloud)
    agent = rig.run(AGENT_WEATHER, "what's the weather")
    assert isinstance(agent, Weather)
    rig.finish(agent)
    assert lookup.calls == [("today", "43215")]
    said = (
        "It's 54 degrees and partly cloudy, with a high of 58, a low of 41 "
        "and a 20 percent chance of rain."
    )
    assert rig.summary == ["Getting the weather for 43215.", said]
    assert rig.voice.said == [said]
    assert said.count(".") == 1
    assert rig.detail == [
        "Weather for 43215",
        "Now       Partly cloudy, 54°F (feels like 51°F)",
        "Today     high 58°F, low 41°F",
        "Rain      20% chance",
        "Humidity  60%",
        "Wind      8 mph NW",
    ]
    assert rig.troubles == []
    # The key was read once, as the service account, by its secret's name.
    assert cloud.connects == 1
    assert cloud.cloud.made("secret_value") == [("secret_value", SECRET)]


def test_the_key_is_cleared_after_the_call(tmp_path: Path) -> None:
    cloud = Cloud()
    rig = rig_with(tmp_path, FakeLookup(TODAY), cloud)
    rig.finish(rig.run(AGENT_WEATHER))
    assert cloud.cloud.given == [bytearray(len(KEY))]
    # It is in no file under the repository, and on no panel.
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert KEY not in path.read_bytes(), path
    assert KEY.decode() not in "\n".join(rig.summary + rig.detail)


def test_the_forecast_mode_gives_the_days_ahead_a_line_a_day(tmp_path: Path) -> None:
    lookup = FakeLookup(AHEAD)
    rig = rig_with(tmp_path, lookup)
    rig.finish(rig.run(AGENT_WEATHER, "forecast", mode="forecast"))
    assert lookup.calls == [("days", "43215")]
    assert rig.summary[0] == "Getting the forecast for 43215."
    assert rig.summary[1] == (
        "Here is the forecast. Saturday, light rain with a high of 58; Sunday, "
        "sunny with a high of 61; Monday, cloudy with a high of 55."
    )
    assert rig.detail[0] == "Forecast for 43215"
    assert len(rig.detail) == 5
    assert rig.detail[1].startswith("Saturday") and "Light rain" in rig.detail[1]
    assert rig.detail[4].startswith("Tuesday ") and "80% rain" in rig.detail[4]


def test_no_zip_code_is_one_line_and_nothing_is_read_or_looked_up(
    tmp_path: Path,
) -> None:
    lookup = FakeLookup(TODAY)
    cloud = Cloud()
    rig = rig_with(tmp_path, lookup, cloud, extra="")
    assert rig.run(AGENT_WEATHER).done
    assert rig.troubles == [NO_ZIP_CODE]
    assert rig.summary == [NO_ZIP_CODE]
    assert lookup.calls == [] and cloud.connects == 0


def test_no_key_file_is_one_line_without_a_lookup(tmp_path: Path) -> None:
    lookup = FakeLookup(TODAY)
    refusal = "refused: no key at secrets/service-account-autobot-agent.json yet"
    rig = rig_with(tmp_path, lookup, Cloud(refusal=refusal))
    agent = rig.run(AGENT_WEATHER)
    rig.finish(agent)
    assert rig.troubles == [refusal]
    assert lookup.calls == []


def test_a_secret_with_no_value_is_one_line_without_a_lookup(tmp_path: Path) -> None:
    lookup = FakeLookup(TODAY)
    rig = rig_with(tmp_path, lookup, Cloud(key=None))
    rig.finish(rig.run(AGENT_WEATHER))
    assert rig.troubles == [f"secret {SECRET} has no enabled version (HTTP 404)"]
    assert lookup.calls == []


def test_an_empty_secret_is_one_line_without_a_lookup(tmp_path: Path) -> None:
    lookup = FakeLookup(TODAY)
    rig = rig_with(tmp_path, lookup, Cloud(key=b"  \n"))
    rig.finish(rig.run(AGENT_WEATHER))
    assert rig.troubles == [EMPTY_SECRET.format(secret=SECRET)]
    assert lookup.calls == []


def test_a_lookup_that_fails_is_one_line_and_the_key_is_still_cleared(
    tmp_path: Path,
) -> None:
    lookup = FakeLookup(
        TODAY, error=WeatherError("weather.googleapis.com answered 403")
    )
    cloud = Cloud()
    rig = rig_with(tmp_path, lookup, cloud)
    rig.finish(rig.run(AGENT_WEATHER))
    assert rig.troubles == ["weather.googleapis.com answered 403"]
    assert cloud.cloud.given == [bytearray(len(KEY))]
    assert rig.voice.said == []


def test_the_eleventh_call_in_a_minute_is_refused_by_the_agents_own_rate(
    tmp_path: Path,
) -> None:
    lookup = FakeLookup(AHEAD)
    rig = rig_with(tmp_path, lookup)
    # The days ahead are one call to the API each.
    for _ in range(10):
        rig.finish(rig.run(AGENT_WEATHER, "forecast", mode="forecast"))
    assert len(lookup.calls) == 10
    assert (tmp_path / CALLS_FILE).is_file()
    rig.finish(rig.run(AGENT_WEATHER, "forecast", mode="forecast"))
    assert len(lookup.calls) == 10
    assert len(rig.troubles) == 1
    assert rig.troubles[0].startswith("the weather agent's minute limit of 10 ")
    assert "\n" not in rig.troubles[0]


def test_todays_weather_counts_as_the_two_calls_it_makes(tmp_path: Path) -> None:
    lookup = FakeLookup(TODAY)
    rig = rig_with(tmp_path, lookup)
    for _ in range(5):
        rig.finish(rig.run(AGENT_WEATHER))
    assert len(lookup.calls) == 5
    rig.finish(rig.run(AGENT_WEATHER))
    assert len(lookup.calls) == 5
    assert rig.troubles[0].startswith("the weather agent's minute limit of 10 ")


def test_the_limits_are_the_definitions(tmp_path: Path) -> None:
    lookup = FakeLookup(AHEAD)
    rig = rig_with(tmp_path, lookup, extra=ZIP + "minute_limit = 2\n")
    for _ in range(3):
        rig.finish(rig.run(AGENT_WEATHER, "forecast", mode="forecast"))
    assert len(lookup.calls) == 2
    assert rig.troubles[0].startswith("the weather agent's minute limit of 2 ")


def test_with_the_request_confirmation_on_it_asks_first_and_no_skips_the_lookup(
    tmp_path: Path,
) -> None:
    lookup = FakeLookup(TODAY)
    cloud = Cloud()
    extra = ZIP + 'confirm_request = true\nconfirm_request_question = "Look it up?"\n'
    rig = rig_with(tmp_path, lookup, cloud, extra=extra)
    agent = rig.run(AGENT_WEATHER)
    rig.frames(2.0)
    assert rig.voice.said == ["Look it up?"]
    assert rig.face.confirming and lookup.calls == []
    rig.hear("nah")
    rig.finish(agent)
    assert lookup.calls == []
    assert rig.summary[-1] == NOT_CONFIRMED
    assert rig.troubles == []
    # The key that was read is cleared all the same.
    assert cloud.cloud.given == [bytearray(len(KEY))]
    # Nothing was counted against the rate.
    assert not (tmp_path / CALLS_FILE).exists()


def test_a_yes_lets_the_lookup_go(tmp_path: Path) -> None:
    lookup = FakeLookup(TODAY)
    extra = ZIP + "confirm_request = true\n"
    rig = rig_with(tmp_path, lookup, extra=extra)
    agent = rig.run(AGENT_WEATHER)
    rig.frames(2.0)
    rig.hear("go ahead")
    rig.finish(agent)
    assert lookup.calls == [("today", "43215")]


def test_cancel_clears_the_key(tmp_path: Path) -> None:
    cloud = Cloud()
    extra = ZIP + "confirm_request = true\n"
    rig = rig_with(tmp_path, FakeLookup(TODAY), cloud, extra=extra)
    agent = rig.run(AGENT_WEATHER)
    assert isinstance(agent, Weather)
    agent.cancel()
    assert agent.done
    assert cloud.cloud.given == [bytearray(len(KEY))]


def test_without_a_way_to_google_cloud_it_says_so(tmp_path: Path) -> None:
    rig = AgentRig(tmp_path, extra=ZIP, seams=Seams())
    assert rig.run(AGENT_WEATHER).done
    assert rig.troubles == [NO_CLOUD]


def test_the_weather_agent_sends_requests_and_uses_no_model() -> None:
    agent = AGENTS[AGENT_WEATHER]
    assert agent.sends_requests and not agent.uses_model
    assert agent.settings == ("zip_code", "daily_limit", "minute_limit", "secret")


def test_no_days_is_said_plainly() -> None:
    assert days_response(TODAY).summary == "I have no forecast for the days ahead."
    assert today_response(TODAY).detail[0] == "Weather for 43215"
