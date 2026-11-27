"""Audio logic for the interface: devices, levels, clips. No drawing.

The pure parts (`usable_devices`, `Capture`, `meter_cells`, `clip_peak`,
`sanitise_clip_name`, `write_wav`) are tested with fakes. `AudioBackend` is
the seam to the hardware; `SoundDeviceBackend` is the real one and is the only
code here that touches `sounddevice`, which it imports lazily so that
importing this module never loads PortAudio.

Everything is 16 kHz mono int16, the format the listener uses. All audio stays
on this machine (CONSTRAINTS.md CN-1).
"""

from __future__ import annotations

import contextlib
import math
import re
import threading
import wave
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

from autobot.audio_check import (
    INT16_FULL_SCALE,
    AudioCheckError,
    DeviceKind,
    levels,
    resolve_device,
)
from autobot.config import DeviceSpec

type Samples = npt.NDArray[np.int16]
type BlockHandler = Callable[[Samples], None]

# The bottom of the level meter, in dB relative to full scale.
METER_FLOOR_DB = -60.0
# Above these the meter turns yellow, then red.
METER_WARN_DB = -18.0
METER_HOT_DB = -6.0
# A test clip stops by itself after this long.
MAX_CLIP_SECONDS = 10.0
# How much of the held peak survives each block (80 ms): about 1 s to fall 12 dB.
PEAK_HOLD_DECAY = 0.9

# Names PipeWire gives the ALSA devices that follow its default source and sink.
PIPEWIRE_DEVICE_NAMES = ("pipewire", "default")

# The committed test fixtures. Nothing is saved here as a side effect: a clip
# is recorded into the voice dataset (`autobot.wakeword.dataset`) and a
# fixture is a deliberate export of one.
FIXTURE_DIR = Path("tests/fixtures/audio")
MAX_CLIP_NAME = 64


class AudioError(Exception):
    """An audio device could not be used. The message is one line."""


# --- devices ----------------------------------------------------------------


@dataclass(frozen=True)
class DeviceChoice:
    index: int
    name: str
    # What to write to the config file to select this device.
    spec: DeviceSpec

    @property
    def label(self) -> str:
        return f"{self.index:>2}  {self.name}"


def device_spec(
    index: int, kind: DeviceKind, devices: Sequence[Mapping[str, Any]]
) -> DeviceSpec:
    """The config value for device `index`: its name when that is unambiguous.

    Names are preferred because indexes move when hardware is plugged in. The
    index is the fallback when the name alone would match another device.
    """
    name = str(devices[index]["name"])
    try:
        if name.strip() and resolve_device(name, kind, devices) == index:
            return name
    except AudioCheckError:
        pass
    return index


def usable_devices(
    devices: Sequence[Mapping[str, Any]],
    kind: DeviceKind,
    supports: Callable[[int], bool],
) -> list[DeviceChoice]:
    """The devices of `kind` that `supports` accepts (16 kHz mono int16)."""
    channels_key = f"max_{kind}_channels"
    return [
        DeviceChoice(index, str(device["name"]), device_spec(index, kind, devices))
        for index, device in enumerate(devices)
        if device[channels_key] > 0 and supports(index)
    ]


def describe_device(
    spec: DeviceSpec, kind: DeviceKind, devices: Sequence[Mapping[str, Any]]
) -> tuple[int | None, str]:
    """(index, text) for a configured device; index None when it does not resolve."""
    try:
        index = resolve_device(spec, kind, devices)
    except AudioCheckError as error:
        return None, str(error)
    return index, f"#{index} {devices[index]['name']}"


def follows_pipewire(
    spec: DeviceSpec, kind: DeviceKind, devices: Sequence[Mapping[str, Any]]
) -> bool:
    """Whether the configured device is one that follows PipeWire's default."""
    try:
        name = str(devices[resolve_device(spec, kind, devices)]["name"])
    except AudioCheckError:
        return False
    return name.strip().lower() in PIPEWIRE_DEVICE_NAMES


# --- levels -----------------------------------------------------------------


@dataclass(frozen=True)
class Level:
    """Levels of the most recent block, in int16 sample units."""

    peak: int = 0
    rms: float = 0.0
    # The recent maximum of `peak`, falling slowly: the meter's peak marker.
    hold: float = 0.0


def level_db(level: float) -> float:
    """A level in sample units as dB relative to full scale; -inf for silence."""
    if level <= 0:
        return -math.inf
    return 20 * math.log10(level / INT16_FULL_SCALE)


def meter_cells(level: float, width: int) -> int:
    """How many of `width` meter cells a level fills, from the floor to 0 dBFS."""
    if width <= 0:
        return 0
    db = level_db(level)
    if db <= METER_FLOOR_DB:
        return 0
    return min(width, max(1, round(width * (1 - db / METER_FLOOR_DB))))


def meter_zones(width: int) -> tuple[int, int]:
    """The cells at which the meter turns yellow and then red."""
    warn = round(width * (1 - METER_WARN_DB / METER_FLOOR_DB))
    hot = round(width * (1 - METER_HOT_DB / METER_FLOOR_DB))
    return warn, hot


# --- capture ----------------------------------------------------------------

# A block whose peak is at or below this is digital silence: a working
# microphone in the quietest room still delivers more.
SILENT_PEAK = 3
# The input has to be digital silence for this long before the interface says
# so: a stream may open with a moment of zeros.
SILENCE_WARNING_SECONDS = 2.0
# What the interface shows while it lasts.
SILENCE_WARNING = "the microphone delivers silence: check its mute button"


@dataclass(frozen=True)
class Clip:
    samples: Samples
    sample_rate: int

    @property
    def seconds(self) -> float:
        return len(self.samples) / self.sample_rate

    @property
    def peak(self) -> int:
        return levels(self.samples.tolist())[0]


@dataclass(frozen=True)
class StreamedClip:
    """A recording that was written to a WAV file as it was made."""

    path: Path
    sample_rate: int
    samples: int
    peak: int
    # One line when writing the file failed part of the way; empty otherwise.
    error: str = ""

    @property
    def seconds(self) -> float:
        return self.samples / self.sample_rate


def clip_peak(samples: Samples) -> int:
    """The largest sample magnitude, without leaving numpy (a long clip is slow otherwise)."""
    if len(samples) == 0:
        return 0
    return int(np.abs(samples.astype(np.int32)).max())


class Capture:
    """Takes blocks from the input stream; keeps the level and records clips.

    `feed` runs on the audio thread; everything else on the interface's. One
    stream therefore serves both the meter and the recording.

    A recording is held in memory and handed over by `take_clip`, unless
    `start_recording` is given a file to stream to: then every block is
    written to that WAV as it arrives, nothing is kept in memory, and the
    result comes from `take_streamed`. That is how an hour of room audio is
    recorded.

    It also counts how long the input has been digital silence without a
    break (`silent_seconds`), so that a muted microphone is seen on the meter
    before anything is recorded (`delivering_silence`).
    """

    def __init__(self, sample_rate: int, max_seconds: float = MAX_CLIP_SECONDS) -> None:
        self.sample_rate = sample_rate
        # The limit of a recording that names none of its own.
        self.max_samples = int(max_seconds * sample_rate)
        self._limit = self.max_samples
        self._lock = threading.Lock()
        self._level = Level()
        # Samples of digital silence in a row, up to now.
        self._silent = 0
        self._recording = False
        self._blocks: list[Samples] = []
        self._recorded = 0
        self._peak = 0
        self._finished: Clip | None = None
        self._wav: wave.Wave_write | None = None
        self._wav_path: Path | None = None
        self._wav_error = ""
        self._streamed: StreamedClip | None = None

    def feed(self, block: Samples) -> None:
        """Take one block of mono int16 samples."""
        peak, rms = levels(block.tolist())
        with self._lock:
            hold = max(float(peak), self._level.hold * PEAK_HOLD_DECAY)
            self._level = Level(peak, rms, hold)
            self._silent = self._silent + len(block) if peak <= SILENT_PEAK else 0
            if not self._recording:
                return
            room = self._limit - self._recorded
            taken = block[:room]
            if self._wav is not None:
                try:
                    self._wav.writeframes(taken.astype("<i2").tobytes())
                except OSError as error:
                    # A full disk, say: keep what was written and stop.
                    self._wav_error = error.strerror or str(error)
                    self._finish()
                    return
            else:
                self._blocks.append(taken.copy())
            self._recorded += len(taken)
            self._peak = max(self._peak, peak)
            if self._recorded >= self._limit:
                self._finish()

    def level(self) -> Level:
        with self._lock:
            return self._level

    def reset_level(self) -> None:
        with self._lock:
            self._level = Level()
            self._silent = 0

    @property
    def silent_seconds(self) -> float:
        """How long the input has been digital silence, without a break, up to now."""
        with self._lock:
            return self._silent / self.sample_rate

    @property
    def delivering_silence(self) -> bool:
        """Whether the input has been digital silence for `SILENCE_WARNING_SECONDS`."""
        return self.silent_seconds >= SILENCE_WARNING_SECONDS

    @property
    def recording(self) -> bool:
        with self._lock:
            return self._recording

    @property
    def recorded_seconds(self) -> float:
        with self._lock:
            return self._recorded / self.sample_rate

    @property
    def limit_seconds(self) -> float:
        """How long the current (or the last) recording may run."""
        with self._lock:
            return self._limit / self.sample_rate

    def start_recording(
        self, max_seconds: float | None = None, stream_to: Path | None = None
    ) -> None:
        """Start a recording that stops by itself after `max_seconds`.

        With `stream_to`, the audio goes to that WAV file as it arrives.
        Raises `AudioError` when the file cannot be created.
        """
        wav: wave.Wave_write | None = None
        if stream_to is not None:
            try:
                stream_to.parent.mkdir(parents=True, exist_ok=True)
                # Open for the whole recording: `_finish` closes it.
                wav = wave.open(str(stream_to), "wb")  # noqa: SIM115
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(self.sample_rate)
            except OSError as error:
                raise AudioError(
                    f"cannot write {stream_to}: {error.strerror or error}"
                ) from None
        with self._lock:
            if self._recording:
                self._finish()
            self._blocks = []
            self._recorded = 0
            self._peak = 0
            self._finished = None
            self._streamed = None
            self._wav = wav
            self._wav_path = stream_to
            self._wav_error = ""
            self._limit = (
                self.max_samples
                if max_seconds is None
                else max(1, int(max_seconds * self.sample_rate))
            )
            self._recording = True

    def stop_recording(self) -> None:
        with self._lock:
            if self._recording:
                self._finish()

    def take_clip(self) -> Clip | None:
        """The clip of a recording that has ended, once; None otherwise."""
        with self._lock:
            clip, self._finished = self._finished, None
            return clip

    def take_streamed(self) -> StreamedClip | None:
        """The file of a streamed recording that has ended, once; None otherwise."""
        with self._lock:
            streamed, self._streamed = self._streamed, None
            return streamed

    def _finish(self) -> None:
        self._recording = False
        if self._wav is not None:
            assert self._wav_path is not None
            error = self._wav_error
            try:
                self._wav.close()
            except OSError as close_error:
                error = error or close_error.strerror or str(close_error)
            self._streamed = StreamedClip(
                self._wav_path, self.sample_rate, self._recorded, self._peak, error
            )
            self._wav = None
            self._wav_path = None
            return
        if self._blocks:
            samples = np.concatenate(self._blocks).astype(np.int16)
        else:
            samples = np.zeros(0, dtype=np.int16)
        self._blocks = []
        self._finished = Clip(samples, self.sample_rate)


# --- clips ------------------------------------------------------------------


def sanitise_clip_name(name: str) -> str:
    """Turn what the owner typed into a safe `.wav` file name.

    Only letters, digits, `.`, `_` and `-` survive; anything else, including
    path separators, becomes `-`, so the name cannot leave the directory the
    clip is saved in. Raises `ValueError` when nothing usable is left.
    """
    stem = name.strip()
    if stem.lower().endswith(".wav"):
        stem = stem[:-4]
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem)
    stem = re.sub(r"-{2,}", "-", stem).strip("-._")
    stem = stem[:MAX_CLIP_NAME].rstrip("-._")
    if not stem:
        raise ValueError("the file name needs at least one letter or digit")
    return f"{stem}.wav"


def write_wav(path: Path, clip: Clip, *, overwrite: bool = False) -> None:
    """Write `clip` as a mono 16-bit WAV. Raises `FileExistsError` unless `overwrite`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with (
        path.open("wb" if overwrite else "xb") as handle,
        wave.open(handle, "wb") as wav,
    ):
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(clip.sample_rate)
        wav.writeframes(clip.samples.astype("<i2").tobytes())


# --- hardware ---------------------------------------------------------------


class InputStream(Protocol):
    def close(self) -> None:
        """Stop the stream and release the device."""


class AudioBackend(Protocol):
    def query_devices(self) -> list[Mapping[str, Any]]:
        """The device list, as PortAudio found it when this process first asked."""

    def supports(self, index: int, kind: DeviceKind, sample_rate: int) -> bool:
        """Whether the device opens as mono int16 at `sample_rate`."""

    def open_input(
        self, index: int, sample_rate: int, block_samples: int, on_block: BlockHandler
    ) -> InputStream:
        """Start capturing; `on_block` gets each block on the audio thread."""

    def play(self, clip: Clip, index: int) -> None:
        """Start playing `clip` and return at once."""

    def stop_playback(self) -> None:
        """Stop whatever `play` started."""


class _SoundDeviceStream:
    def __init__(self, stream: Any) -> None:
        self._stream = stream

    def close(self) -> None:
        # Closing a stream whose device has gone must not raise.
        with contextlib.suppress(Exception):
            self._stream.stop()
            self._stream.close()


class SoundDeviceBackend:
    """The real backend: PortAudio through `sounddevice`, local devices only."""

    def query_devices(self) -> list[Mapping[str, Any]]:
        import sounddevice as sd

        # PortAudio lists devices once, when it starts, so hardware plugged in
        # while the interface is open appears only after a restart. That is
        # acceptable here: such hardware shows up at once as a PipeWire source
        # or sink, which the Audio screen asks for live. Indexes also differ
        # from run to run (busy hardware devices drop out of the list), which
        # is why `device_spec` writes names.
        try:
            return [dict(device) for device in sd.query_devices()]
        except sd.PortAudioError as error:
            raise AudioError(f"cannot list audio devices: {error}") from None

    def supports(self, index: int, kind: DeviceKind, sample_rate: int) -> bool:
        import sounddevice as sd

        check = sd.check_input_settings if kind == "input" else sd.check_output_settings
        try:
            check(device=index, samplerate=sample_rate, channels=1, dtype="int16")
        except (sd.PortAudioError, ValueError):
            return False
        return True

    def open_input(
        self, index: int, sample_rate: int, block_samples: int, on_block: BlockHandler
    ) -> InputStream:
        import sounddevice as sd

        def callback(indata: Any, frames: int, time: Any, status: Any) -> None:
            on_block(indata[:, 0].copy())

        try:
            stream = sd.InputStream(
                device=index,
                samplerate=sample_rate,
                channels=1,
                dtype="int16",
                blocksize=block_samples,
                callback=callback,
            )
            stream.start()
        except sd.PortAudioError as error:
            raise AudioError(f"cannot open the input device: {error}") from None
        return _SoundDeviceStream(stream)

    def play(self, clip: Clip, index: int) -> None:
        import sounddevice as sd

        try:
            sd.play(clip.samples, samplerate=clip.sample_rate, device=index)
        except sd.PortAudioError as error:
            raise AudioError(f"cannot play on the output device: {error}") from None

    def stop_playback(self) -> None:
        import sounddevice as sd

        try:
            sd.stop()
        except sd.PortAudioError:
            pass
