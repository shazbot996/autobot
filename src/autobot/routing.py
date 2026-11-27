"""Agent routing: the rules that hand a spoken command to an agent. No drawing.

The wake phrase only wakes autobot. What is said after it is the command,
and the rules decide which agent receives it (RR-1, RR-3). A rule is one
`[[routes]]` entry of `config/autobot.toml` (`autobot.config.RouteConfig`):
a `command`, a word or a few words, the `agent` it routes to, and an
optional `mode` the agent receives with it (AG-7).

A rule matches a transcript when its command stands in it, among any other
words, by the listener's containment rule (WW-6): lower-cased, apostrophes
dropped, punctuation turned into word breaks, and the command's letters,
spaces removed, found among the transcript's starting at the start of a
word. So "Echo!", "please echo that" and "eco, echo" all match `echo`, and
"techo" does not. The rules are tried in the order the file lists them, and
the first that matches wins. No rule matching is not an error: autobot keeps
listening until its time for a command runs out.

Everything here is text: the transcript comes from the listener's local
speech-to-text (`CN-1`), and no rule calls anything outside this machine.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from autobot.config import (
    AGENT_CAMERA,
    AGENT_ECHO,
    AGENT_INTRODUCE,
    AGENT_QUESTION,
    AGENT_TIME,
    AGENT_WEATHER,
    RouteConfig,
)

# What each agent does, in a few words, for the screens.
AGENT_NOTES = {
    AGENT_ECHO: "records five seconds of your voice and plays it back",
    AGENT_QUESTION: "asks a language model your question and answers it",
    AGENT_WEATHER: "says the weather for your zip code, or the days ahead",
    AGENT_TIME: "says the time from this computer's clock",
    AGENT_CAMERA: "opens the camera application in Firefox on the NUC",
    AGENT_INTRODUCE: "says who autobot is and how he works",
}

_APOSTROPHES = re.compile(r"['’]")
_NOT_WORD = re.compile(r"[^a-z0-9]+")


def normalise(text: str) -> str:
    """Lower-case words separated by single spaces; punctuation removed."""
    lowered = _APOSTROPHES.sub("", text.lower())
    return _NOT_WORD.sub(" ", lowered).strip()


def contains_command(transcript: str, command: str) -> bool:
    """Whether `command` stands in `transcript`, by the module's matching rule."""
    wanted = normalise(command).replace(" ", "")
    if not wanted:
        return False
    words = normalise(transcript).split()
    compact = "".join(words)
    start = 0
    for word in words:
        if compact.startswith(wanted, start):
            return True
        start += len(word)
    return False


@dataclass(frozen=True)
class Match:
    """The rule a transcript matched."""

    route: RouteConfig
    # The rule's place in the file, from 1.
    number: int
    # The words it matched in.
    transcript: str

    def describe(self) -> str:
        text = f'rule {self.number}: "{self.route.command}" -> {self.route.agent} agent'
        return f"{text} ({self.route.mode})" if self.route.mode else text


def match_route(transcript: str, routes: Sequence[RouteConfig]) -> Match | None:
    """The first rule whose command stands in `transcript`; None when none does."""
    for number, route in enumerate(routes, start=1):
        if contains_command(transcript, route.command):
            return Match(route, number, " ".join(transcript.split()))
    return None


def commands_text(routes: Sequence[RouteConfig]) -> str:
    """The commands, quoted, for a sentence: `"echo"`, `"echo" or "list"`."""
    quoted = [f'"{route.command}"' for route in routes]
    if len(quoted) <= 1:
        return "".join(quoted)
    return ", ".join(quoted[:-1]) + " or " + quoted[-1]
