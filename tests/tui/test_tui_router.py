"""`autobot.tui.router`: the wake wakes autobot, and a command routes to an agent."""

import dataclasses
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tui_fakes import CONFIG_TEXT, FakeAudio, FakeRunner, FakeVoice

from autobot.agents.base import Launch
from autobot.agents.registry import AGENTS
from autobot.config import parse_config
from autobot.tui.echo import Echo
from autobot.tui.face import Experience, FaceContext, FaceModel
from autobot.tui.face_reactions import ReactionSet, reaction_set
from autobot.tui.router import (
    CANCELLED,
    LISTEN_SECONDS,
    LISTENING,
    NO_ANSWER,
    NO_MATCH,
    NO_RULES,
    NO_WORDS,
    NOT_CONFIRMED,
    NOTHING_HEARD,
    ROUTED,
    WAITING,
    Router,
)
from autobot.tui.runner import FunctionTask
from autobot.tui.tree_game import Position

AT = "2026-11-27T07:00:00.000+00:00"
FRAME = 0.1
# The one rule most tests route by: "echo" runs the echo.
ECHO_ONLY = """
[[routes]]
command = "echo"
agent = "echo"
"""
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


class FakeAgentDef:
    """An agent, in the registry's shape, whose experience is a test's fake."""

    def __init__(self, name: str, make: Callable[[], Experience]) -> None:
        self.name = name
        self.about = "a test's agent"
        self.uses_model = False
        self.sends_requests = False
        self.settings: tuple[str, ...] = ()
        self._make = make
        self.launches: list[Launch] = []

    def make(self, launch: Launch) -> Experience:
        self.launches.append(launch)
        return self._make()


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
        config_text: str = CONFIG_TEXT + ECHO_ONLY,
        reactions: ReactionSet | None = None,
    ) -> None:
        self.clock = FakeClock()
        self.agents: list[FakeAgent] = []
        self.routers: list[Router] = []

        def agent() -> FakeAgent:
            made = FakeAgent(reactions)
            self.agents.append(made)
            return made

        self.echo = FakeAgentDef("echo", agent)

        def router() -> Router:
            made = Router({"echo": self.echo})
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


def test_the_echo_agent_runs_the_echo(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    launch = Launch(rig.model.context.config.agent("echo"), "", "echo")
    assert isinstance(AGENTS["echo"].make(launch), Echo)


def test_the_agent_is_launched_with_its_definition_and_the_transcript(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)
    rig.model.feed(woke("hey autobot", 1))
    rig.model.feed([transcript("please echo that", 2), score(2)])
    rig.model.tick()
    assert rig.echo.launches == [
        Launch(rig.model.context.config.agent("echo"), "", "please echo that")
    ]


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


# --- the session: levels, keywords, confirmations, listening --------------------

SESSION_ROUTES = """
[[routes]]
command = "weather"
agent = "weather"

[[routes]]
command = "forecast"
agent = "weather"
mode = "forecast"

[[routes]]
command = "time"
agent = "time"
"""
QUESTION = "Shall I look that up?"
# Long enough for the fake voice's clip to be spoken: nothing is heard over it.
ASKED = 5.0
CONFIRM_ENTRY = f"""
[[agents]]
name = "weather"
confirm_entry = true
confirm_entry_question = "{QUESTION}"
"""
NOT_LISTENING = """
[[agents]]
name = "weather"
listening = false
"""


class SlowAgent(FakeAgent):
    """A fake agent that runs for a while and notes a `cancel`."""

    def __init__(self) -> None:
        super().__init__(frames=10_000)
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class SessionRig:
    """The router on fakes with a fake `weather` and `time` agent, both slow by default."""

    def __init__(self, tmp_path: Path, extra: str = "", *, slow: bool = True) -> None:
        self.clock = FakeClock()
        self.agents: list[FakeAgent] = []
        self.routers: list[Router] = []
        self.voice = FakeVoice()

        def agent() -> FakeAgent:
            made = SlowAgent() if slow else FakeAgent()
            self.agents.append(made)
            return made

        self.defs = {name: FakeAgentDef(name, agent) for name in ("weather", "time")}

        def router() -> Router:
            made = Router(self.defs)
            self.routers.append(made)
            return made

        self.model = FaceModel(
            parse_config(tomllib.loads(CONFIG_TEXT + SESSION_ROUTES + extra)),
            experience=router,
            audio=FakeAudio(),
            runner=FakeRunner(),
            render=self.voice,
            spawn=lambda function: FunctionTask(function, inline=True),
            repo=tmp_path,
            models_root=tmp_path,
            hostname="nuc",
            clock=self.clock,
        )
        self.stretch = 0

    @property
    def router(self) -> Router:
        return self.routers[-1]

    @property
    def summary(self) -> list[str]:
        return self.model.summary.texts

    @property
    def detail(self) -> list[str]:
        return list(self.model.detail.lines)

    @property
    def place(self) -> tuple[str, str, str]:
        position = self.model.position
        return (position.level, position.node, position.phase)

    def wake(self, text: str = "hey autobot") -> None:
        self.stretch += 1
        self.model.feed(woke(text, self.stretch))

    def say(self, text: str) -> None:
        self.stretch += 1
        self.model.feed([transcript(text, self.stretch), score(self.stretch)])
        self.frames(FRAME)

    def frames(self, seconds: float) -> None:
        for _ in range(round(seconds / FRAME)):
            self.clock.now += FRAME
            self.model.tick()

    def spoken(self) -> list[str]:
        return [text for text, _, _ in self.voice.calls]


def test_cancel_at_the_agents_level_ends_the_session(tmp_path: Path) -> None:
    rig = SessionRig(tmp_path)
    rig.wake()
    assert rig.place == ("agents", "", "wake")
    rig.say("please cancel that")
    assert rig.summary[-1] == CANCELLED
    assert rig.detail[-1] == 'heard "please cancel that": the keyword cancel'
    assert not rig.model.running
    assert rig.agents == []
    assert rig.place == ("root", "", "listening")
    assert rig.routers[0].position.phase == "done"


def test_a_word_that_only_starts_like_cancel_is_not_the_keyword(tmp_path: Path) -> None:
    rig = SessionRig(tmp_path)
    rig.wake()
    rig.say("no cancellation please")
    assert rig.model.running
    assert CANCELLED not in rig.summary


def test_cancel_during_an_agent_ends_it_and_listens_again(tmp_path: Path) -> None:
    rig = SessionRig(tmp_path)
    rig.wake()
    rig.say("weather")
    agent = rig.agents[0]
    assert isinstance(agent, SlowAgent) and agent.started
    assert rig.place == ("agents", "weather", "running")
    rig.frames(1.0)
    rig.say("okay, cancel")
    assert agent.cancelled
    assert rig.router.agent is None
    assert rig.place == ("agents", "", "wake")
    listening = LISTENING.format(commands='"weather", "forecast" or "time"')
    assert rig.summary[-2:] == [CANCELLED, listening]
    assert rig.summary.count(listening) == 2
    ticks = agent.ticks
    rig.frames(0.5)
    # The cancelled agent gets no more frames.
    assert agent.ticks == ticks
    # A command still routes, with its ten seconds started again.
    rig.frames(LISTEN_SECONDS - 1.0)
    rig.say("what time is it")
    assert len(rig.agents) == 2
    assert rig.place == ("agents", "time", "running")
    assert rig.model.running


def test_an_agent_without_cancel_is_dropped_all_the_same(tmp_path: Path) -> None:
    rig = SessionRig(tmp_path)
    plain = FakeAgent(frames=10_000)
    rig.defs["time"] = FakeAgentDef("time", lambda: plain)
    rig.wake()
    rig.say("time")
    rig.say("cancel")
    assert rig.router.agent is None
    assert rig.summary[-2] == CANCELLED


def test_an_agent_that_is_not_listening_does_not_hear_cancel(tmp_path: Path) -> None:
    rig = SessionRig(tmp_path, NOT_LISTENING)
    rig.wake()
    rig.say("weather")
    agent = rig.agents[0]
    rig.say("cancel")
    rig.frames(1.0)
    assert isinstance(agent, SlowAgent) and not agent.cancelled
    assert rig.router.agent is agent
    assert CANCELLED not in rig.summary
    # What was heard was dropped unread.
    assert not rig.model.context.heard
    # The setting is the agent's own: the time agent still listens.
    assert rig.model.context.config.agent("time").listening


def test_a_confirmation_on_the_way_in_starts_the_agent_only_on_yes(
    tmp_path: Path,
) -> None:
    rig = SessionRig(tmp_path, CONFIRM_ENTRY)
    rig.wake()
    rig.say("weather")
    # The question is asked at the node, and the agent has not started.
    assert rig.place == ("agents", "weather", "confirming")
    assert rig.spoken() == [QUESTION]
    assert QUESTION in rig.summary
    assert rig.agents == []
    rig.frames(ASKED)
    rig.say("you betcha")
    assert len(rig.agents) == 1 and rig.agents[0].started
    assert rig.place == ("agents", "weather", "running")
    assert rig.summary[-2] == ROUTED.format(agent="weather")
    # It was asked once.
    assert rig.spoken() == [QUESTION]


def test_a_no_returns_to_the_agents_level(tmp_path: Path) -> None:
    rig = SessionRig(tmp_path, CONFIRM_ENTRY)
    rig.wake()
    rig.say("weather")
    rig.frames(ASKED)
    rig.say("nope")
    assert rig.agents == []
    assert rig.place == ("agents", "", "wake")
    assert rig.summary[-2] == NOT_CONFIRMED.format(agent="weather")
    assert rig.summary[-1].startswith("Listening for a command")
    # The time agent asks nothing: it starts at once.
    rig.say("time")
    assert len(rig.agents) == 1
    assert rig.place == ("agents", "time", "running")


def test_ten_seconds_without_an_answer_returns_to_the_agents_level(
    tmp_path: Path,
) -> None:
    rig = SessionRig(tmp_path, CONFIRM_ENTRY)
    rig.wake()
    rig.say("weather")
    rig.frames(ASKED)
    rig.say("hmm, let me think")
    assert rig.place == ("agents", "weather", "confirming")
    rig.frames(LISTEN_SECONDS + 3.0)
    assert rig.agents == []
    assert rig.place == ("agents", "", "wake")
    assert NO_ANSWER.format(agent="weather") in rig.summary
    assert rig.model.running
    # And ten more seconds with no command end the session.
    rig.frames(LISTEN_SECONDS + 0.5)
    assert not rig.model.running
    assert rig.summary[-1] == NOTHING_HEARD


def test_cancel_answers_a_confirmation_no(tmp_path: Path) -> None:
    rig = SessionRig(tmp_path, CONFIRM_ENTRY)
    rig.wake()
    rig.say("weather")
    rig.frames(ASKED)
    rig.say("cancel")
    assert rig.agents == []
    assert rig.place == ("agents", "", "wake")
    assert rig.model.running


def test_the_position_follows_one_session_from_the_root_and_back(
    tmp_path: Path,
) -> None:
    rig = SessionRig(tmp_path, slow=False)
    seen = [rig.place]
    rig.wake()
    seen.append(rig.place)
    rig.say("give me the forecast")
    seen.append(rig.place)
    rig.frames(1.0)
    seen.append(rig.place)
    assert seen == [
        ("root", "", "listening"),
        ("agents", "", "wake"),
        ("agents", "weather", "running"),
        ("root", "", "listening"),
    ]
    # The router's own last position is the root, done.
    assert rig.routers[0].position == Position("root", "", "done")
    # The mode of the rule reached the agent.
    launch = rig.defs["weather"].launches[0]
    assert launch.mode == "forecast"
    assert launch.transcript == "give me the forecast"
    assert 'rule 2: "forecast" -> weather agent (forecast)' in rig.detail[1]


def test_an_agent_asking_before_its_request_is_heard_though_not_listening(
    tmp_path: Path,
) -> None:
    class Asks(FakeAgent):
        def __init__(self) -> None:
            super().__init__(frames=10_000)
            self.outcome: str | None = None
            self.confirmation: Any = None

        def start(self, face: FaceContext) -> None:
            self.started = True
            self.confirmation = face.confirm("This may cost money. Go on?")

        def tick(self, face: FaceContext) -> None:
            if self.confirmation.done:
                self.outcome = self.confirmation.outcome
                self.done = True
            else:
                self.confirmation.tick(face)

    rig = SessionRig(tmp_path, NOT_LISTENING)
    asks = Asks()
    rig.defs["weather"] = FakeAgentDef("weather", lambda: asks)
    rig.wake()
    rig.say("weather")
    rig.frames(ASKED)
    assert rig.model.context.confirming
    rig.say("sure")
    rig.frames(0.5)
    assert asks.outcome == "yes"
    assert not rig.model.running
