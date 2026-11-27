"""The words speech-to-text is biased toward: autobot's own (IS-16). No drawing.

Each transcription the NUC asks of the server carries hints, which the
server gives its model as hotwords. They are built here, from the
configuration alone, in the order routing depends on them:

1. the wake phrases;
2. each routing rule's agent, command and mode, in the rules' order, and
   then the name of any agent no rule names;
3. the yes words and the no words;
4. the global action keywords.

A word is listed once, whatever its case. The list is cut where the hints,
joined as the server joins them (`, `), would pass `[server] hint_chars`:
the cap keeps the model's prompt short, and what is cut is what matters
least.
"""

from __future__ import annotations

from collections.abc import Iterable

from autobot.config import Config

# What the server joins the hints with (`autobot_server.stt.hotwords`).
SEPARATOR = ", "


def candidate_hints(config: Config) -> Iterable[str]:
    """Every hint the configuration gives, in order, repeats and all."""
    for phrase in config.wake_phrases:
        yield phrase.phrase
    for route in config.routes:
        yield route.agent
        yield route.command
        yield route.mode
        # The example phrases are sentences, not vocabulary: they are left out.
    for agent in config.agents:
        yield agent.name
    yield from config.words.yes
    yield from config.words.no
    yield from config.words.actions


def build_hints(config: Config) -> tuple[str, ...]:
    """The hints for a transcription: de-duplicated and cut at `hint_chars`."""
    limit = config.server.hint_chars
    hints: list[str] = []
    seen: set[str] = set()
    used = 0
    for candidate in candidate_hints(config):
        word = " ".join(candidate.split())
        if not word or word.lower() in seen:
            continue
        cost = len(word) + (len(SEPARATOR) if hints else 0)
        if used + cost > limit:
            break
        seen.add(word.lower())
        hints.append(word)
        used += cost
    return tuple(hints)
