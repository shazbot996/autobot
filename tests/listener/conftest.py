"""Shared pieces of the listener tests.

Two guarantees are made here for every test in this directory:

- **No network.** `socket` is patched so that connecting anywhere but a Unix
  domain socket, or resolving a host name, fails the test (CONSTRAINTS.md
  CN-1 and CN-2: the listener path makes no network calls). The patch sees
  Python code only. Native code is covered by the offline environment, set
  below before any speech library can be imported, and was checked from
  outside with `strace -f -e trace=connect` (see `autobot.listener.models`).
- **No downloads.** The model files are looked up under `models/`; when one
  is missing the run fails with a message naming it. Nothing is skipped and
  nothing is fetched.
"""

from __future__ import annotations

import json
import socket
import wave
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from autobot.config import Config, parse_config
from autobot.listener.detector import Frame, WakeEvent
from autobot.listener.engines.text_match import WHISPER_FILES
from autobot.listener.models import (
    FASTER_WHISPER_MODEL_DIR,
    OPENWAKEWORD_EMBEDDING_MODEL,
    OPENWAKEWORD_MELSPEC_MODEL,
    PIPER_VOICES_DIR,
    SILERO_VAD_MODEL,
    WAKEWORD_MODELS_DIR,
    ModelMissingError,
    enforce_offline,
    require_dir,
    require_file,
)
from autobot.listener.service import Pipeline, build_pipeline

# Before any test module imports onnxruntime, faster-whisper or piper.
enforce_offline()

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "audio"
FRAME_SAMPLES = 1280
SAMPLE_RATE = 16000
FIXED_TIME = datetime(2026, 11, 25, 21, 30, 0, 500000, tzinfo=UTC)

# The trained model the `openwakeword` tests run: what `make train-wakeword` writes.
WAKEWORD_MODEL = WAKEWORD_MODELS_DIR / "autobot.onnx"

POSITIVE_CLIPS = ("autobot", "hey_autobot", "yo_autobot")
NEGATIVE_CLIPS = ("room_noise", "unrelated_speech", "near_miss_auto")


class NetworkAccessError(AssertionError):
    """A listener test tried to use the network."""


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that connects off the machine or resolves a host name."""
    enforce_offline()
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def connect(self: socket.socket, address: Any) -> None:
        if self.family != socket.AF_UNIX:
            raise NetworkAccessError(f"network connection attempted: {address!r}")
        real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        if self.family != socket.AF_UNIX:
            raise NetworkAccessError(f"network connection attempted: {address!r}")
        return real_connect_ex(self, address)

    def getaddrinfo(*args: Any, **kwargs: Any) -> Any:
        raise NetworkAccessError(f"host name lookup attempted: {args[:2]!r}")

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


@pytest.fixture(scope="session")
def models() -> None:
    """Fail, clearly, when a model file the tests need is not under `models/`."""
    try:
        require_file(SILERO_VAD_MODEL, "Silero VAD")
        require_dir(FASTER_WHISPER_MODEL_DIR, "faster-whisper", WHISPER_FILES)
    except ModelMissingError as error:
        pytest.fail(
            f"{error}. The listener tests need the model files in place; "
            "they are never downloaded by a test run.",
            pytrace=False,
        )


@pytest.fixture(scope="session")
def ack_voice(models: None) -> str:
    """The acknowledgement voice, checked to be present under `models/piper/`."""
    voice = "en_US-lessac-medium"
    try:
        require_file(PIPER_VOICES_DIR / f"{voice}.onnx", f"piper voice {voice!r}")
        require_file(PIPER_VOICES_DIR / f"{voice}.onnx.json", f"piper voice {voice!r}")
    except ModelMissingError as error:
        pytest.fail(f"{error}. Tests never download a voice.", pytrace=False)
    return voice


def make_config(
    kind: str = "text_match",
    nuc_hostname: str = "nuc",
    model: str = "",
    threshold: float = 0.5,
) -> Config:
    """A configuration with one `autobot` wake phrase, independent of the repo's file."""
    return parse_config(
        {
            "host": {"nuc_hostname": nuc_hostname},
            "audio": {"input_device": "pipewire", "output_device": "pipewire"},
            "wake_phrases": [
                {
                    "phrase": "autobot",
                    "kind": kind,
                    "model": model,
                    "threshold": threshold,
                    "target": "ack",
                }
            ],
        }
    )


@pytest.fixture(scope="session")
def wakeword_threshold(models: None) -> float:
    """The trained model's own recommended threshold; fails when the model is missing.

    The model is trained, not fetched: `make train-wakeword` writes it and,
    beside it, a `.json` with the threshold its evaluation recommends. The
    fixture tests run the model at that threshold, so they follow whichever
    model is installed.
    """
    try:
        require_file(OPENWAKEWORD_MELSPEC_MODEL, "openWakeWord melspectrogram")
        require_file(OPENWAKEWORD_EMBEDDING_MODEL, "openWakeWord embedding")
        require_file(WAKEWORD_MODEL, "wake-word (train it with 'make train-wakeword')")
        metrics = require_file(WAKEWORD_MODEL.with_suffix(".json"), "wake-word metrics")
    except ModelMissingError as error:
        pytest.fail(
            f"{error}. The openwakeword tests need the trained model in place; "
            "a test run never trains or downloads one.",
            pytrace=False,
        )
    return float(json.loads(metrics.read_text())["recommended_threshold"])


@pytest.fixture
def openwakeword_pipeline(wakeword_threshold: float) -> Iterator[Pipeline]:
    """The service's own frame path, real VAD and the trained model, for `openwakeword`."""
    config = make_config(
        "openwakeword", model=str(WAKEWORD_MODEL), threshold=wakeword_threshold
    )
    pipeline = build_pipeline(config)
    yield pipeline
    pipeline.reset()


@pytest.fixture
def text_match_pipeline(models: None) -> Iterator[Pipeline]:
    """The service's own frame path, real VAD and real whisper, for `text_match`."""
    pipeline = build_pipeline(make_config("text_match"))
    yield pipeline
    pipeline.reset()


def load_clip(name: str) -> list[Frame]:
    """A fixture clip as the 80 ms frames the microphone would deliver."""
    path = FIXTURES / f"{name}.wav"
    if not path.is_file():
        pytest.fail(f"fixture clip is missing: {path}", pytrace=False)
    with wave.open(str(path), "rb") as wav:
        assert wav.getframerate() == SAMPLE_RATE, f"{path.name} is not 16 kHz"
        assert wav.getnchannels() == 1, f"{path.name} is not mono"
        assert wav.getsampwidth() == 2, f"{path.name} is not 16-bit"
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
    whole = len(samples) - len(samples) % FRAME_SAMPLES
    return [
        samples[start : start + FRAME_SAMPLES]
        for start in range(0, whole, FRAME_SAMPLES)
    ]


def silence(frames: int) -> list[Frame]:
    return [np.zeros(FRAME_SAMPLES, dtype=np.int16) for _ in range(frames)]


def run_clip(pipeline: Pipeline, name: str) -> list[WakeEvent]:
    """Feed a clip, then a second of quiet, through the pipeline."""
    events: list[WakeEvent] = []
    for frame in [*load_clip(name), *silence(12)]:
        events.extend(pipeline.process(frame))
    return events


# --- the `sherpa_kws` kind -----------------------------------------------------


@pytest.fixture(scope="session")
def sherpa_kws_model(models: None) -> Path:
    """The sherpa-onnx keyword-spotting model's directory; fails when it is missing."""
    from autobot.listener.engines.sherpa_kws import SHERPA_KWS_DIR, SHERPA_KWS_FILES

    try:
        return require_dir(
            SHERPA_KWS_DIR, "sherpa-onnx keyword spotter", SHERPA_KWS_FILES
        )
    except ModelMissingError as error:
        pytest.fail(
            f"{error}. The sherpa_kws tests need the model in place; "
            "a test run never downloads one.",
            pytrace=False,
        )


@pytest.fixture
def sherpa_kws_pipeline(sherpa_kws_model: Path) -> Iterator[Pipeline]:
    """The service's own frame path, real VAD and the real model, for `sherpa_kws`."""
    pipeline = build_pipeline(make_config("sherpa_kws"))
    yield pipeline
    pipeline.reset()
