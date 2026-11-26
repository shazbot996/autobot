"""The listener service: `python -m autobot.listener`.

Main loop: load the configuration, build one detector per `[[wake_phrases]]`
entry, read the microphone, gate the frames through the VAD, feed the
detectors, publish every `WakeEvent`, and speak the acknowledgement.

`Pipeline` is the frame path, gate then detectors, on its own. The service
and the fixture tests both drive detectors through `Pipeline.process`, so the
tests exercise the path the microphone takes.

The process never opens a network connection (CONSTRAINTS.md CN-1, CN-2): all
models are local files and the offline environment is set before any speech
library is imported.

Exit status: 0 after SIGTERM or SIGINT; 1 for a fault that may pass (the audio
device); 2 for one that will not (configuration, a missing model, the wrong
host, a listener already running). The systemd unit restarts on 1, not on 2.

Set `AUTOBOT_LISTENER_DEBUG=1` to log what the detectors heard. It is off by
default: the log would otherwise hold a transcript of the room.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
import time
from collections.abc import Sequence
from datetime import datetime
from types import FrameType

from autobot.config import Config, ConfigError, load_config, resolve_repo_path
from autobot.listener.ack import Acknowledgement, render
from autobot.listener.audio import AudioError, Microphone, WrongHostError, require_nuc
from autobot.listener.detector import (
    Detector,
    Frame,
    UnknownKindError,
    WakeEvent,
    build_detector,
)
from autobot.listener.events import SOCKET_PATH, AlreadyRunningError, EventPublisher
from autobot.listener.models import ModelMissingError, enforce_offline
from autobot.listener.vad import SileroVad, SpeechGate, VadError

EXIT_OK = 0
EXIT_TRANSIENT = 1
EXIT_PERMANENT = 2

TARGET_ACK = "ack"
DEBUG_ENV_VAR = "AUTOBOT_LISTENER_DEBUG"
# The line `make up` waits for.
READY_MESSAGE = "listening"

READ_TIMEOUT_SECONDS = 0.5
# No audio at all for this long means the input device has gone.
STALLED_SECONDS = 10.0
# Audio is dropped for this long after the acknowledgement ends, so its echo
# in the room is not heard as speech.
ACK_TAIL_SECONDS = 0.25


def log(message: str) -> None:
    """One timestamped line on stdout, which is the listener's log."""
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    print(f"{stamp} listener: {message}", flush=True)


class Pipeline:
    """The frame path: the speech gate, then every detector.

    One VAD scores each frame once. A detector that needs wider margins than
    the gate's (a `gate_margins` attribute: see `autobot.listener.detector`)
    is fed through a gate of its own with those margins, driven by the same
    score. `gate` is the gate the first detector is fed through.
    """

    def __init__(self, gate: SpeechGate, detectors: Sequence[Detector]):
        self.detectors = tuple(detectors)
        # The gate that owns the VAD.
        self._source = gate
        default = (gate.pre_roll_frames, gate.hangover_frames)
        by_margins: dict[tuple[int, int], tuple[SpeechGate, list[Detector]]] = {}
        for detector in self.detectors:
            margins: tuple[int, int] = (
                getattr(detector, "gate_margins", None) or default
            )
            if margins not in by_margins:
                own = gate if margins == default else gate.with_margins(*margins)
                by_margins[margins] = (own, [])
            by_margins[margins][1].append(detector)
        self._routes = tuple(by_margins.values())
        self.gate = self._routes[0][0] if self._routes else gate
        # Counters for the log: frames seen, frames let through, stretches ended.
        self.frames = 0
        self.voiced_frames = 0
        self.stretches = 0

    def process(self, frame: Frame) -> list[WakeEvent]:
        """Take one microphone frame; return the wake events it completed."""
        probability = self._source.probability(frame)
        self.frames += 1
        events: list[WakeEvent] = []
        for gate, detectors in self._routes:
            result = gate.push_scored(frame, probability)
            if gate is self.gate:
                self.voiced_frames += len(result.frames)
                self.stretches += result.speech_ended
            for voiced in result.frames:
                for detector in detectors:
                    event = detector.feed(voiced)
                    if event is not None:
                        events.append(event)
            if result.speech_ended:
                for detector in detectors:
                    event = detector.end_of_speech()
                    if event is not None:
                        events.append(event)
        return events

    def reset(self) -> None:
        """Forget everything buffered: audio was dropped."""
        self._source.reset()
        for gate, _detectors in self._routes:
            gate.reset()
        for detector in self.detectors:
            detector.reset()


def build_pipeline(config: Config) -> Pipeline:
    """The gate and one detector per configured wake phrase."""
    gate = SpeechGate(SileroVad(config.audio.sample_rate))
    detectors = [build_detector(phrase, config.audio) for phrase in config.wake_phrases]
    return Pipeline(gate, detectors)


def run(config: Config, stop: threading.Event) -> None:
    """Run until `stop` is set. Opens the microphone, the speaker and the socket."""
    debug = bool(os.environ.get(DEBUG_ENV_VAR))
    pipeline = build_pipeline(config)
    needs_ack = any(phrase.target == TARGET_ACK for phrase in config.wake_phrases)
    ack = (
        Acknowledgement(render(config.ack), config.audio.output_device)
        if needs_ack
        else None
    )
    phrases = ", ".join(
        f"{phrase.phrase!r} ({phrase.kind})" for phrase in config.wake_phrases
    )
    with (
        EventPublisher(resolve_repo_path(SOCKET_PATH)) as publisher,
        Microphone(config.audio) as microphone,
    ):
        log(
            f"{READY_MESSAGE} on {microphone.device_name!r} for {phrases}; "
            f"events on {SOCKET_PATH}"
        )
        last_audio = time.monotonic()
        was_in_speech = False
        while not stop.is_set():
            publisher.poll()
            frame = microphone.read(READ_TIMEOUT_SECONDS)
            if frame is None:
                if time.monotonic() - last_audio > STALLED_SECONDS:
                    raise AudioError(
                        f"no audio from the input device for {STALLED_SECONDS:g} s"
                    )
                continue
            last_audio = time.monotonic()
            events = pipeline.process(frame)
            if debug:
                _log_debug(pipeline, was_in_speech)
                was_in_speech = pipeline.gate.in_speech
            for event in events:
                publisher.publish(event)
            if ack is not None and any(event.target == TARGET_ACK for event in events):
                _acknowledge(ack, microphone, pipeline)
        voiced = 100.0 * pipeline.voiced_frames / max(1, pipeline.frames)
        log(
            f"heard {pipeline.stretches} stretches of speech; "
            f"{voiced:.0f}% of the audio reached the detectors"
        )
        if microphone.dropped or microphone.overflows:
            log(
                f"audio: {microphone.dropped} frames dropped, "
                f"{microphone.overflows} input overflows"
            )


def _acknowledge(
    ack: Acknowledgement, microphone: Microphone, pipeline: Pipeline
) -> None:
    """Speak, with the detectors muted: nothing captured meanwhile is kept."""
    try:
        ack.play()
    except AudioError as error:
        log(f"ack failed: {error}")
    else:
        log("ack spoken")
    time.sleep(ACK_TAIL_SECONDS)
    microphone.discard()
    pipeline.reset()


def _log_debug(pipeline: Pipeline, was_in_speech: bool) -> None:
    in_speech = pipeline.gate.in_speech
    if in_speech and not was_in_speech:
        log("debug: speech started")
    if was_in_speech and not in_speech:
        heard = [
            f"{transcript.text!r} ({transcript.score:.2f})"
            for detector in pipeline.detectors
            if (transcript := getattr(detector, "last_transcript", None)) is not None
        ]
        # A model-scored detector has no transcript: log its highest score.
        heard += [
            f"peak score {peak:.2f}"
            for detector in pipeline.detectors
            if (peak := getattr(detector, "last_peak", None)) is not None
        ]
        log(f"debug: speech ended; heard {', '.join(heard) or 'nothing'}")


def main() -> int:
    enforce_offline()
    stop = threading.Event()

    def on_signal(signum: int, frame: FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    try:
        config = load_config()
        require_nuc(config, "the listener")
        run(config, stop)
    except (
        AlreadyRunningError,
        ConfigError,
        ModelMissingError,
        UnknownKindError,
        VadError,
        WrongHostError,
    ) as error:
        print(error, file=sys.stderr, flush=True)
        return EXIT_PERMANENT
    except AudioError as error:
        # A device may come back; the unit restarts the listener.
        print(error, file=sys.stderr, flush=True)
        return EXIT_TRANSIENT
    log("stopped")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
