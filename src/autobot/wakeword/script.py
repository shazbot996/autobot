"""The recording script: what a session asks the speaker to say (TS-3).

`build_script` turns a wake phrase and its `PhraseTable` into a fixed list of
`Prompt`s: the phrase said every way the synthetic models missed it, the
sound-alikes and ordinary sentences that must not fire, five minutes of the
room with nobody speaking, and last an optional hour of the room left to
itself. Every clip a session records carries its prompt's `id` and `label`, so
it is labelled by construction.

The script is deterministic for a phrase, and prompt identifiers are stable:
the split between training and held-out clips hashes them
(`autobot.wakeword.dataset.split`), so renumbering a group would move its
clips across the split. Add prompts at the end of a group.

No drawing, no audio.
"""

from __future__ import annotations

from dataclasses import dataclass

from autobot.wakeword.data import SENTENCES, SUFFIXES, PhraseTable
from autobot.wakeword.dataset import LABEL_AMBIENT, LABEL_NEGATIVE, LABEL_POSITIVE

__all__ = [
    "AMBIENT_FLOOR_SECONDS",
    "AMBIENT_PROMPTS",
    "AMBIENT_SECONDS",
    "HARD_NEGATIVES",
    "LONG_AMBIENT_ID",
    "LONG_AMBIENT_MIN_SECONDS",
    "LONG_AMBIENT_SECONDS",
    "POSITIVE_COUNTS",
    "SENTENCE_NEGATIVES",
    "SPEECH_SECONDS",
    "Prompt",
    "build_script",
]

# The longest take of a spoken prompt; the recording stops by itself there.
SPEECH_SECONDS = 10.0

# How many prompts each kind of positive gets: 56 in all.
POSITIVE_COUNTS: dict[str, int] = {
    "bare": 10,
    "hey": 8,
    "yo": 8,
    "sentence": 8,
    "slow": 4,
    "fast": 4,
    "quiet": 4,
    "raised": 4,
    "near": 3,
    "far": 3,
}
# Sound-alikes from the phrase's table, and ordinary sentences.
HARD_NEGATIVES = 20
SENTENCE_NEGATIVES = 10

# The ambient floor: five hands-off prompts of a minute each.
AMBIENT_PROMPTS = 5
AMBIENT_SECONDS = 60.0
AMBIENT_FLOOR_SECONDS = AMBIENT_PROMPTS * AMBIENT_SECONDS
# The optional long prompt: an hour, which may be ended after half of it.
LONG_AMBIENT_SECONDS = 3600.0
LONG_AMBIENT_MIN_SECONDS = 1800.0
LONG_AMBIENT_ID = "ambient-long"

# What the positive kinds ask for, beside the text. `{phrase}` is filled in.
_INSTRUCTIONS: dict[str, str] = {
    "bare": "Say the wake phrase in your normal voice.",
    "hey": 'Say it with "hey" in front, as you would call across the room.',
    "yo": 'Say it with "yo" in front.',
    "sentence": "Say the whole sentence, without pausing after the wake phrase.",
    "slow": "Say it slowly, drawing the word out.",
    "fast": "Say it fast, as if in a hurry.",
    "quiet": "Say it quietly, almost under your breath.",
    "raised": "Say it with a raised voice, as if calling from another room.",
    "near": "Lean in close to the microphone and say it.",
    "far": "Step back to the far side of the room and say it.",
}
_HARD_INSTRUCTION = "Say these words: they sound like the wake phrase but are not it."
_SENTENCE_INSTRUCTION = "Read this sentence aloud in your normal voice."
_AMBIENT_INSTRUCTION = "Say nothing, hands off, for one minute. It stops by itself."
_LONG_INSTRUCTION = "Leave it running (60 min, optional)"
# Words put before the phrase in the sentence prompts, in turn.
_SENTENCE_PREFIXES = ("", "hey", "", "yo", "", "okay", "", "so")


@dataclass(frozen=True)
class Prompt:
    """One thing the session asks for, and how its clip is labelled."""

    # Stable identifier inside the script, such as "bare-01": the manifest's
    # `prompt_id`, and what the split hashes.
    id: str
    # What to say, shown large. For an ambient prompt, what to do.
    text: str
    # One of `autobot.wakeword.dataset.LABELS`.
    label: str
    # How to say it, shown above the text.
    instruction: str
    # How long the recording may run. A spoken prompt is stopped by the
    # speaker before that; an ambient prompt runs the whole time.
    seconds: float = SPEECH_SECONDS
    # Whether the wake phrase is the last thing in `text` (positives only):
    # the training places the phrase's end at the end of the speech then.
    phrase_last: bool = True
    # The recording cannot be ended by the speaker before this many seconds.
    min_seconds: float = 0.0
    # An optional prompt is offered, and a session is complete without it.
    optional: bool = False

    @property
    def ambient(self) -> bool:
        return self.label == LABEL_AMBIENT

    @property
    def hands_off(self) -> bool:
        """Whether the recording runs to its end by itself (a countdown)."""
        return self.ambient


def _distinct(texts: tuple[str, ...] | list[str]) -> list[str]:
    seen: set[str] = set()
    kept = []
    for text in texts:
        if text not in seen:
            seen.add(text)
            kept.append(text)
    return kept


def _sentence_suffixes(count: int) -> list[str]:
    """`count` different things to say after the phrase, spread over `SUFFIXES`."""
    suffixes = _distinct([suffix for suffix in SUFFIXES if suffix])
    if not suffixes:
        return ["can you hear me"] * count
    step = max(1, len(suffixes) // count)
    return [suffixes[(index * step) % len(suffixes)] for index in range(count)]


def _hard_negatives(phrase: str, table: PhraseTable, count: int) -> list[str]:
    """The sound-alikes to ask for: the nearest ones first.

    Every `train_excluded` text (said the same as the phrase, or one sound
    away) comes first, in the table's order, then the first distinct trained
    ones.
    """
    hard = _distinct(table.hard)
    nearest = [text for text in hard if text in table.train_excluded]
    trained = [text for text in _distinct(table.trained) if text not in nearest]
    chosen = [text for text in nearest + trained if text.lower() != phrase]
    return chosen[:count]


def build_script(phrase: str, table: PhraseTable) -> list[Prompt]:
    """The session's prompts for `phrase`, in the order they are asked."""
    phrase = " ".join(phrase.lower().split())
    prompts: list[Prompt] = []

    def positives(kind: str, texts: list[str], *, phrase_last: bool = True) -> None:
        for number, text in enumerate(texts, start=1):
            prompts.append(
                Prompt(
                    id=f"{kind}-{number:02d}",
                    text=text,
                    label=LABEL_POSITIVE,
                    instruction=_INSTRUCTIONS[kind],
                    phrase_last=phrase_last,
                )
            )

    counts = POSITIVE_COUNTS
    positives("bare", [phrase] * counts["bare"])
    positives("hey", [f"hey {phrase}"] * counts["hey"])
    positives("yo", [f"yo {phrase}"] * counts["yo"])
    sentences = [
        " ".join(part for part in (prefix, f"{phrase},", suffix) if part)
        for prefix, suffix in zip(
            (_SENTENCE_PREFIXES * counts["sentence"])[: counts["sentence"]],
            _sentence_suffixes(counts["sentence"]),
            strict=True,
        )
    ]
    positives("sentence", sentences, phrase_last=False)
    for kind in ("slow", "fast", "quiet", "raised", "near", "far"):
        positives(kind, [phrase] * counts[kind])

    for number, text in enumerate(
        _hard_negatives(phrase, table, HARD_NEGATIVES), start=1
    ):
        prompts.append(
            Prompt(f"hard-{number:02d}", text, LABEL_NEGATIVE, _HARD_INSTRUCTION)
        )
    spoken = [text for text in SENTENCES if phrase not in text.lower()]
    for number, text in enumerate(spoken[:SENTENCE_NEGATIVES], start=1):
        prompts.append(
            Prompt(f"speech-{number:02d}", text, LABEL_NEGATIVE, _SENTENCE_INSTRUCTION)
        )

    for number in range(1, AMBIENT_PROMPTS + 1):
        prompts.append(
            Prompt(
                id=f"ambient-{number:02d}",
                text="say nothing",
                label=LABEL_AMBIENT,
                instruction=_AMBIENT_INSTRUCTION,
                seconds=AMBIENT_SECONDS,
            )
        )
    prompts.append(
        Prompt(
            id=LONG_AMBIENT_ID,
            text="leave it running",
            label=LABEL_AMBIENT,
            instruction=_LONG_INSTRUCTION,
            seconds=LONG_AMBIENT_SECONDS,
            min_seconds=LONG_AMBIENT_MIN_SECONDS,
            optional=True,
        )
    )
    return prompts
