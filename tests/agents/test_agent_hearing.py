"""What an agent hears after its own cue: a recording as whisper takes it. No model loaded."""

import io
import wave
from pathlib import Path

import numpy as np
import pytest

from agents.agent_fakes import FakeHearing
from autobot.agents.hearing import (
    LOCAL_SOURCE,
    WHISPER_SAMPLE_RATE,
    Heard,
    LocalHearing,
    ServerHearing,
    wav_bytes,
    whisper_samples,
)
from autobot.listener.models import ModelMissingError
from autobot.server.contract import SttRequest
from autobot.server.watch import DEGRADED, DOWN
from autobot.tui.audio import Clip
from server.server_fakes import FakeTransport, FakeWatch, client_on


def test_a_clip_at_16_khz_is_scaled_to_minus_one_to_one() -> None:
    clip = Clip(np.array([0, 16384, -32768], dtype=np.int16), WHISPER_SAMPLE_RATE)
    samples = whisper_samples(clip)
    assert samples.dtype == np.float32
    assert samples.tolist() == [0.0, 0.5, -1.0]


def test_a_clip_at_another_rate_is_resampled_to_16_khz() -> None:
    clip = Clip(np.zeros(48000, dtype=np.int16), 48000)
    assert len(whisper_samples(clip)) == WHISPER_SAMPLE_RATE


def test_a_missing_model_says_which_file_and_downloads_nothing(tmp_path: Path) -> None:
    hearing = LocalHearing(tmp_path)
    with pytest.raises(ModelMissingError, match="faster-whisper"):
        hearing.load()


# --- the server's model first, the NUC's own when it cannot answer (IS-4, IS-15) ---

CLIP = Clip(np.full(32000, 1000, dtype=np.int16), WHISPER_SAMPLE_RATE)
HINTS = ("autobot", "weather")


def test_a_clip_goes_to_the_server_as_a_16_khz_mono_16_bit_wav() -> None:
    clip = Clip(np.full(48000, 1000, dtype=np.int16), 48000)
    with wave.open(io.BytesIO(wav_bytes(clip)), "rb") as reader:
        assert (
            reader.getframerate(),
            reader.getnchannels(),
            reader.getsampwidth(),
            reader.getnframes(),
        ) == (16000, 1, 2, 16000)
        samples = np.frombuffer(reader.readframes(16000), dtype="<i2")
    assert int(samples[0]) == 1000 and int(samples[-1]) == 1000


def test_the_server_transcribes_when_its_stt_job_is_ready(tmp_path: Path) -> None:
    transport, local = FakeTransport(), FakeHearing(text="from the nuc")
    hearing = ServerHearing(client_on(tmp_path, transport), FakeWatch(), local, HINTS)
    heard = hearing.transcribe(CLIP)
    assert heard == Heard(
        "Will I need an umbrella this afternoon?", "server:large-v3-turbo"
    )
    assert local.heard == []
    (sent,) = transport.sent
    # 0.5 s plus 0.25 s for each of the clip's two seconds.
    assert (sent.path, sent.timeout) == ("/v1/stt", 1.0)
    request = SttRequest.from_json(sent.body)
    assert request.hints == HINTS
    assert request.audio == wav_bytes(CLIP)


def test_a_server_that_times_out_is_answered_from_the_local_hearing(
    tmp_path: Path,
) -> None:
    transport = FakeTransport(error=TimeoutError("timed out"))
    local = FakeHearing(text="from the nuc")
    hearing = ServerHearing(client_on(tmp_path, transport), FakeWatch(), local, HINTS)
    heard = hearing.transcribe(CLIP)
    assert heard == Heard("from the nuc", LOCAL_SOURCE)
    assert heard.source == "local:tiny.en"
    # The request was made once and not tried again.
    assert len(transport.sent) == 1 and local.heard == [2.0]


def test_the_server_is_not_asked_while_it_is_down_or_its_stt_is_not_ready(
    tmp_path: Path,
) -> None:
    transport, local = FakeTransport(), FakeHearing(text="from the nuc")
    watch = FakeWatch(DOWN)
    hearing = ServerHearing(client_on(tmp_path, transport), watch, local)
    assert hearing.transcribe(CLIP).source == LOCAL_SOURCE
    watch.set(DEGRADED, stt="speech-to-text model file is missing")
    assert hearing.transcribe(CLIP).source == LOCAL_SOURCE
    assert transport.sent == []
    # The fallback is what is loaded ahead of time.
    hearing.load()
    assert local.loads == 1
