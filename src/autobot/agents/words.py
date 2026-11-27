"""The shared words: global action keywords and the words that answer. No drawing.

The `[words]` table of `config/autobot.toml` (`autobot.config.WordsConfig`)
holds three lists every agent shares: the words that answer a confirmation
yes, the words that answer it no (AG-13), and the global action keywords,
which act at any level of the tree and are tried before the rules (AG-14).

A word stands in a transcript by the rules' containment rule
(`autobot.routing.contains_command`): lower-cased, apostrophes dropped,
punctuation turned into word breaks, spaces ignored, starting at the start
of a word. A word of these lists must also end where a word of the
transcript ends, so "please cancel that" holds `cancel` and "cancellation"
does not, and "nothing" is not "no".

Everything here is text: the listener's local transcript (`CN-1`).
"""

from __future__ import annotations

from collections.abc import Iterable

from autobot.config import Config
from autobot.routing import normalise


def stands_in(transcript: str, word: str) -> bool:
    """Whether `word` stands in `transcript`, from a word's start to a word's end."""
    wanted = normalise(word).replace(" ", "")
    if not wanted:
        return False
    words = normalise(transcript).split()
    compact = "".join(words)
    starts: list[int] = []
    ends: set[int] = set()
    at = 0
    for part in words:
        starts.append(at)
        at += len(part)
        ends.add(at)
    return any(
        compact.startswith(wanted, start) and start + len(wanted) in ends
        for start in starts
    )


def match_word(transcript: str, words: Iterable[str]) -> str | None:
    """The first word of `words` that stands in `transcript`; None when none does."""
    for word in words:
        if stands_in(transcript, word):
            return word
    return None


def action_keyword(transcript: str, config: Config) -> str | None:
    """The global action keyword said in `transcript`, from `[words] actions` (AG-14)."""
    return match_word(transcript, config.words.actions)
