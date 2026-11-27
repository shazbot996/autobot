"""`autobot_server.stt`: `POST /v1/stt` on a fake speech model (IS-6, IS-7, IS-8, IS-16)."""

from __future__ import annotations

import base64
import io
import json
import os
import wave
from pathlib import Path

import pytest

from autobot_server.app import Response
from autobot_server.config import checkout_root
from autobot_server.contract import Segment, SttRequest, SttResponse, contract_dir
from autobot_server.job import NOT_READY, READY, Job
from autobot_server.stt import (
    _OFFLINE_ENV,
    SAMPLE_RATE,
    STT_PATH,
    WHISPER_FILES,
    Transcribed,
    Utterance,
    WhisperJob,
    decode_wav,
    enforce_offline,
    hotwords,
    stt_endpoint,
)
from tests.server_fakes import BEARER, FakeClock, make_app

FIXTURE = Path("tests/fixtures/audio/hey_autobot.wav")
HEARD = Transcribed(
    text="Hey autobot.",
    language="en",
    segments=(Segment(start_seconds=0.0, end_seconds=1.8, text="Hey autobot."),),
)


class FakeSpeechModel:
    """A speech model that hears `HEARD` and keeps what it was given."""

    def __init__(self, heard: Transcribed = HEARD):
        self.heard = heard
        self.utterances: list[Utterance] = []
        self.error: Exception | None = None

    def transcribe(self, utterance: Utterance) -> Transcribed:
        self.utterances.append(utterance)
        if self.error is not None:
            error, self.error = self.error, None
            raise error
        return self.heard


class FakeLoader:
    """Hands out one `FakeSpeechModel`, and records what it was asked to load."""

    def __init__(self) -> None:
        self.model = FakeSpeechModel()
        self.loads: list[tuple[Path, str]] = []

    def __call__(self, path: Path, compute_type: str) -> FakeSpeechModel:
        self.loads.append((path, compute_type))
        return self.model


def model_dir(root: Path, files: tuple[str, ...] = WHISPER_FILES) -> Path:
    """A stand-in model directory named `large-v3-turbo`, holding `files`."""
    path = root / "large-v3-turbo"
    path.mkdir()
    for name in files:
        (path / name).write_bytes(b"")
    return path


def stt_job(
    tmp_path: Path, loader: FakeLoader
) -> tuple[Job[Utterance, Transcribed], WhisperJob]:
    engine = WhisperJob(model_dir(tmp_path), "int8_float16", loader=loader)
    job: Job[Utterance, Transcribed] = Job("stt", engine, clock=FakeClock())
    job.start()
    return job, engine


def wav(rate: int = SAMPLE_RATE, channels: int = 1, width: int = 2) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(width)
        writer.setframerate(rate)
        # One second of silence.
        writer.writeframes(bytes(rate * channels * width))
    return buffer.getvalue()


def request_body(audio: bytes, hints: tuple[str, ...] = ()) -> bytes:
    return json.dumps(SttRequest.from_wav(audio, hints).to_json()).encode("utf-8")


def body_of(response: Response) -> dict[str, object]:
    assert isinstance(response.body, bytes)
    data = json.loads(response.body)
    assert isinstance(data, dict)
    return data


def shape_of(value: object) -> object:
    """The keys and value types of a JSON value, without the values."""
    if isinstance(value, dict):
        return {key: shape_of(item) for key, item in value.items()}
    if isinstance(value, list):
        return [shape_of(item) for item in value[:1]]
    if isinstance(value, int | float) and not isinstance(value, bool):
        return "number"
    return type(value).__name__


def post(tmp_path: Path, body: bytes) -> tuple[Response, FakeLoader]:
    loader = FakeLoader()
    job, engine = stt_job(tmp_path, loader)
    app = make_app(endpoints={STT_PATH: stt_endpoint(job, engine.model)})
    return app.handle("POST", STT_PATH, BEARER, body), loader


def test_the_fixture_is_transcribed_into_the_contract_shape(tmp_path: Path) -> None:
    audio = (checkout_root() / FIXTURE).read_bytes()
    response, loader = post(tmp_path, request_body(audio, ("autobot",)))
    assert response.status == 200
    answer = body_of(response)
    example = json.loads(
        (contract_dir() / "stt-response.json").read_text(encoding="utf-8")
    )
    assert shape_of(answer) == shape_of(example)
    decoded = SttResponse.from_json(answer)
    assert decoded.text == HEARD.text
    assert decoded.segments == HEARD.segments
    assert decoded.model == "large-v3-turbo"
    # The fixture's own length, from its frames: 59864 at 16 kHz.
    assert decoded.duration_seconds == pytest.approx(3.74)
    # The model got the fixture's frames, and only one utterance beside the warm-up.
    assert len(loader.model.utterances) == 2
    with wave.open(io.BytesIO(audio), "rb") as reader:
        assert loader.model.utterances[1].pcm == reader.readframes(reader.getnframes())


def test_the_contract_example_request_is_answered(tmp_path: Path) -> None:
    body = (contract_dir() / "stt-request.json").read_bytes()
    response, loader = post(tmp_path, body)
    assert response.status == 200
    assert loader.model.utterances[1].hotwords is not None
    assert loader.model.utterances[1].hotwords.startswith("Glossary: autobot, ")


@pytest.mark.parametrize(
    ("audio", "said"),
    [
        (wav(rate=8000), "8000 Hz"),
        (wav(rate=44100), "44100 Hz"),
        (wav(channels=2), "2 channel(s)"),
        (wav(width=1), "8-bit"),
        (b"not a wav file at all", "not a WAV file"),
    ],
)
def test_audio_that_is_not_16_khz_mono_16_bit_is_400(
    tmp_path: Path, audio: bytes, said: str
) -> None:
    response, loader = post(tmp_path, request_body(audio))
    assert response.status == 400
    assert said in str(body_of(response)["error"])
    # Only the warm-up reached the model.
    assert len(loader.model.utterances) == 1


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b'{"hints": []}',
        b'{"audio_base64": "%%%"}',
        b'{"audio_base64": "", "hints": "autobot"}',
    ],
)
def test_a_body_that_is_not_the_contract_is_400(tmp_path: Path, body: bytes) -> None:
    response, _loader = post(tmp_path, body)
    assert response.status == 400


def test_the_hints_are_one_glossary_entry() -> None:
    assert hotwords(("autobot", " weather ", "", "yes")) == (
        "Glossary: autobot, weather, yes."
    )
    assert hotwords(()) is None
    assert hotwords(("  ",)) is None


def test_no_hints_means_no_hotwords(tmp_path: Path) -> None:
    _response, loader = post(tmp_path, request_body(wav()))
    assert loader.model.utterances[1].hotwords is None


def test_the_model_is_loaded_from_its_directory_in_the_configured_type(
    tmp_path: Path,
) -> None:
    loader = FakeLoader()
    job, engine = stt_job(tmp_path, loader)
    assert job.state == READY
    assert loader.loads == [(tmp_path / "large-v3-turbo", "int8_float16")]
    assert engine.model == "large-v3-turbo"
    # The warm-up is one second of silence with no hint.
    warm = loader.model.utterances[0]
    assert warm.pcm == bytes(SAMPLE_RATE * 2)
    assert warm.hotwords is None


@pytest.mark.parametrize("missing", WHISPER_FILES)
def test_a_missing_model_file_is_a_reason_not_a_fetch(
    tmp_path: Path, missing: str
) -> None:
    loader = FakeLoader()
    files = tuple(name for name in WHISPER_FILES if name != missing)
    engine = WhisperJob(model_dir(tmp_path, files), "float16", loader=loader)
    job: Job[Utterance, Transcribed] = Job("stt", engine, clock=FakeClock())
    job.start()
    assert job.state == NOT_READY
    assert missing in job.reason
    assert "make server-fetch-models" in job.reason
    assert loader.loads == []


def test_a_missing_model_directory_leaves_stt_not_ready(tmp_path: Path) -> None:
    engine = WhisperJob(tmp_path / "absent", "float16", loader=FakeLoader())
    job: Job[Utterance, Transcribed] = Job("stt", engine, clock=FakeClock())
    job.start()
    app = make_app(endpoints={STT_PATH: stt_endpoint(job, engine.model)})
    response = app.handle("POST", STT_PATH, BEARER, request_body(wav()))
    assert response.status == 503
    assert "not ready" in str(body_of(response)["error"])


def test_losing_the_gpu_mid_request_is_503_and_not_ready(tmp_path: Path) -> None:
    loader = FakeLoader()
    job, engine = stt_job(tmp_path, loader)
    loader.model.error = RuntimeError("CUDA failed with error out of memory")
    app = make_app(endpoints={STT_PATH: stt_endpoint(job, engine.model)})
    response = app.handle("POST", STT_PATH, BEARER, request_body(wav()))
    assert response.status == 503
    assert job.state == NOT_READY
    assert "out of memory" in job.reason


def test_the_time_taken_is_the_transcription_s(tmp_path: Path) -> None:
    clock = FakeClock(100.0)

    class SlowModel(FakeSpeechModel):
        def transcribe(self, utterance: Utterance) -> Transcribed:
            clock.advance(0.25)
            return super().transcribe(utterance)

    model = SlowModel()
    engine = WhisperJob(
        model_dir(tmp_path), "int8_float16", loader=lambda path, kind: model
    )
    job: Job[Utterance, Transcribed] = Job("stt", engine, clock=FakeClock())
    job.start()
    app = make_app(endpoints={STT_PATH: stt_endpoint(job, engine.model, clock=clock)})
    response = app.handle("POST", STT_PATH, BEARER, request_body(wav()))
    answer = SttResponse.from_json(body_of(response))
    assert answer.elapsed_seconds == pytest.approx(0.25)
    assert answer.duration_seconds == pytest.approx(1.0)


def test_nothing_is_written(tmp_path: Path) -> None:
    before = sorted(tmp_path.rglob("*"))
    _response, _loader = post(tmp_path, request_body(wav(), ("autobot",)))
    # Only the stand-in model directory the test made is there.
    after = sorted(tmp_path.rglob("*"))
    assert after == sorted(
        [tmp_path / "large-v3-turbo"]
        + [tmp_path / "large-v3-turbo" / name for name in WHISPER_FILES]
    )
    assert before == []


def test_decode_wav_gives_the_frames_and_their_seconds() -> None:
    pcm, seconds = decode_wav(wav())
    assert len(pcm) == SAMPLE_RATE * 2
    assert seconds == pytest.approx(1.0)


def test_the_offline_environment_is_the_nucs(monkeypatch: pytest.MonkeyPatch) -> None:
    from autobot.listener.models import _OFFLINE_ENV as NUC_OFFLINE_ENV

    assert _OFFLINE_ENV == NUC_OFFLINE_ENV
    for name in _OFFLINE_ENV:
        monkeypatch.delenv(name, raising=False)
    enforce_offline()
    for name, value in _OFFLINE_ENV.items():
        assert os.environ[name] == value


def test_faster_whisper_is_imported_only_after_the_offline_environment() -> None:
    source = (
        checkout_root() / "server" / "src" / "autobot_server" / "stt.py"
    ).read_text(encoding="utf-8")
    top, _, rest = source.partition("class Whisper:")
    assert "faster_whisper" not in top.replace("faster-whisper", "")
    assert rest.index("enforce_offline()") < rest.index("from faster_whisper import")
    assert "local_files_only=True" in rest


def test_the_request_carries_the_audio_as_base64() -> None:
    audio = wav()
    request = SttRequest.from_wav(audio, ("autobot",))
    assert base64.b64decode(request.audio_base64) == audio
