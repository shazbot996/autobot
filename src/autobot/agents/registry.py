"""The agents autobot has, by name (AG-1). No drawing.

`AGENTS` is what the router hands a command to: one `Agent` per name of
`autobot.config.ROUTE_AGENTS`. Each entry says what the agent's code does
(`about`, for the Agent editor, AG-10), whether a language model stands
behind it, whether it sends a request off the machine, and which definition
keys it owns. Its code is in its own module, imported when the agent is
first made, so naming an agent loads nothing: not the ADK, not a lookup.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from autobot.agents.base import Agent, Launch
from autobot.config import (
    AGENT_CAMERA,
    AGENT_CORE,
    AGENT_ECHO,
    AGENT_INTRODUCE,
    AGENT_QUESTION,
    AGENT_SETTINGS,
    AGENT_TIME,
    AGENT_WEATHER,
)
from autobot.tui.face import Experience


@dataclass(frozen=True)
class BuiltAgent:
    """One agent: what the editor shows of its code, and how its experience is made."""

    name: str
    about: str
    uses_model: bool
    sends_requests: bool
    # Makes the experience; it imports the agent's module when first called.
    factory: Callable[[Launch], Experience]

    @property
    def settings(self) -> tuple[str, ...]:
        return AGENT_SETTINGS.get(self.name, ())

    def make(self, launch: Launch) -> Experience:
        return self.factory(launch)


def _echo(launch: Launch) -> Experience:
    from autobot.tui.echo import Echo

    return Echo()


def _question(launch: Launch) -> Experience:
    from autobot.agents.question import Question

    return Question(launch)


def _weather(launch: Launch) -> Experience:
    from autobot.agents.weather import Weather

    return Weather(launch)


def _time(launch: Launch) -> Experience:
    from autobot.agents.time_of_day import TimeOfDay

    return TimeOfDay(launch)


def _camera(launch: Launch) -> Experience:
    from autobot.agents.camera import Camera

    return Camera(launch)


def _introduce(launch: Launch) -> Experience:
    from autobot.agents.introduce import Introduce

    return Introduce(launch)


def _core(launch: Launch) -> Experience:
    from autobot.agents.core import Core

    return Core(launch)


def _by_name(*agents: Agent) -> Mapping[str, Agent]:
    return {agent.name: agent for agent in agents}


# The agents, by name, in the order of `ROUTE_AGENTS`.
AGENTS: Mapping[str, Agent] = _by_name(
    BuiltAgent(
        AGENT_ECHO,
        "Says it is about to record, records five seconds from the microphone, "
        "and plays the recording back. The recording is held in memory only "
        "and is dropped when the echo ends. Nothing leaves this machine.",
        uses_model=False,
        sends_requests=False,
        factory=_echo,
    ),
    BuiltAgent(
        AGENT_QUESTION,
        "Takes what you said after the command word as your question and "
        "sends it, as text, with the prompt below, to the Gemini model chosen "
        "on the Google Cloud screen, through Gemini ADK on Vertex AI as "
        "autobot's service account. It reads the summary of the answer aloud "
        "in the local voice and lays the detail out in the panel. Only the "
        "words the listener transcribed go out; no audio does, and nothing "
        "is remembered between questions.",
        uses_model=True,
        sends_requests=True,
        factory=_question,
    ),
    BuiltAgent(
        AGENT_WEATHER,
        "Looks up the weather for the zip code below with Google's Weather "
        "API and says it in a sentence, with the day laid out in the panel. "
        "The command weather gives today; the forecast mode gives the days "
        "ahead. The zip code becomes coordinates from a table on this "
        "machine. The API key is read from Secret Manager for the one call "
        "and cleared; it is never in the config file. The agent counts its "
        "own lookups against the two limits below before a call goes out.",
        uses_model=False,
        sends_requests=True,
        factory=_weather,
    ),
    BuiltAgent(
        AGENT_TIME,
        "Reads this computer's clock and says the time, with the date, the "
        "time to the second and the time zone in the panel. It reaches "
        "nothing outside this machine.",
        uses_model=False,
        sends_requests=False,
        factory=_time,
    ),
    BuiltAgent(
        AGENT_CAMERA,
        "Starts the local commands below, in order, in the folder below, "
        "and does not wait for them: the camera application's proxy server "
        "and then Firefox on its page. It runs on the NUC only. It stops "
        "nothing and closes nothing; what the commands print goes to "
        "run/agents.log.",
        uses_model=False,
        sends_requests=False,
        factory=_camera,
    ),
    BuiltAgent(
        AGENT_INTRODUCE,
        "Reads the script below aloud, as it is written, in the local "
        "voice, and shows its lines in the panel. No language model is "
        "called: edit the script to change what autobot says about himself.",
        uses_model=False,
        sends_requests=False,
        factory=_introduce,
    ),
    BuiltAgent(
        AGENT_CORE,
        "autobot himself: takes what you say when no command matches and "
        "talks with you, a turn at a time, on the language model of the "
        "inference server in the house, with the prompt below as his "
        "personality. He speaks each sentence as it arrives. The "
        "conversation is kept on this machine, in memory, for the session "
        "only, and is sent whole with each turn, as text. He can hand a "
        "hard question to an agent whose model is gemini, which asks from "
        "this machine after that agent's own confirmation. When the server "
        "is away he follows the fallback below.",
        uses_model=True,
        sends_requests=True,
        factory=_core,
    ),
)
