"""The `time` agent: the time from this machine's clock, in two parts."""

from datetime import datetime
from pathlib import Path

import pytest

from agents.agent_fakes import AgentRig
from autobot.agents.registry import AGENTS
from autobot.agents.time_of_day import TimeOfDay, part_of_day, spoken_time
from autobot.config import AGENT_TIME
from autobot.tui.face import Seams


def at(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 11, 27, hour, minute, second)


@pytest.mark.parametrize(
    ("moment", "said"),
    [
        (at(15, 42), "It's 3:42 in the afternoon."),
        (at(9, 5), "It's 9:05 in the morning."),
        (at(19, 30), "It's 7:30 in the evening."),
        (at(23, 59), "It's 11:59 at night."),
        (at(0, 15), "It's 12:15 at night."),
        (at(8, 0), "It's 8 o'clock in the morning."),
        (at(12, 0), "It's noon."),
        (at(0, 0), "It's midnight."),
    ],
)
def test_the_time_is_said_the_way_a_person_says_it(moment: datetime, said: str) -> None:
    assert spoken_time(moment) == said


def test_the_day_has_four_parts() -> None:
    assert [part_of_day(hour) for hour in (4, 5, 11, 12, 17, 18, 20, 21)] == [
        "at night",
        "in the morning",
        "in the morning",
        "in the afternoon",
        "in the afternoon",
        "in the evening",
        "in the evening",
        "at night",
    ]


def test_a_fixed_clock_of_15_42_is_said_and_shown(tmp_path: Path) -> None:
    rig = AgentRig(tmp_path, seams=Seams(wall_clock=lambda: at(15, 42, 7)))
    agent = rig.run(AGENT_TIME)
    assert isinstance(agent, TimeOfDay)
    rig.finish(agent)
    assert rig.summary == ["It's 3:42 in the afternoon."]
    assert rig.voice.said == ["It's 3:42 in the afternoon."]
    # The detail: the date, the time to the second, the time zone.
    assert rig.detail[0] == "Friday, November 27, 2026"
    assert rig.detail[1] == "15:42:07"
    assert rig.detail[2].startswith("Time zone: ")
    assert rig.troubles == []
    # The listener held its ears while the line was spoken.
    assert any("hold" in call for call in rig.runner.calls)
    assert len(rig.audio.played) == 1


def test_the_time_agent_is_local(tmp_path: Path) -> None:
    agent = AGENTS[AGENT_TIME]
    assert not agent.uses_model and not agent.sends_requests
    # It runs with no way to Google Cloud, no lookup and no model at all.
    rig = AgentRig(tmp_path, seams=Seams(wall_clock=lambda: at(8, 0)))
    experience = rig.run(AGENT_TIME)
    rig.finish(experience)
    assert rig.summary == ["It's 8 o'clock in the morning."]


def test_a_voice_that_cannot_render_still_shows_the_time(tmp_path: Path) -> None:
    rig = AgentRig(tmp_path, seams=Seams(wall_clock=lambda: at(15, 42)))
    rig.voice.error = RuntimeError("missing model file: models/piper/joe.onnx")
    agent = rig.run(AGENT_TIME)
    rig.finish(agent)
    assert rig.summary == ["It's 3:42 in the afternoon."]
    assert rig.detail[-1] == "voice: missing model file: models/piper/joe.onnx"
    assert rig.audio.played == []
