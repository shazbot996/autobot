"""What an agent hears said after its own cue: a recording, transcribed here. No drawing.

The listener transcribes speech in two-second pieces, tuned to find a wake
phrase (`autobot.listener.engines.text_match`), so a question of several
seconds would reach an agent cut into pieces. An agent that wants a whole
sentence records it with the face's own capture and hands the clip to a
`Hearing`, which turns it into text.

`LocalHearing` does so with the same local model the listener uses,
faster-whisper `tiny.en` on the CPU, loaded from `models/` under the models
root and never downloaded (`CN-1`): no audio leaves the machine. It loads the
model once, on first use or on `load`, and both block, so an agent calls them
on a worker thread. The tests give a fake.

`ServerHearing` asks the inference server on `GAMEON1` first, when the
watcher says its `stt` job is ready, and falls back to a `LocalHearing` at
once when the server cannot answer in time (IS-4, IS-8). The recording goes
to the owner's own machine on the house LAN and to no cloud service, with
the hints of IS-16 beside it; the wake word never goes there (IS-15). It is
used only when `[server] stt_enabled` is on, which waits for `CN-1` to be
amended.

Every transcript is a `Heard`: the text and where it came from,
`server:<model>` or `local:tiny.en`, so that what quotes a transcript can
say its source (IS-15).
"""

from __future__ import annotations

import io
import threading
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import numpy as np
import numpy.typing as npt

from autobot.listener.models import FASTER_WHISPER_MODEL_DIR

if TYPE_CHECKING:
    from autobot.listener.engines.text_match import WhisperTranscriber
    from autobot.server.client import ServerClient
    from autobot.server.watch import ServerWatch
    from autobot.tui.audio import Clip

# The rate whisper takes its audio at.
WHISPER_SAMPLE_RATE = 16000
INT16_FULL_SCALE = 32768.0
# Where a transcript came from, as a detail line shows it (IS-15).
LOCAL_SOURCE = "local:tiny.en"
SERVER_SOURCE = "server:{model}"


@dataclass(frozen=True)
class Heard:
    """What was said in a recording, and which model heard it (IS-15)."""

    text: str
    # `local:tiny.en`, or `server:<model>`.
    source: str = LOCAL_SOURCE


class Hearing(Protocol):
    """Local speech-to-text for one recording at a time."""

    def load(self) -> None:
        """Load the model now, so the first `transcribe` does not wait for it."""
        ...

    def transcribe(self, clip: Clip) -> Heard:
        """The words said in `clip`, empty when none were, and their source. It blocks."""
        ...


def whisper_samples(clip: Clip) -> npt.NDArray[np.float32]:
    """`clip` as whisper takes it: mono float32 from -1 to 1, at 16 kHz."""
    samples: npt.NDArray[np.float32] = (
        clip.samples.astype(np.float32) / INT16_FULL_SCALE
    )
    if clip.sample_rate == WHISPER_SAMPLE_RATE or len(samples) == 0:
        return samples
    count = round(len(samples) * WHISPER_SAMPLE_RATE / clip.sample_rate)
    at = np.linspace(0, len(samples) - 1, num=max(1, count))
    resampled: npt.NDArray[np.float32] = np.interp(
        at, np.arange(len(samples)), samples
    ).astype(np.float32)
    return resampled


class LocalHearing:
    """faster-whisper `tiny.en`, from `models/` under `models_root`."""

    def __init__(self, models_root: Path) -> None:
        self._dir = models_root / FASTER_WHISPER_MODEL_DIR
        self._lock = threading.Lock()
        self._model: WhisperTranscriber | None = None

    def _loaded(self) -> WhisperTranscriber:
        if self._model is None:
            from autobot.listener.engines.text_match import WhisperTranscriber

            self._model = WhisperTranscriber(self._dir)
        return self._model

    def load(self) -> None:
        with self._lock:
            self._loaded()

    def transcribe(self, clip: Clip) -> Heard:
        with self._lock:
            # No hint: a question has no phrase to expect.
            text = self._loaded().transcribe(whisper_samples(clip), "").text.strip()
        return Heard(text, LOCAL_SOURCE)


def wav_bytes(clip: Clip) -> bytes:
    """`clip` as the server takes it: a WAV file, 16 kHz mono 16-bit PCM (IS-8)."""
    samples = np.clip(whisper_samples(clip) * INT16_FULL_SCALE, -32768, 32767)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(WHISPER_SAMPLE_RATE)
        writer.writeframes(samples.astype("<i2").tobytes())
    return buffer.getvalue()


class ServerHearing:
    """The server's larger model when it is ready, the NUC's own otherwise (IS-4)."""

    def __init__(
        self,
        client: ServerClient,
        watch: ServerWatch,
        local: Hearing,
        hints: Sequence[str] = (),
    ) -> None:
        self._client = client
        self._watch = watch
        self._local = local
        self._hints = tuple(hints)

    def load(self) -> None:
        # The fallback is kept warm: the server may go away at any time.
        self._local.load()

    def transcribe(self, clip: Clip) -> Heard:
        from autobot.server.client import ServerUnavailable

        if self._watch.ready("stt"):
            try:
                answer = self._client.stt(wav_bytes(clip), clip.seconds, self._hints)
            except ServerUnavailable:
                # No second try: the NUC's own model answers now (IS-4).
                pass
            else:
                return Heard(
                    answer.text.strip(), SERVER_SOURCE.format(model=answer.model)
                )
        return self._local.transcribe(clip)
