"""The `openwakeword` engine: a trained wake-word model, scored frame by frame.

This is the cheap detector for 24x7 use. A small model trained for one phrase
(`make train-wakeword` writes `models/wakeword/autobot.onnx`) is run on
openWakeWord's shared feature extractor: every 80 ms frame becomes one 96-value
embedding, and the model scores the last 16 embeddings (1.28 s of them, which
see about two seconds of audio). The detector fires when the score reaches the
phrase's `threshold`. No transcription is involved.

The engine runs the three ONNX files with `onnxruntime` and `numpy` only. The
`openwakeword` Python package is not a dependency: it needs `tflite-runtime`,
which has no wheel for Python 3.12. `OnnxScorer` repeats the package's
streaming feature code and was checked to give the same scores as
`openwakeword.Model`, to the last digit.

How it decides:

- The service gates frames through the VAD, so the engine sees stretches of
  speech only and is idle in silence (`autobot.listener.detector`). Each fed
  frame is scored. The engine asks for wider margins than the gate's
  defaults, `GATE_MARGINS`: 12 frames (0.96 s) before the speech and 10
  (0.8 s) after it. The model scores highest in the half second after the
  phrase ends, which is often after the VAD has stopped hearing speech, and
  it wants to have heard what came before. Measured on 140 "autobot"
  utterances in seven voices the model was not trained on, at threshold 0.9:
  scoring every frame detected 90.7 %; the default margins (3 and 6 frames)
  82.9 % from a cold start and 85.0 % primed (below); these margins 90.7 %,
  feeding 53 % of the frames of a stream that was half speech.
- The model needs context. It was trained on the phrase with quiet or noise
  before it, and its scores mean nothing until its two-second window has
  filled. So the scorer starts every stretch from a *primed* state: buffers
  holding the features of a little near-silence, computed once when the
  detector is built. `end_of_speech()` and `reset()` both return to it. A
  stretch therefore looks to the model like quiet, then the speech, which is
  what it was trained on, and nothing is carried from one stretch of speech
  into the next.
- After it fires it ignores the next `refractory_seconds` of fed audio, so one
  utterance yields one event: the score stays high for several frames after
  the phrase ends.

The cost is two small ONNX calls and one tiny one per fed frame, about 1.5 ms
on the NUC, each on one thread, and nothing at all during silence.

`last_peak` holds the highest score of the stretch that last ended; the
service logs it when `AUTOBOT_LISTENER_DEBUG` is set, which is how a threshold
is tuned against a real room.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

from autobot.config import (
    KIND_OPENWAKEWORD,
    AudioConfig,
    ConfigError,
    WakePhraseConfig,
)
from autobot.listener.detector import Clock, Detector, Frame, WakeEvent, utc_now
from autobot.listener.models import (
    OPENWAKEWORD_EMBEDDING_MODEL,
    OPENWAKEWORD_MELSPEC_MODEL,
    ModelMissingError,
    enforce_offline,
    require_file,
)

# What openWakeWord's feature extractor and its models are built for.
SAMPLE_RATE = 16000
FRAME_SAMPLES = 1280
# The mel model is given each frame with this much of the audio before it.
MEL_CONTEXT_SAMPLES = 480
# The embedding model reads 76 mel rows (760 ms) and is stepped 8 rows (80 ms).
MEL_BINS = 32
EMBEDDING_MEL_ROWS = 76
EMBEDDING_STEP_ROWS = 8
EMBEDDING_SIZE = 96
# A wake-word model scores this many embeddings: 1.28 s of them.
MODEL_EMBEDDINGS = 16

# (pre-roll, hangover) frames this engine asks the speech gate for: see the
# module docstring and `autobot.listener.detector`.
GATE_MARGINS = (12, 10)

# Frames of near-silence the primed state is computed from: enough to fill
# the mel rows and the model's embeddings.
PRIME_FRAMES = 26
# Standard deviation of that near-silence, in int16 units (about -64 dBFS).
PRIME_NOISE_LEVEL = 20.0

type Embeddings = npt.NDArray[np.float32]


class Scorer(Protocol):
    """Scores a stream of frames for one phrase; `OnnxScorer` or a test fake."""

    def score(self, frame: Frame) -> float:
        """Take the next frame; return the model's score, from 0 to 1."""
        ...

    def reset(self) -> None:
        """Forget the stream: the next frame does not follow the last one."""
        ...


def mel_transform(spectrogram: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
    """openWakeWord's scaling of the mel model's output, before the embedding."""
    scaled: npt.NDArray[np.float32] = spectrogram / 10.0 + 2.0
    return scaled


def onnx_session(path: Path) -> Any:
    """A one-thread CPU session. `enforce_offline()` runs before the import."""
    enforce_offline()
    import onnxruntime

    options = onnxruntime.SessionOptions()
    # One thread: the models are tiny, and idle thread pools cost CPU all day.
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    options.log_severity_level = 3
    return onnxruntime.InferenceSession(
        str(path), sess_options=options, providers=["CPUExecutionProvider"]
    )


@cache
def feature_sessions() -> tuple[Any, Any]:
    """The mel and embedding sessions, one pair per process, shared by every phrase."""
    melspec = require_file(OPENWAKEWORD_MELSPEC_MODEL, "openWakeWord melspectrogram")
    embedding = require_file(OPENWAKEWORD_EMBEDDING_MODEL, "openWakeWord embedding")
    return onnx_session(melspec), onnx_session(embedding)


def require_wakeword_model(path: Path | None, phrase: str) -> Path:
    """The trained model's path, or `ModelMissingError` saying how to make it."""
    if path is None:
        raise ConfigError(f"wake phrase {phrase!r}: openwakeword needs a model file")
    if not path.is_file():
        raise ModelMissingError(
            f"wake-word model file for {phrase!r} is missing: {path} "
            "(train it with 'make train-wakeword'; models are never downloaded "
            "at run time)"
        )
    return path


class OnnxScorer:
    """openWakeWord's streaming features and one wake-word model, on onnxruntime.

    Per frame: the mel model turns the frame, with a little of the audio
    before it, into 8 mel rows; the embedding model turns the last 76 rows
    into one embedding; the wake-word model scores the last 16 embeddings.
    """

    def __init__(
        self,
        model: Path,
        *,
        sessions: tuple[Any, Any] | None = None,
        prime_frames: int = PRIME_FRAMES,
    ):
        self._melspec, self._embedding = sessions or feature_sessions()
        self._model = onnx_session(model)
        model_input = self._model.get_inputs()[0]
        self._model_input: str = model_input.name
        shape = tuple(model_input.shape[1:])
        if shape != (MODEL_EMBEDDINGS, EMBEDDING_SIZE):
            raise ConfigError(
                f"{model} is not a wake-word model this engine can run: its input is "
                f"{shape}, not ({MODEL_EMBEDDINGS}, {EMBEDDING_SIZE})"
            )
        self._melspec_input: str = self._melspec.get_inputs()[0].name
        self._embedding_input: str = self._embedding.get_inputs()[0].name
        self._raw: npt.NDArray[np.int16]
        self._mel: npt.NDArray[np.float32]
        self._embeddings: Embeddings
        self._cold_start()
        if prime_frames > 0:
            noise = np.random.default_rng(0).normal(
                0.0, PRIME_NOISE_LEVEL, (prime_frames, FRAME_SAMPLES)
            )
            for frame in noise.astype(np.int16):
                self._advance(frame)
        self._primed = (self._raw, self._mel, self._embeddings)

    def _cold_start(self) -> None:
        self._raw = np.zeros(MEL_CONTEXT_SAMPLES, dtype=np.int16)
        self._mel = np.ones((EMBEDDING_MEL_ROWS, MEL_BINS), dtype=np.float32)
        self._embeddings = np.zeros((0, EMBEDDING_SIZE), dtype=np.float32)

    def reset(self) -> None:
        # `_advance` builds new arrays and never writes into these.
        self._raw, self._mel, self._embeddings = self._primed

    def _advance(self, frame: Frame) -> None:
        """Take one frame into the buffers: one more embedding."""
        audio = np.concatenate((self._raw, frame))
        self._raw = audio[-MEL_CONTEXT_SAMPLES:]
        spectrogram = self._melspec.run(
            None, {self._melspec_input: audio.astype(np.float32)[np.newaxis, :]}
        )[0]
        rows = mel_transform(spectrogram.reshape(-1, MEL_BINS))
        self._mel = np.concatenate((self._mel, rows))[-EMBEDDING_MEL_ROWS:]
        embedding = self._embedding.run(
            None, {self._embedding_input: self._mel[np.newaxis, :, :, np.newaxis]}
        )[0]
        self._embeddings = np.concatenate(
            (self._embeddings, embedding.reshape(1, EMBEDDING_SIZE))
        )[-MODEL_EMBEDDINGS:]

    def score(self, frame: Frame) -> float:
        self._advance(frame)
        if len(self._embeddings) < MODEL_EMBEDDINGS:
            return 0.0
        output = self._model.run(
            None, {self._model_input: self._embeddings[np.newaxis, :, :]}
        )[0]
        return float(output.reshape(-1)[0])


class OpenWakeWordDetector:
    """A `Detector` that fires when the model's score reaches the threshold."""

    gate_margins = GATE_MARGINS

    def __init__(
        self,
        phrase: str,
        target: str,
        scorer: Scorer,
        *,
        threshold: float = 0.5,
        frame_seconds: float = FRAME_SAMPLES / SAMPLE_RATE,
        refractory_seconds: float = 2.0,
        clock: Clock = utc_now,
    ):
        self.phrase = phrase
        self.target = target
        self.threshold = threshold
        self._scorer = scorer
        self._clock = clock
        self._refractory_frames = round(refractory_seconds / frame_seconds)
        self._refractory_left = 0
        self._peak = 0.0
        # The highest score of the stretch of speech that last ended.
        self.last_peak: float | None = None

    def feed(self, frame: Frame) -> WakeEvent | None:
        score = self._scorer.score(frame)
        self._peak = max(self._peak, score)
        if self._refractory_left > 0:
            self._refractory_left -= 1
            return None
        if score < self.threshold:
            return None
        self._refractory_left = self._refractory_frames
        return WakeEvent(
            phrase=self.phrase,
            target=self.target,
            kind=KIND_OPENWAKEWORD,
            score=round(score, 3),
            at=self._clock(),
        )

    def end_of_speech(self) -> WakeEvent | None:
        # The next stretch starts from the primed state. The refractory period
        # is kept: a pause must not let one utterance fire twice.
        self.last_peak = self._peak
        self._peak = 0.0
        self._scorer.reset()
        return None

    def reset(self) -> None:
        # Audio was dropped mid-stretch: what was heard of it still counts as
        # the last stretch, for the debug log.
        self.last_peak = self._peak
        self._scorer.reset()
        self._refractory_left = 0
        self._peak = 0.0


def build(phrase: WakePhraseConfig, audio: AudioConfig) -> Detector:
    """The engine's builder: see `autobot.listener.detector.build_detector`."""
    if audio.sample_rate != SAMPLE_RATE or audio.frame_samples != FRAME_SAMPLES:
        raise ConfigError(
            f"openwakeword needs {FRAME_SAMPLES}-sample frames at {SAMPLE_RATE} Hz "
            f"(sample_rate = 16000, frame_ms = 80), not {audio.frame_samples} "
            f"samples at {audio.sample_rate} Hz"
        )
    model = require_wakeword_model(phrase.model_path, phrase.phrase)
    return OpenWakeWordDetector(
        phrase.phrase,
        phrase.target,
        OnnxScorer(model),
        threshold=phrase.threshold,
    )
