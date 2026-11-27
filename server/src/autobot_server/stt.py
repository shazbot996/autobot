"""Speech-to-text: `POST /v1/stt` (IS-6, IS-7, IS-8, IS-16).

The `stt` job runs faster-whisper `large-v3-turbo` on the card, from the
directory `[server] stt_model` names under the checkout's `models/`, in the
compute type `[server] stt_compute_type` names (IS-10). The caller never
names the model (IS-8).

The model is loaded from its local directory only. The offline environment
is set before faster-whisper is imported, so neither its Hugging Face code
nor onnxruntime reaches the network (IS-7, `CONVENTIONS.md` "Local-only
speech"). The server keeps its own copy of that environment because it does
not import `autobot`. A missing directory makes the job not ready, with the
reason; it is never a fetch: `make server-fetch-models` is the one
downloader on this host.

A request is one utterance as a WAV file, 16 kHz mono 16-bit PCM, the format
the listener records; anything else is `400`. It is decoded with the
standard library's `wave`, in memory. The samples live in one local variable
while the model transcribes them and are then dropped: nothing is written,
kept or forwarded (IS-6).

The request's hints, autobot's own words, are given to the model as
hotwords, worded as a glossary entry as the NUC's `text_match` engine words
its phrase ("Glossary: autobot, weather."), which tells the decoder how the
words are spelled without forcing them onto other speech (IS-16).
"""

from __future__ import annotations

import io
import os
import time
import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from autobot_server.app import Endpoint, Response, json_response, request_json
from autobot_server.config import ServerSettings
from autobot_server.contract import ContractError, Segment, SttRequest, SttResponse
from autobot_server.job import Job, NotReady

STT_PATH = "/v1/stt"

# The one audio format the endpoint takes, as the listener records it (IS-8).
SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2
INT16_FULL_SCALE = 32768.0

# The files of a CTranslate2 faster-whisper model directory the server needs.
WHISPER_FILES = ("model.bin", "config.json", "tokenizer.json")
# The owner speaks English, and a short command is too little audio for the
# model to tell its language reliably.
LANGUAGE = "en"
BEAM_SIZE = 5
# How the hints are put to the model: see the module docstring.
HOTWORDS_FORMAT = "Glossary: {hints}."
# The warm-up: one second of silence, so that the first real request does not
# pay for CUDA's first kernels.
WARM_UP_SECONDS = 1.0

# Environment that keeps the speech libraries off the network, the same as the
# NUC's `autobot.listener.models._OFFLINE_ENV`. It must be set before
# faster-whisper is imported: call `enforce_offline()` first.
_OFFLINE_ENV = {
    "ORT_DISABLE_TELEMETRY": "1",
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
    "DO_NOT_TRACK": "1",
}


def enforce_offline() -> None:
    """Set the offline environment. Call before importing faster-whisper."""
    os.environ.update(_OFFLINE_ENV)


@dataclass(frozen=True)
class Utterance:
    """One utterance for the model: its samples and its hotwords.

    `pcm` is the WAV file's frames, 16 kHz mono 16-bit little-endian; it is
    held only while the request runs (IS-6).
    """

    pcm: bytes
    hotwords: str | None


@dataclass(frozen=True)
class Transcribed:
    """What the model heard: the text, its language and its segments."""

    text: str
    language: str
    segments: tuple[Segment, ...]


class SpeechModel(Protocol):
    """A loaded speech-to-text model: faster-whisper on the card, or a test fake."""

    def transcribe(self, utterance: Utterance) -> Transcribed:
        """The transcript of `utterance`."""
        ...


# Loads the model from its directory in a compute type.
type SpeechLoader = Callable[[Path, str], SpeechModel]


class Whisper:
    """faster-whisper on CUDA, loaded from a local directory only."""

    def __init__(self, path: Path, compute_type: str):
        enforce_offline()
        from faster_whisper import WhisperModel

        self._model: Any = WhisperModel(
            str(path),
            device="cuda",
            compute_type=compute_type,
            local_files_only=True,
        )

    def transcribe(self, utterance: Utterance) -> Transcribed:
        import numpy as np

        samples = (
            np.frombuffer(utterance.pcm, dtype="<i2").astype(np.float32)
            / INT16_FULL_SCALE
        )
        found, info = self._model.transcribe(
            samples,
            language=LANGUAGE,
            beam_size=BEAM_SIZE,
            # One pass at temperature 0: deterministic, and bounded in time.
            temperature=0.0,
            condition_on_previous_text=False,
            # The NUC's VAD has already cut the utterance (IS-8).
            vad_filter=False,
            hotwords=utterance.hotwords,
        )
        segments = tuple(
            Segment(
                start_seconds=round(float(segment.start), 2),
                end_seconds=round(float(segment.end), 2),
                text=str(segment.text).strip(),
            )
            for segment in found
        )
        return Transcribed(
            text=" ".join(segment.text for segment in segments if segment.text),
            language=str(info.language),
            segments=segments,
        )


class WhisperJob:
    """The `stt` job's engine: the model of `[server] stt_model`, kept resident."""

    def __init__(
        self,
        path: Path,
        compute_type: str,
        *,
        loader: SpeechLoader = Whisper,
    ):
        self._path = path
        self._compute_type = compute_type
        self._loader = loader
        self._model: SpeechModel | None = None

    @classmethod
    def from_settings(
        cls, settings: ServerSettings, *, loader: SpeechLoader = Whisper
    ) -> WhisperJob:
        """The engine of the model the server's settings name."""
        return cls(settings.stt_model_path, settings.stt_compute_type, loader=loader)

    @property
    def model(self) -> str:
        """The model's name, as `/health` and each response report it: its directory's."""
        return self._path.name

    def load(self) -> None:
        """Load the model from its directory; `NotReady` naming what is missing."""
        for name in WHISPER_FILES:
            if not (self._path / name).is_file():
                raise NotReady(
                    f"speech-to-text model file is missing: {self._path / name} "
                    "(models are never downloaded at run time; "
                    "'make server-fetch-models' fetches them)"
                )
        self._model = self._loader(self._path, self._compute_type)

    def warm_up(self) -> None:
        """Transcribe one second of silence."""
        frames = int(SAMPLE_RATE * WARM_UP_SECONDS)
        self._loaded().transcribe(Utterance(bytes(frames * SAMPLE_WIDTH), None))

    def run(self, request: Utterance) -> Transcribed:
        """The model's transcript of one utterance."""
        return self._loaded().transcribe(request)

    def _loaded(self) -> SpeechModel:
        if self._model is None:
            raise NotReady("the speech-to-text model is not loaded")
        return self._model


def decode_wav(audio: bytes) -> tuple[bytes, float]:
    """The frames of a 16 kHz mono 16-bit WAV file and its seconds; `ContractError` else."""
    try:
        with wave.open(io.BytesIO(audio), "rb") as reader:
            rate = reader.getframerate()
            channels = reader.getnchannels()
            width = reader.getsampwidth()
            count = reader.getnframes()
            pcm = reader.readframes(count)
    except (wave.Error, EOFError):
        raise ContractError(
            "SttRequest.audio_base64 is not a WAV file of 16-bit PCM"
        ) from None
    if (rate, channels, width) != (SAMPLE_RATE, CHANNELS, SAMPLE_WIDTH):
        raise ContractError(
            f"the audio is {rate} Hz, {channels} channel(s), {width * 8}-bit: "
            f"it must be {SAMPLE_RATE} Hz, mono, {SAMPLE_WIDTH * 8}-bit PCM"
        )
    frames = len(pcm) // (CHANNELS * SAMPLE_WIDTH)
    return pcm, frames / SAMPLE_RATE


def hotwords(hints: Sequence[str]) -> str | None:
    """The hints as one glossary entry for the model; none when there are none."""
    words = [hint.strip() for hint in hints if hint.strip()]
    return HOTWORDS_FORMAT.format(hints=", ".join(words)) if words else None


def stt_endpoint(
    job: Job[Utterance, Transcribed],
    model: str,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> Endpoint:
    """`POST /v1/stt`: the request's WAV through `job`, as the IS-8 body."""

    def endpoint(body: bytes) -> Response:
        request = SttRequest.from_json(request_json(body))
        pcm, duration = decode_wav(request.audio)
        started = clock()
        heard = job.run(Utterance(pcm, hotwords(request.hints)))
        elapsed = clock() - started
        answer = SttResponse(
            text=heard.text,
            language=heard.language,
            segments=heard.segments,
            duration_seconds=round(duration, 2),
            elapsed_seconds=round(elapsed, 3),
            model=model,
        )
        return json_response(200, answer.to_json())

    return endpoint
