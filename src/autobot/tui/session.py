"""The recording session: which prompt is next, and what a take becomes. No drawing.

A `Session` is one speaker working through the script
(`autobot.wakeword.script`) for one wake phrase. It keeps no state of its own
beyond a copy of the manifest: a prompt is answered when the voice dataset
holds a session clip for it by this speaker, so a session that is left resumes
at the first unanswered prompt whenever it is opened again, in this run of the
interface or a later one.

A take is stored the moment it is accepted (`accept`, or `accept_file` for a
recording that was streamed to disk), so nothing recorded is ever held only in
memory; `discard` removes the latest take of a prompt again.

The screen that draws this is `autobot.tui.screens.session`.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from autobot.audio_check import INT16_FULL_SCALE
from autobot.tui.audio import SILENT_PEAK, Clip, level_db
from autobot.wakeword.dataset import (
    LABEL_AMBIENT,
    LABELS,
    SOURCE_SESSION,
    ClipRecord,
    VoiceDataset,
    recorded_now,
)
from autobot.wakeword.script import Prompt

# A long recording is streamed to `<root>/.recording-<pid>.wav.part` and moved
# into place when it is accepted. The dataset ignores the file meanwhile: it
# has no manifest line, and it is not named `*.wav`.
STREAM_PREFIX = ".recording-"
STREAM_SUFFIX = ".wav.part"

# A take whose peak is at or below `SILENT_PEAK` is digital silence: a muted
# microphone.
# A spoken take whose peak is below this (-40 dBFS) is flagged as too quiet.
QUIET_PEAK = round(INT16_FULL_SCALE * 10 ** (-40 / 20))
# A take whose peak reaches this hit full scale.
CLIPPED_PEAK = int(INT16_FULL_SCALE) - 68


def take_warning(peak: int, label: str) -> str:
    """A few words when a take's level looks wrong; empty when it looks fine.

    What is wrong comes before the colon and what to do after it. A warning
    never refuses a take: the speaker decides whether to keep it.
    """
    if peak <= SILENT_PEAK:
        return "digital silence: is the microphone muted?"
    if peak >= CLIPPED_PEAK:
        return "clipped: move back or lower the input level"
    if label != LABEL_AMBIENT and peak < QUIET_PEAK:
        return f"very quiet (peak {level_db(peak):.0f} dBFS): move closer"
    return ""


@dataclass(frozen=True)
class Progress:
    """How far a session is."""

    # Required prompts answered, and how many there are.
    done: int
    total: int
    # (answered, total) of the required prompts, for each label.
    labels: dict[str, tuple[int, int]]
    # Optional prompts answered, and how many there are.
    optional_done: int
    optional_total: int

    @property
    def complete(self) -> bool:
        return self.done >= self.total

    @property
    def fraction(self) -> float:
        return self.done / self.total if self.total else 1.0


def stream_path(root: Path, pid: int | None = None) -> Path:
    """Where this process streams a long recording under the dataset directory."""
    return root / f"{STREAM_PREFIX}{os.getpid() if pid is None else pid}{STREAM_SUFFIX}"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (ProcessLookupError, OverflowError, ValueError):
        # Gone, or a number no process can have.
        return False
    return True


def remove_stale_streams(root: Path) -> list[Path]:
    """Delete the partial files of long recordings that were interrupted.

    A file belongs to the process whose id is in its name. It is stale when
    that process is gone, or is this one (which calls this only when it is
    not recording). Another running interface's file is left alone. Never
    raises: a file that cannot be deleted is ignored, as the dataset ignores it.
    """
    removed: list[Path] = []
    try:
        candidates = sorted(root.glob(f"{STREAM_PREFIX}*{STREAM_SUFFIX}"))
    except OSError:
        return removed
    for path in candidates:
        owner = path.name[len(STREAM_PREFIX) : -len(STREAM_SUFFIX)]
        if owner.isdigit() and int(owner) != os.getpid() and _alive(int(owner)):
            continue
        with contextlib.suppress(OSError):
            path.unlink()
            removed.append(path)
    return removed


class Session:
    """One speaker's way through the script for one wake phrase."""

    def __init__(
        self,
        dataset: VoiceDataset,
        phrase: str,
        speaker: str,
        script: Sequence[Prompt],
    ) -> None:
        self.dataset = dataset
        self.phrase = phrase
        self.speaker = speaker.strip()
        self.script: list[Prompt] = list(script)
        self._records: list[ClipRecord] = []
        self.refresh()

    def refresh(self) -> None:
        """Read the manifest again. Raises `DatasetError` when it cannot be read."""
        self._records = [
            record
            for record in self.dataset.for_phrase(self.phrase)
            if record.speaker.strip() == self.speaker
            and record.source == SOURCE_SESSION
            and record.prompt_id
        ]

    # --- reading ------------------------------------------------------------

    def clips_for(self, prompt: Prompt) -> list[ClipRecord]:
        """This speaker's takes of `prompt`, oldest first."""
        return [record for record in self._records if record.prompt_id == prompt.id]

    def answered(self, prompt: Prompt) -> bool:
        return any(record.prompt_id == prompt.id for record in self._records)

    def next_index(self) -> int | None:
        """The position of the first prompt with no clip; None when all have one."""
        answered = {record.prompt_id for record in self._records}
        for index, prompt in enumerate(self.script):
            if prompt.id not in answered:
                return index
        return None

    def next_prompt(self) -> Prompt | None:
        """The first prompt with no clip by this speaker: where a session resumes."""
        index = self.next_index()
        return None if index is None else self.script[index]

    def progress(self) -> Progress:
        answered = {record.prompt_id for record in self._records}
        done = dict.fromkeys(LABELS, 0)
        total = dict.fromkeys(LABELS, 0)
        optional_done = optional_total = 0
        for prompt in self.script:
            if prompt.optional:
                optional_total += 1
                optional_done += prompt.id in answered
            else:
                total[prompt.label] = total.get(prompt.label, 0) + 1
                if prompt.id in answered:
                    done[prompt.label] = done.get(prompt.label, 0) + 1
        return Progress(
            done=sum(done.values()),
            total=sum(total.values()),
            labels={label: (done[label], total[label]) for label in total},
            optional_done=optional_done,
            optional_total=optional_total,
        )

    def complete(self) -> bool:
        """Whether every prompt that is not optional has a clip."""
        return self.progress().complete

    # --- writing ------------------------------------------------------------

    def _record(self, prompt: Prompt, device: str) -> ClipRecord:
        return ClipRecord(
            file="",
            phrase=self.phrase,
            prompt=prompt.text,
            prompt_id=prompt.id,
            label=prompt.label,
            source=SOURCE_SESSION,
            speaker=self.speaker,
            device=device,
            recorded_at=recorded_now(),
        )

    def accept(self, prompt: Prompt, clip: Clip, device: str) -> ClipRecord:
        """Store `clip` as a take of `prompt`. Raises `DatasetError`."""
        stored = self.dataset.add(clip, self._record(prompt, device))
        self._records.append(stored)
        return stored

    def accept_file(self, prompt: Prompt, source: Path, device: str) -> ClipRecord:
        """Move a streamed recording into place as a take of `prompt`.

        Raises `DatasetError`; the file is left where it was then.
        """
        stored = self.dataset.add_file(source, self._record(prompt, device))
        self._records.append(stored)
        return stored

    def discard(self, prompt: Prompt) -> ClipRecord | None:
        """Delete the latest take of `prompt`; None when it has none.

        Raises `DatasetError`.
        """
        takes = self.clips_for(prompt)
        if not takes:
            return None
        latest = takes[-1]
        self.dataset.remove(latest.file)
        self._records.remove(latest)
        return latest


def complete_speakers(
    dataset: VoiceDataset, phrase: str, script: Sequence[Prompt]
) -> list[str]:
    """The speakers who have recorded one complete session for `phrase`."""
    required = {prompt.id for prompt in script if not prompt.optional}
    answered: dict[str, set[str]] = {}
    for record in dataset.for_phrase(phrase):
        if record.source == SOURCE_SESSION and record.prompt_id:
            answered.setdefault(record.speaker.strip(), set()).add(record.prompt_id)
    return [speaker for speaker, ids in answered.items() if required <= ids]
