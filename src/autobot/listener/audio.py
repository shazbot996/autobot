"""Microphone frames: the configured input as 16 kHz mono int16, 80 ms each.

PortAudio delivers audio on its own thread; `Microphone` queues the frames
there and the service takes them off the queue on the main loop. A slow step
on the main loop (a transcription, the acknowledgement) therefore delays
frames instead of losing them, up to `QUEUE_SECONDS`.

`sounddevice` is imported inside the methods that touch hardware, so that
importing this module never loads PortAudio.
"""

from __future__ import annotations

import contextlib
import queue
import socket
from types import TracebackType
from typing import Any, Self

from autobot.audio_check import AudioCheckError, check_host, resolve_device
from autobot.config import AudioConfig, Config
from autobot.listener.detector import Frame

# Frames held while the main loop is busy; older audio than this is dropped.
QUEUE_SECONDS = 20


class AudioError(Exception):
    """The audio hardware cannot be used. The message is one line."""


class WrongHostError(AudioError):
    """This machine is not the NUC. The message is one line."""


def require_nuc(config: Config, what: str) -> None:
    """Raise `WrongHostError` unless this machine is the configured NUC.

    The same hostname check as `make audio-check` (plan section 9 item 6):
    off the NUC nothing that opens audio hardware runs.
    """
    hostname = socket.gethostname()
    try:
        check_host(config, hostname)
    except AudioCheckError:
        raise WrongHostError(
            f"{what} runs on the NUC only; this host is {hostname}"
        ) from None


class Microphone:
    """The configured input device as a stream of frames. A context manager."""

    def __init__(self, audio: AudioConfig):
        self._audio = audio
        frames_per_second = max(1, 1000 // audio.frame_ms)
        self._queue: queue.Queue[Frame] = queue.Queue(
            maxsize=QUEUE_SECONDS * frames_per_second
        )
        self._stream: Any = None
        self.device_name = ""
        # Frames dropped because the queue was full, and PortAudio overflows.
        self.dropped = 0
        self.overflows = 0

    def __enter__(self) -> Self:
        import sounddevice as sd

        try:
            devices = list(sd.query_devices())
            index = resolve_device(self._audio.input_device, "input", devices)
            self.device_name = str(devices[index]["name"])
            self._stream = sd.InputStream(
                samplerate=self._audio.sample_rate,
                blocksize=self._audio.frame_samples,
                device=index,
                channels=1,
                dtype="int16",
                callback=self._on_audio,
            )
            self._stream.start()
        except AudioCheckError as error:
            raise AudioError(str(error)) from None
        except sd.PortAudioError as error:
            raise AudioError(f"cannot open the input device: {error}") from None
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._stream is not None:
            # Closing must never mask the error that got us here.
            with contextlib.suppress(Exception):
                self._stream.stop()
                self._stream.close()
            self._stream = None

    def _on_audio(self, indata: Any, frames: int, time: Any, status: Any) -> None:
        # Runs on PortAudio's thread: copy, queue, return.
        if status:
            self.overflows += 1
        if frames != self._audio.frame_samples:
            return
        try:
            self._queue.put_nowait(indata[:, 0].copy())
        except queue.Full:
            self.dropped += 1

    def read(self, timeout: float) -> Frame | None:
        """The next frame, or None when none arrives within `timeout` seconds."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def discard(self) -> int:
        """Drop every queued frame; return how many were dropped."""
        count = 0
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return count
            count += 1
