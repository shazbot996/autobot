"""`autobot.tui.router`: the wake wakes autobot, and a command routes to an agent."""

import dataclasses
import tomllib
from pathlib import Path
from typing import Any

from tui_fakes import CONFIG_TEXT, FakeAudio, FakeRunner, FakeVoice

from autobot.config import parse_config
from autobot.tui.echo import Echo
from autobot.tui.face import FaceContext, FaceModel
from autobot.tui.face_reactions import ReactionSet, reaction_set
from autobot.tui.router import (
    AGENTS,
    LISTEN_SECONDS,
    LISTENING,
    NO_MATCH,
    NO_RULES,
    NO_WORDS,
    NOTHING_HEARD,
    ROUTED,
    WAITING,
    Router,
)
from autobot.tui.runner import FunctionTask

AT = "2026-11-27T07:00:00.000+00:00"
FRAME = 0.1
ROUTES = """
[[routes]]
command = "make a list"
agent = "echo"

[[routes]]
command = "echo"
agent = "echo"
"""


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class FakeAgent:
    """An agent's experience that says one line and ends after `frames` frames."""

    def __init__(self, reactions: ReactionSet | None = None, frames: int = 3):
        self.reactions = reactions
        self.frames = frames
        self.done = False
        self.started = False
        self.ticks = 0

    def start(self, face: FaceContext) -> None:
        self.started = True
        face.summary.add("agent started")

    def tick(self, face: FaceContext) -> None:
        self.ticks += 1
        if self.ticks >= self.frames:
            face.summary.add("agent finished")
            self.done = True


def transcript(text: str, stretch: int) -> dict[str, Any]:
    return {
        "event": "transcript",
        "phrase": "autobot",
        "kind": "text_match",
        "text": text,
        "stretch": stretch,
        "at": AT,
    }


def score(stretch: int, kind: str = "text_match") -> dict[str, Any]:
    return {
        "event": "score",
        "phrase": "autobot",
        "kind": kind,
        "score": 1.0,
        "stretch": stretch,
        "at": AT,
    }


def wake(kind: str = "text_match") -> dict[str, Any]:
    return {
        "event": "wake",
        "phrase": "autobot",
        "target": "ack",
        "kind": kind,
        "score": 1.0,
        "at": AT,
    }


def woke(text: str, stretch: int) -> list[dict[str, Any]]:
    """What the listener publishes for a stretch that woke it."""
    return [transcript(text, stretch), score(stretch), wake()]


class Rig:
    """`FaceModel` running the router on fakes, with the agents it started."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        config_text: str = CONFIG_TEXT,
        reactions: ReactionSet | None = None,
    ) -> None:
        self.clock = FakeClock()
        self.agents: list[FakeAgent] = []
        self.routers: list[Router] = []

        def agent() -> FakeAgent:
            made = FakeAgent(reactions)
            self.agents.append(made)
            return made

        def router() -> Router:
            made = Router({"echo": agent})
            self.routers.append(made)
            return made

        self.model = FaceModel(
            parse_config(tomllib.loads(config_text)),
            experience=router,
            audio=FakeAudio(),
            runner=FakeRunner(),
            render=FakeVoice(),
            spawn=lambda function: FunctionTask(function, inline=True),
            repo=tmp_path,
            models_root=tmp_path,
            hostname="nuc",
            clock=self.clock,
        )

    @property
    def router(self) -> Router:
        return self.routers[-1]

    @property
    def summary(self) -> list[str]:
        return self.model.summary.texts

    @property
    def detail(self) -> list[str]:
        return list(self.model.detail.lines)

    def frames(self, seconds: float) -> None:
        for _ in range(round(seconds / FRAME)):
            self.clock.now += FRAME
            self.model.tick()


def test_the_echo_agent_runs_the_echo() -> None:
    assert AGENTS["echo"] is Echo


def test_a_wake_alone_listens_for_a_command_and_starts_no_agent(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)
    rig.model.feed(woke("hey autobot", 1))
    assert rig.model.eyes.name == "listening"
    rig.frames(2.0)
    assert rig.model.running
    assert rig.agents == []
    assert rig.summary == [LISTENING.format(commands='"echo"')]
    assert rig.detail == [WAITING.format(seconds=LISTEN_SECONDS)]


def test_a_command_among_other_words_starts_its_agent(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.model.feed(woke("hey autobot", 1))
    rig.frames(1.0)
    rig.model.feed([transcript("Could you echo my voice?", 2), score(2)])
    rig.model.tick()
    assert len(rig.agents) == 1 and rig.agents[0].started
    assert rig.router.match is not None
    assert rig.router.match.number == 1
    assert ROUTED.format(agent="echo") in rig.summary
    assert rig.detail[-1] == (
        'rule 1: "echo" -> echo agent, heard "Could you echo my voice?"'
    )
    # The router passes the frames to the agent; the session ends with it.
    rig.frames(1.0)
    assert rig.summary[-1] == "agent finished"
    assert not rig.model.running


def test_a_command_in_the_waking_transcript_routes_at_once(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.model.feed(woke("Hey autobot, echo this.", 1))
    assert len(rig.agents) == 1 and rig.agents[0].started
    assert rig.detail[-1] == 'rule 1: "echo" -> echo agent, heard "echo this."'


def test_only_the_words_after_the_wake_phrase_count(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.model.feed(woke("echo, autobot", 1))
    rig.frames(0.5)
    assert rig.agents == []


def test_what_was_said_before_the_wake_does_not_count(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.model.feed([transcript("echo echo", 1), score(1)])
    rig.model.feed(woke("hey autobot", 2))
    rig.frames(0.5)
    assert rig.agents == []


def test_words_no_rule_matches_are_a_detail_line(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.model.feed(woke("hey autobot", 1))
    rig.model.feed([transcript("what time is it", 2), score(2)])
    rig.model.tick()
    assert rig.detail[-1] == NO_MATCH.format(text="what time is it")
    assert rig.model.running and rig.agents == []
    # It keeps listening: a later command still routes.
    rig.model.feed([transcript("echo", 3), score(3)])
    rig.model.tick()
    assert len(rig.agents) == 1


def test_the_first_rule_that_matches_wins(tmp_path: Path) -> None:
    rig = Rig(tmp_path, config_text=CONFIG_TEXT + ROUTES)
    rig.model.feed(woke("hey autobot", 1))
    assert rig.summary == [LISTENING.format(commands='"make a list" or "echo"')]
    rig.model.feed([transcript("echo it and make a list", 2), score(2)])
    rig.model.tick()
    assert rig.router.match is not None
    assert rig.router.match.number == 1


def test_no_command_in_time_ends_the_session(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.model.feed(woke("hey autobot", 1))
    rig.frames(LISTEN_SECONDS - 1.0)
    assert rig.model.running
    rig.frames(1.5)
    assert not rig.model.running
    assert rig.summary[-1] == NOTHING_HEARD
    assert rig.model.eyes.name == "done"
    # A command now is too late: it needs a new wake.
    rig.model.feed([transcript("echo", 2), score(2)])
    rig.frames(0.5)
    assert rig.agents == []
    rig.model.feed(woke("autobot echo", 3))
    assert len(rig.agents) == 1


def test_a_second_wake_while_listening_can_carry_the_command(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.model.feed(woke("hey autobot", 1))
    rig.frames(1.0)
    rig.model.feed(woke("autobot echo", 2))
    rig.model.tick()
    assert len(rig.routers) == 1
    assert len(rig.agents) == 1


def test_nothing_is_heard_while_the_face_holds_the_listener(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.model.feed(woke("hey autobot", 1))
    rig.model.ticker.muted = True
    rig.model.feed([transcript("echo", 2), score(2)])
    rig.model.tick()
    assert rig.agents == []
    assert list(rig.model.context.heard) == []


def test_the_agents_own_reactions_play_once_it_runs(tmp_path: Path) -> None:
    own = reaction_set({"wake": {"poses": [{"pupil": "up left", "hold": 400}]}})
    rig = Rig(tmp_path, reactions=own)
    rig.model.feed(woke("hey autobot", 1))
    assert rig.model.context.reactions is None
    rig.model.feed([transcript("echo", 2), score(2)])
    rig.model.tick()
    assert rig.router.reactions is own
    assert rig.model.context.reactions is own


def test_without_a_text_match_phrase_no_command_can_be_heard(tmp_path: Path) -> None:
    text = CONFIG_TEXT.replace('kind = "text_match"', 'kind = "sherpa_kws"')
    rig = Rig(tmp_path, config_text=text)
    rig.model.feed([score(1, "sherpa_kws"), wake("sherpa_kws")])
    assert not rig.model.running
    assert rig.model.summary.lines[-1].text == NO_WORDS
    assert rig.model.summary.lines[-1].trouble
    assert rig.model.eyes.name == "trouble"


def test_a_rule_for_an_agent_not_built_is_trouble(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    router = Router({})
    face = rig.model.context
    face.heard.append("echo")
    router.start(face)
    assert router.done
    assert face.summary.lines[-1].text == "the echo agent is not built"
    assert face.summary.lines[-1].trouble


def test_no_rules_is_trouble(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    router = Router()
    face = rig.model.context
    face.config = dataclasses.replace(face.config, routes=())
    router.start(face)
    assert router.done
    assert face.summary.lines[-1].text == NO_RULES
