"""Silero VAD and the speech gate.

`SileroVad` runs the Silero voice-activity model (ONNX, local, through
`onnxruntime` directly: no torch). `SpeechGate` turns its per-frame speech
probability into stretches of speech, so that frames of silence never reach a
detector. This is the listener's first efficiency measure and it holds for
every detector kind.

Silero v5 does not take our 80 ms frames. At 16 kHz it wants windows of
exactly 512 samples, each prefixed with the last 64 samples of the window
before it, plus a recurrent state carried from call to call. A 1280-sample
frame is 2.5 windows, so `SileroVad` keeps the remainder between frames: a
frame completes two or three windows, and its probability is the highest of
them.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

from autobot.listener.detector import Frame
from autobot.listener.models import SILERO_VAD_MODEL, enforce_offline, require_file

INT16_FULL_SCALE = 32768.0

# Silero v5 window and context sizes, by sample rate.
_WINDOW_SAMPLES = {16000: 512, 8000: 256}
_CONTEXT_SAMPLES = {16000: 64, 8000: 32}


class VadError(Exception):
    """The VAD cannot run. The message is one line."""


class Vad(Protocol):
    """Anything that scores frames for speech; `SileroVad` or a test fake."""

    def probability(self, frame: Frame) -> float:
        """The probability, from 0 to 1, that `frame` holds speech."""
        ...

    def reset(self) -> None:
        """Forget all state: the next frame does not follow the last one."""
        ...


class SileroVad:
    """Silero VAD v5 on the CPU, one thread."""

    def __init__(self, sample_rate: int = 16000, model: str | Path | None = None):
        if sample_rate not in _WINDOW_SAMPLES:
            raise VadError(
                f"Silero VAD supports 8000 and 16000 Hz, not sample_rate = {sample_rate}"
            )
        path = require_file(model or SILERO_VAD_MODEL, "Silero VAD")
        enforce_offline()
        import onnxruntime

        options = onnxruntime.SessionOptions()
        # One thread: the model is tiny, and idle thread pools cost CPU all day.
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        options.log_severity_level = 3
        self._session: Any = onnxruntime.InferenceSession(
            str(path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self._window = _WINDOW_SAMPLES[sample_rate]
        self._context_samples = _CONTEXT_SAMPLES[sample_rate]
        self._rate = np.array(sample_rate, dtype=np.int64)
        self._state: npt.NDArray[np.float32]
        self._context: npt.NDArray[np.float32]
        self._pending: npt.NDArray[np.float32]
        self._last = 0.0
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(self._context_samples, dtype=np.float32)
        self._pending = np.zeros(0, dtype=np.float32)
        self._last = 0.0

    def probability(self, frame: Frame) -> float:
        samples = np.concatenate(
            (self._pending, frame.astype(np.float32) / INT16_FULL_SCALE)
        )
        whole = len(samples) - len(samples) % self._window
        highest: float | None = None
        for start in range(0, whole, self._window):
            window = samples[start : start + self._window]
            output, self._state = self._session.run(
                None,
                {
                    "input": np.concatenate((self._context, window))[np.newaxis, :],
                    "state": self._state,
                    "sr": self._rate,
                },
            )
            self._context = window[-self._context_samples :]
            score = float(output[0, 0])
            highest = score if highest is None else max(highest, score)
        self._pending = samples[whole:]
        if highest is not None:
            self._last = highest
        # A frame shorter than one window completes none: repeat the last score.
        return self._last


@dataclass(frozen=True)
class GateResult:
    """What the gate lets through for one microphone frame."""

    # Frames to feed to the detectors, oldest first. Empty during silence.
    frames: tuple[Frame, ...] = ()
    # True when this frame ended a stretch of speech.
    speech_ended: bool = False


class SpeechGate:
    """Let only stretches of speech through.

    Speech starts on a frame scoring at least `start_threshold`; the
    `pre_roll_frames` frames before it are released with it. Speech ends after
    `hangover_frames` consecutive frames scoring below `end_threshold`; those
    quiet frames are still released, and the last one carries `speech_ended`.

    `push` scores the frame with the gate's VAD. `push_scored` takes a score
    that was worked out already, which is how several gates with different
    margins share one VAD: `with_margins` makes such a gate, with no VAD of
    its own (see `autobot.listener.service.Pipeline`).
    """

    def __init__(
        self,
        vad: Vad | None,
        *,
        start_threshold: float = 0.5,
        end_threshold: float = 0.35,
        pre_roll_frames: int = 3,
        hangover_frames: int = 6,
    ):
        self._vad = vad
        self.start_threshold = start_threshold
        self.end_threshold = end_threshold
        self.pre_roll_frames = pre_roll_frames
        self.hangover_frames = hangover_frames
        self._pre_roll: deque[Frame] = deque(maxlen=pre_roll_frames)
        self._in_speech = False
        self._quiet = 0

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def with_margins(self, pre_roll_frames: int, hangover_frames: int) -> SpeechGate:
        """A gate with these margins and this gate's thresholds, and no VAD."""
        return SpeechGate(
            None,
            start_threshold=self.start_threshold,
            end_threshold=self.end_threshold,
            pre_roll_frames=pre_roll_frames,
            hangover_frames=hangover_frames,
        )

    def reset(self) -> None:
        """Forget all state: audio was dropped."""
        if self._vad is not None:
            self._vad.reset()
        self._pre_roll.clear()
        self._in_speech = False
        self._quiet = 0

    def probability(self, frame: Frame) -> float:
        """The VAD's score for the next frame. Call it once per frame."""
        if self._vad is None:
            raise VadError("this speech gate has no VAD: use push_scored")
        return self._vad.probability(frame)

    def push(self, frame: Frame) -> GateResult:
        return self.push_scored(frame, self.probability(frame))

    def push_scored(self, frame: Frame, score: float) -> GateResult:
        """Take the next frame with its speech probability, already worked out."""
        if not self._in_speech:
            if score < self.start_threshold:
                self._pre_roll.append(frame)
                return GateResult()
            self._in_speech = True
            self._quiet = 0
            released = (*self._pre_roll, frame)
            self._pre_roll.clear()
            return GateResult(frames=released)
        self._quiet = self._quiet + 1 if score < self.end_threshold else 0
        if self._quiet >= self.hangover_frames:
            self._in_speech = False
            self._quiet = 0
            return GateResult(frames=(frame,), speech_ended=True)
        return GateResult(frames=(frame,))
