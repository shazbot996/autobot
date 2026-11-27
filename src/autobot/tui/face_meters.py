"""The face's two meters: the microphone in the header, the mouth under the eyes. No drawing.

`autobot.tui.screens.face` draws these. Both use the Audio screen's scale
(`meter_cells`, from `METER_FLOOR_DB` to full scale) and its three bands
(`meter_zones`: quiet, warm, hot).

- `MicMeter` shows that the microphone is live (FS-4). It wraps a `Capture`
  and the input stream on `[audio] input_device` that feeds it, and is in one
  of four states, each with its header text: `live` (the level and its hold),
  `silent` (`NO SIGNAL`, once the input has been digital silence for
  `SILENCE_WARNING_SECONDS`, as on the Audio screen), `off` (no stream: off
  the NUC, or not opened) and `failed` (the device would not open, with its
  one-line error). The same capture records the echo, so one stream serves
  the meter and the recording.
- `Mouth` moves with autobot's own sound (FS-6): the level of whichever clip
  is playing, the speech line or the owner's recording, at the point playback
  has reached by the clock. It is flat when nothing plays, and lets go of the
  clip once it has ended.

All audio stays on this machine (`CN-1`, `CN-2`).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import numpy as np

from autobot.audio_check import AudioCheckError, dbfs, resolve_device
from autobot.config import DeviceSpec
from autobot.tui.audio import (
    SILENCE_WARNING,
    AudioBackend,
    AudioError,
    Capture,
    Clip,
    InputStream,
    meter_cells,
    meter_zones,
)

type MicState = Literal["live", "silent", "off", "failed"]

# The header text of the states that show no level.
SILENT_TEXT = "NO SIGNAL"
OFF_TEXT = "OFF"
# The mouth reads the clip in blocks this long: the listener's frame, 80 ms.
MOUTH_BLOCK_SECONDS = 0.08


@dataclass(frozen=True)
class Bar:
    """A meter of `width` cells: how many are filled, where the hold marker is.

    `hold` is the cell of the peak marker, 0 for none. The bands run quiet up
    to `warn`, warm up to `hot`, and hot to the end, as `meter_zones` gives.
    """

    width: int
    cells: int
    hold: int
    warn: int
    hot: int


def bar(level: float, hold: float, width: int) -> Bar:
    """A level and its held peak, in sample units, as a meter of `width` cells."""
    width = max(0, width)
    cells = meter_cells(level, width)
    marker = meter_cells(hold, width)
    warn, hot = meter_zones(width)
    return Bar(width, cells, marker if marker > cells else 0, warn, hot)


class MicMeter:
    """The microphone's level, for the header: the capture and the stream it reads."""

    def __init__(self, capture: Capture) -> None:
        self.capture = capture
        self.stream: InputStream | None = None
        # The device's one-line error while the meter is `failed`.
        self.error = ""

    def open(self, audio: AudioBackend, device: DeviceSpec, frame_samples: int) -> bool:
        """Open `device` into the capture; False, and `failed`, when it will not open.

        The caller checks the NUC first (FS-14): off it, the meter stays `off`.
        """
        if self.stream is not None:
            return True
        try:
            index = resolve_device(device, "input", audio.query_devices())
            self.stream = audio.open_input(
                index, self.capture.sample_rate, frame_samples, self.capture.feed
            )
        except (AudioCheckError, AudioError) as error:
            self.error = str(error)
            return False
        self.error = ""
        return True

    def close(self) -> None:
        """Stop any recording, release the device, and fall back to `off`."""
        self.capture.stop_recording()
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        self.capture.reset_level()
        self.error = ""

    @property
    def state(self) -> MicState:
        if self.stream is None:
            return "failed" if self.error else "off"
        return "silent" if self.capture.delivering_silence else "live"

    @property
    def text(self) -> str:
        """The header's reading: the level, `NO SIGNAL`, `OFF`, or the device error."""
        state = self.state
        if state == "live":
            return dbfs(self.capture.level().peak)
        if state == "silent":
            return SILENT_TEXT
        if state == "failed":
            return self.error
        return OFF_TEXT

    @property
    def warning(self) -> str:
        """The Audio screen's silence warning while `silent`; empty otherwise."""
        return SILENCE_WARNING if self.state == "silent" else ""

    def bar(self, width: int) -> Bar:
        """The level and its hold while `live`; an empty meter in any other state."""
        if self.state != "live":
            return bar(0.0, 0.0, width)
        level = self.capture.level()
        return bar(level.peak, level.hold, width)


def mouth_level(
    clip: Clip, elapsed: float, block_seconds: float = MOUTH_BLOCK_SECONDS
) -> float:
    """The RMS, in sample units, of the block of `clip` playing at `elapsed` seconds.

    0 before the start and from the end on.
    """
    if elapsed < 0 or elapsed >= clip.seconds:
        return 0.0
    block = max(1, round(block_seconds * clip.sample_rate))
    start = int(elapsed * clip.sample_rate) // block * block
    samples = clip.samples[start : start + block].astype(np.float64)
    if len(samples) == 0:
        return 0.0
    return math.sqrt(float(np.mean(samples * samples)))


class Mouth:
    """The level of the clip autobot is playing, by the clock; flat when none is.

    `play` is called as the clip is handed to `AudioBackend.play`, so the
    meter and the speaker start together. The clock is in seconds,
    `time.monotonic` in the interface and a fake in the tests.
    """

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._clip: Clip | None = None
        self._started = 0.0

    def play(self, clip: Clip) -> None:
        """Follow `clip` from its start, now."""
        self._clip = clip
        self._started = self._clock()

    def stop(self) -> None:
        """Playback was stopped: the mouth is flat, and the clip is let go."""
        self._clip = None

    @property
    def playing(self) -> bool:
        """Whether a clip is still playing; one that has ended is let go here."""
        if self._clip is None:
            return False
        if self._clock() - self._started >= self._clip.seconds:
            self._clip = None
            return False
        return True

    def level(self) -> float:
        """The level of the playing clip now, in sample units; 0 when none plays."""
        if not self.playing:
            return 0.0
        assert self._clip is not None
        return mouth_level(self._clip, self._clock() - self._started)

    def bar(self, width: int) -> Bar:
        """The mouth as a meter of `width` cells, with no hold marker."""
        return bar(self.level(), 0.0, width)
