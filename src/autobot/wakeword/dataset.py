"""The voice dataset: the owner's recordings and the manifest that names them.

Recordings are 16 kHz mono 16-bit WAV files in one directory, `data/voice/`,
and `data/voice/manifest.jsonl` is the only record of what each clip is: one
JSON object per line (`ClipRecord`). A WAV with no manifest line does not
exist to the training, and neither does a line whose WAV is gone. The
directory is never committed and nothing here sends a clip anywhere: the
recordings never leave the NUC (CONSTRAINTS.md CN-1).

The split between training and held-out data is decided here and nowhere
else, and it never changes between runs:

- a positive or negative clip recorded for a script prompt is held out when
  `sha1(speaker + "/" + prompt_id) % 5 == 0`, so every take of a prompt by a
  speaker lands on the same side;
- a positive or negative clip with no prompt identifier (a live-monitor mark,
  a clip saved from the Audio screen) is held out when `sha1(file) % 5 == 0`;
- an ambient clip is split by time: its first four fifths are trained on and
  its last fifth is held out (`ambient_slices`).

A committed test fixture is never a side effect of recording: `export_fixture`
writes one chosen clip under one of the fixture names of
`tests/fixtures/audio/README.md`, with the quiet lead-in and tail the listener
tests need (TS-5). That copy inside the checkout is the only way a recording
leaves the dataset directory.

This module does no drawing and touches no audio hardware.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import wave
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Literal

import numpy as np
import numpy.typing as npt

from autobot.tui.audio import Clip, sanitise_clip_name, write_wav

__all__ = [
    "AMBIENT_HELDOUT_SHARE",
    "FIXTURE_LEAD_IN",
    "FIXTURE_NAMES",
    "FIXTURE_SAMPLE_RATE",
    "FIXTURE_TAIL",
    "HELDOUT",
    "HELDOUT_ONE_IN",
    "LABELS",
    "LABEL_AMBIENT",
    "LABEL_NEGATIVE",
    "LABEL_POSITIVE",
    "MANIFEST",
    "MANIFEST_NAME",
    "SOURCES",
    "SOURCE_MONITOR",
    "SOURCE_SESSION",
    "TRAIN",
    "VOICE_DIR",
    "ClipRecord",
    "DatasetError",
    "HeldoutSize",
    "LabelCount",
    "Slice",
    "Split",
    "VoiceDataset",
    "ambient_slices",
    "clip_file_name",
    "export_fixture",
    "recorded_now",
    "sanitise_clip_name",
    "split",
    "voiced_span",
]

VOICE_DIR = Path("data/voice")
MANIFEST_NAME = "manifest.jsonl"
MANIFEST = VOICE_DIR / MANIFEST_NAME

LABEL_POSITIVE = "positive"
LABEL_NEGATIVE = "negative"
LABEL_AMBIENT = "ambient"
LABELS = (LABEL_POSITIVE, LABEL_NEGATIVE, LABEL_AMBIENT)

SOURCE_SESSION = "session"
SOURCE_MONITOR = "monitor"
SOURCES = (SOURCE_SESSION, SOURCE_MONITOR)

type Split = Literal["train", "heldout"]
TRAIN: Split = "train"
HELDOUT: Split = "heldout"
# One prompt (or one unprompted clip) in this many is held out.
HELDOUT_ONE_IN = 5
# The share of every ambient recording, at its end, that is held out.
AMBIENT_HELDOUT_SHARE = 1 / HELDOUT_ONE_IN

# A (start, end) range of an ambient clip, in seconds.
type Slice = tuple[float, float]

# The fixture file names, as the table in `tests/fixtures/audio/README.md` lists
# them; a test reads that table to keep the two in step.
FIXTURE_NAMES = (
    "autobot.wav",
    "hey_autobot.wav",
    "yo_autobot.wav",
    "room_noise.wav",
    "unrelated_speech.wav",
    "near_miss_auto.wav",
)
# The quiet a fixture has before and after its speech, in seconds.
FIXTURE_LEAD_IN = 0.5
FIXTURE_TAIL = 1.0
FIXTURE_SAMPLE_RATE = 16000
# The voiced span is found on frames this long.
_SPAN_FRAME_SECONDS = 0.02
# A frame is voiced when its level is this many times the clip's noise floor,
# and at least this share of the loudest frame's.
_SPAN_OVER_FLOOR = 3.0
_SPAN_OF_PEAK = 0.1

# The longest speaker and prompt parts of a file name, so that the time stamp
# always survives `sanitise_clip_name`'s limit.
_NAME_PART = 20
_STAMP_FORMAT = "%Y%m%d-%H%M%S"


class DatasetError(Exception):
    """The voice dataset could not be read or written. The message is one line."""


@dataclass(frozen=True)
class ClipRecord:
    """One line of the manifest: what one clip is (TS-4)."""

    # The WAV's file name inside the dataset directory. Leave it empty when
    # adding a clip: `VoiceDataset.add` names the file.
    file: str
    # The wake phrase the clip was recorded for.
    phrase: str
    # The text of the prompt that asked for the clip, or a description such as
    # "audio screen" for a clip with no script prompt.
    prompt: str
    # The prompt's identifier in the script; empty for a clip with no script prompt.
    prompt_id: str
    # One of `LABELS`.
    label: str
    # One of `SOURCES`.
    source: str
    speaker: str
    # The name of the input device the clip was recorded through.
    device: str
    # Local date and time of recording, ISO 8601 with seconds and the UTC
    # offset: "2026-11-26T17:40:03-05:00" (`recorded_now`).
    recorded_at: str

    def to_json_line(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False) + "\n"


@dataclass(frozen=True)
class LabelCount:
    clips: int = 0
    minutes: float = 0.0


@dataclass(frozen=True)
class HeldoutSize:
    """How much of the dataset is held out for evaluation (TS-7, TS-8)."""

    positives: int = 0
    negatives: int = 0
    ambient_minutes: float = 0.0


def recorded_now() -> str:
    """The current local time in the form `ClipRecord.recorded_at` holds."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def clip_file_name(record: ClipRecord) -> str:
    """`<speaker>-<prompt_id or source>-<YYYYmmdd-HHMMSS>.wav` for a record."""
    try:
        stamp = datetime.fromisoformat(record.recorded_at).strftime(_STAMP_FORMAT)
    except ValueError:
        raise DatasetError(
            f"recorded_at {record.recorded_at!r} is not an ISO 8601 date and time"
        ) from None
    parts = []
    for text in (record.speaker, record.prompt_id or record.source):
        try:
            part = sanitise_clip_name(text)[: -len(".wav")][:_NAME_PART]
        except ValueError:
            raise DatasetError(
                f"cannot name a clip from {text!r}: it needs a letter or a digit"
            ) from None
        parts.append(part.rstrip("-._"))
    return sanitise_clip_name(f"{parts[0]}-{parts[1]}-{stamp}")


def _bucket(key: str) -> int:
    digest = hashlib.sha1(key.encode("utf-8"), usedforsecurity=False).digest()
    return int.from_bytes(digest, "big") % HELDOUT_ONE_IN


def split(record: ClipRecord) -> Split:
    """Which side of the split a positive or negative clip is on. Stable.

    An ambient clip has no side: it is split by time, with `ambient_slices`,
    and asking for its side raises `ValueError`.
    """
    if record.label == LABEL_AMBIENT:
        raise ValueError(
            f"{record.file}: an ambient clip is split by time, with ambient_slices"
        )
    if record.prompt_id:
        key = f"{record.speaker}/{record.prompt_id}"
    else:
        key = record.file
    return HELDOUT if _bucket(key) == 0 else TRAIN


def ambient_slices(record: ClipRecord, seconds: float) -> tuple[Slice, Slice]:
    """(train, heldout) second ranges of an ambient clip `seconds` long.

    The first four fifths are trained on and the last fifth is held out, so a
    60 s clip gives `((0.0, 48.0), (48.0, 60.0))`. `record` is taken so that
    the rule can come to depend on the clip without changing the callers.
    """
    if record.label != LABEL_AMBIENT:
        raise ValueError(f"{record.file}: only an ambient clip is split by time")
    end = max(0.0, float(seconds))
    cut = end * (HELDOUT_ONE_IN - 1) / HELDOUT_ONE_IN
    return (0.0, cut), (cut, end)


def _parse_line(text: str, number: int, manifest: Path) -> ClipRecord:
    where = f"{manifest} line {number}"
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise DatasetError(f"{where} is not valid JSON") from None
    if not isinstance(data, dict):
        raise DatasetError(f"{where} is not a JSON object")
    values: dict[str, str] = {}
    for field in fields(ClipRecord):
        value = data.get(field.name, "" if field.name == "prompt_id" else None)
        if not isinstance(value, str):
            raise DatasetError(f"{where}: {field.name} is missing or not a string")
        values[field.name] = value
    record = ClipRecord(**values)
    _check(record, where)
    if not record.file or Path(record.file).name != record.file:
        raise DatasetError(f"{where}: file {record.file!r} is not a plain file name")
    return record


def _check(record: ClipRecord, where: str) -> None:
    if record.label not in LABELS:
        raise DatasetError(
            f"{where}: label {record.label!r} is not one of: {', '.join(LABELS)}"
        )
    if record.source not in SOURCES:
        raise DatasetError(
            f"{where}: source {record.source!r} is not one of: {', '.join(SOURCES)}"
        )


class VoiceDataset:
    """The clips under one directory and their manifest.

    Nothing is cached: every call reads the manifest, so two processes (the
    interface and a training run) always see the same dataset.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    @property
    def manifest(self) -> Path:
        return self.root / MANIFEST_NAME

    def path(self, clip: ClipRecord | str) -> Path:
        """The WAV file of a record, or of a file name."""
        return self.root / (clip if isinstance(clip, str) else clip.file)

    # --- reading ------------------------------------------------------------

    def _lines(self) -> list[tuple[str, ClipRecord]]:
        """Every manifest line with its record, in file order."""
        try:
            text = self.manifest.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except (OSError, UnicodeDecodeError) as error:
            raise DatasetError(f"cannot read {self.manifest}: {error}") from None
        return [
            (line, _parse_line(line, number, self.manifest))
            for number, line in enumerate(text.splitlines(), start=1)
            if line.strip()
        ]

    def records(self) -> list[ClipRecord]:
        """The clips, in the order they were added. A line whose WAV is gone is left out."""
        return [record for _, record in self._lines() if self.path(record).is_file()]

    def for_phrase(self, phrase: str) -> list[ClipRecord]:
        """The clips recorded for `phrase` (compared without case)."""
        wanted = phrase.strip().lower()
        return [
            record
            for record in self.records()
            if record.phrase.strip().lower() == wanted
        ]

    def last_speaker(self) -> str:
        """The speaker of the newest clip; empty for an empty dataset."""
        records = self.records()
        return records[-1].speaker if records else ""

    def seconds(self, clip: ClipRecord | str) -> float:
        """A clip's length, from its WAV header."""
        path = self.path(clip)
        try:
            with wave.open(str(path), "rb") as wav:
                return wav.getnframes() / wav.getframerate()
        except (OSError, EOFError, wave.Error, ZeroDivisionError):
            raise DatasetError(f"cannot read {path} as a WAV file") from None

    def load(self, clip: ClipRecord | str) -> Clip:
        """A clip's audio, as mono int16 samples."""
        path = self.path(clip)
        try:
            with wave.open(str(path), "rb") as wav:
                shape = (wav.getnchannels(), wav.getsampwidth())
                rate = wav.getframerate()
                frames = wav.readframes(wav.getnframes())
        except (OSError, EOFError, wave.Error):
            raise DatasetError(f"cannot read {path} as a WAV file") from None
        if shape != (1, 2):
            raise DatasetError(f"{path} is not a mono 16-bit WAV file")
        return Clip(np.frombuffer(frames, dtype="<i2").astype(np.int16), rate)

    def counts(self, phrase: str | None = None) -> dict[str, LabelCount]:
        """Clips and minutes for each of `LABELS`, for one phrase or for all."""
        records = self.records() if phrase is None else self.for_phrase(phrase)
        clips = dict.fromkeys(LABELS, 0)
        seconds = dict.fromkeys(LABELS, 0.0)
        for record in records:
            clips[record.label] += 1
            seconds[record.label] += self.seconds(record)
        return {
            label: LabelCount(clips[label], seconds[label] / 60) for label in LABELS
        }

    def heldout_size(self, phrase: str) -> HeldoutSize:
        """The held-out positives and negatives, and the held-out ambient minutes."""
        positives = negatives = 0
        ambient_seconds = 0.0
        for record in self.for_phrase(phrase):
            if record.label == LABEL_AMBIENT:
                _, (start, end) = ambient_slices(record, self.seconds(record))
                ambient_seconds += end - start
            elif split(record) == HELDOUT:
                if record.label == LABEL_POSITIVE:
                    positives += 1
                else:
                    negatives += 1
        return HeldoutSize(positives, negatives, ambient_seconds / 60)

    # --- writing ------------------------------------------------------------

    def _stored(self, record: ClipRecord) -> tuple[ClipRecord, Path]:
        """`record` checked, and the first free file name for it claimed.

        The file is created empty so that the name cannot be given out twice.
        """
        _check(record, "the clip")
        if not record.speaker.strip():
            raise DatasetError("the clip needs a speaker's name")
        if record.file:
            try:
                wanted = sanitise_clip_name(record.file)
            except ValueError as error:
                raise DatasetError(str(error)) from None
        else:
            wanted = clip_file_name(record)
        stem = wanted[: -len(".wav")]
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            number = 1
            while True:
                name = wanted if number == 1 else f"{stem}-{number}.wav"
                try:
                    (self.root / name).open("xb").close()
                    break
                except FileExistsError:
                    number += 1
        except OSError as error:
            raise DatasetError(
                f"cannot write into {self.root}: {error.strerror or error}"
            ) from None
        return (
            ClipRecord(**{**asdict(record), "file": name}),
            self.root / name,
        )

    def _append(self, record: ClipRecord) -> None:
        with self.manifest.open("a", encoding="utf-8") as handle:
            handle.write(record.to_json_line())
            handle.flush()
            os.fsync(handle.fileno())

    def add(self, clip: Clip, record: ClipRecord) -> ClipRecord:
        """Write `clip` as a WAV and append its manifest line; return the stored record.

        The file is named from the record (`clip_file_name`), or from
        `record.file` when that is set; a name already taken gets `-2`, `-3`,
        and so on. No existing clip is ever overwritten.
        """
        stored, path = self._stored(record)
        try:
            write_wav(path, clip, overwrite=True)
            self._append(stored)
        except OSError as error:
            with contextlib.suppress(OSError):
                path.unlink()
            raise DatasetError(
                f"cannot write into {self.root}: {error.strerror or error}"
            ) from None
        return stored

    def add_file(self, source: Path, record: ClipRecord) -> ClipRecord:
        """Move a finished WAV on the same file system into the dataset.

        For a recording too long to hold in memory, written as it was made.
        """
        stored, path = self._stored(record)
        moved = False
        try:
            os.replace(source, path)
            moved = True
            self._append(stored)
        except OSError as error:
            # Put the audio back rather than lose it; give the name up.
            with contextlib.suppress(OSError):
                if moved:
                    os.replace(path, source)
                else:
                    path.unlink()
            raise DatasetError(
                f"cannot write into {self.root}: {error.strerror or error}"
            ) from None
        return stored

    def remove(self, file: str) -> None:
        """Delete a clip: rewrite the manifest without its line, then delete the WAV."""
        lines = self._lines()
        kept = [line for line, record in lines if record.file != file]
        if len(kept) == len(lines):
            raise DatasetError(f"no clip named {file} in {self.manifest}")
        try:
            handle, temp_name = tempfile.mkstemp(
                dir=self.root, prefix=f".{MANIFEST_NAME}.", suffix=".tmp"
            )
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as temp:
                    temp.write("".join(f"{line}\n" for line in kept))
                    temp.flush()
                    os.fsync(temp.fileno())
                os.replace(temp_name, self.manifest)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(temp_name)
                raise
            self.path(file).unlink(missing_ok=True)
        except OSError as error:
            raise DatasetError(
                f"cannot write into {self.root}: {error.strerror or error}"
            ) from None


def voiced_span(
    samples: npt.NDArray[np.int16], sample_rate: int
) -> tuple[int, int] | None:
    """(first, end) sample positions of the speech in a clip; None when it has none.

    Speech is the frames whose level stands above the clip's own noise floor.
    A clip that is level throughout (room noise, silence) has no span.
    """
    frame = max(1, int(_SPAN_FRAME_SECONDS * sample_rate))
    count = len(samples) // frame
    if count < 2:
        return None
    frames = samples[: count * frame].astype(np.float64).reshape(count, frame)
    rms = np.sqrt((frames**2).mean(axis=1))
    peak = float(rms.max())
    floor = float(np.percentile(rms, 10))
    threshold = max(floor * _SPAN_OVER_FLOOR, peak * _SPAN_OF_PEAK)
    if peak <= 0 or threshold >= peak:
        return None
    voiced = np.flatnonzero(rms >= threshold)
    return int(voiced[0]) * frame, min(len(samples), (int(voiced[-1]) + 1) * frame)


def export_fixture(
    record: ClipRecord,
    source: Path,
    destination: Path,
    lead_in: float = FIXTURE_LEAD_IN,
    tail: float = FIXTURE_TAIL,
) -> Path:
    """Write the clip in `source` as a test fixture at `destination`.

    `record` is the clip's manifest line and `source` its WAV. The fixture is
    a 16 kHz mono 16-bit WAV in which the speech has at least `lead_in`
    seconds before it and `tail` seconds after it: where the recording has
    less, silence is added. An ambient clip, and a clip in which no speech
    stands out, is written as it is. An existing fixture is replaced, through
    a temporary file, so a failure leaves it as it was. The clip in the
    dataset is not changed. Raises `DatasetError` with a one-line message.
    """
    try:
        with wave.open(str(source), "rb") as wav:
            shape = (wav.getnchannels(), wav.getsampwidth(), wav.getframerate())
            frames = wav.readframes(wav.getnframes())
    except (OSError, EOFError, wave.Error):
        raise DatasetError(f"cannot read {source} as a WAV file") from None
    if shape != (1, 2, FIXTURE_SAMPLE_RATE):
        raise DatasetError(
            f"{source.name} is not a {FIXTURE_SAMPLE_RATE} Hz mono 16-bit WAV file"
        )
    samples = np.frombuffer(frames, dtype="<i2").astype(np.int16)
    if not len(samples):
        raise DatasetError(f"{source.name} holds no audio")
    span = None if record.label == LABEL_AMBIENT else voiced_span(samples, shape[2])
    if span is not None:
        first, end = span
        before = max(0, round(lead_in * shape[2]) - first)
        after = max(0, round(tail * shape[2]) - (len(samples) - end))
        samples = np.concatenate(
            [np.zeros(before, np.int16), samples, np.zeros(after, np.int16)]
        )
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(
            dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp"
        )
        os.close(handle)
        try:
            write_wav(Path(temp_name), Clip(samples, shape[2]), overwrite=True)
            # `mkstemp` makes the file private; a committed fixture is not.
            os.chmod(temp_name, 0o644)
            os.replace(temp_name, destination)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temp_name)
            raise
    except OSError as error:
        raise DatasetError(
            f"cannot write {destination}: {error.strerror or error}"
        ) from None
    return destination
