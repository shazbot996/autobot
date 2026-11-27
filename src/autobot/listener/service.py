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

**Stretches and their scores.** A *stretch* is one stretch of speech as the
pipeline's stretch gate lets it through: the pre-roll frames from just before
the speech, the speech, and the quiet frames that end it. The stretch gate is
the gate with the longest pre-roll and the longest hangover any detector asks
for (`Pipeline.stretch_gate`). With one detector kind configured it is that
kind's own gate: 3 frames before and 6 after for `text_match`, 12 and 10 for
`openwakeword`. With both kinds configured it is the `openwakeword` gate,
whose stretches contain the shorter ones the `text_match` detector is fed.
Each stretch gets the next identifier, a counter that starts at 1 with each
listener run, and one `ScoreEvent` per detector (`autobot.listener.events`
gives the line and what `score` is). A stretch is scored when it ends, or
just before the first wake event inside it, whichever comes first, so its
score events are always published before a wake it causes. A wake whose
acknowledgement is spoken ends its stretch there: the audio captured while
the listener speaks is dropped, and speech that goes on afterwards is a new
stretch.

**Transcripts.** Each transcription a detector makes (a `text_match`
detector's `last_transcript`) is published as a `TranscriptEvent` on the
socket, stamped with the identifier of the stretch it belongs to, in the
frame it is made and before that frame's score and wake events. It never
reaches the log; only the debug switch above logs what was heard.

**What is kept in memory, and the snapshot.** For the live monitor (studio
spec rule TS-9) `RecentAudio` keeps the audio of the last five scored
stretches, each with its pre-roll (at most the last `MAX_STRETCH_SECONDS` of
a stretch longer than that), and the last ten seconds of microphone frames
(125 of them), as int16 samples and nothing else. Neither is ever written
anywhere by the listener on its own. `python -m autobot.listener.control
snapshot` asks for one of them:

1. It writes `run/snapshot.request`, holding a stretch identifier or the word
   `buffer`, and sends the listener `SIGUSR1`.
2. The signal only sets a flag. The main loop sees it before the next frame
   and writes the audio asked for as `run/snapshot.wav` (16 kHz mono 16-bit,
   mode 0600) through a temporary name and `os.replace`, then logs
   "snapshot written". When it cannot (the stretch is no longer among the
   five held, nothing has been heard yet, the request is unreadable) it
   writes the one-line reason to `run/snapshot.error` the same way and logs
   "snapshot refused". Either way it removes the request file.

The three `run/snapshot.*` files are removed when the listener starts and
when it stops.

**The hold.** Another process that is about to play sound (the face, before
it speaks or plays a recording back) can make the listener stop listening
for a while, so it does not hear that sound as speech.
`python -m autobot.listener.control hold --seconds S` asks for it:

1. It writes `run/hold.request`, holding S (more than 0, at most
   `MAX_HOLD_SECONDS`), and sends the listener `SIGUSR2`.
2. The signal only sets a flag. Before the next frame the main loop reads S,
   holds until S + `ACK_TAIL_SECONDS` from now, logs "hold: S s", and removes
   the request file. An unreadable or out-of-range request is logged as
   "hold refused" and leaves no hold. A new request extends the hold and
   never shortens it.
3. While the hold lasts, frames are read and dropped: nothing is scored or
   published. When it ends, the listener does what it does after its own
   acknowledgement: it discards what the microphone queued, resets the
   pipeline, and ends the open stretch.

The listener plays its own acknowledgement before it looks at the next
request, so a hold asked for just after a wake starts once that has been
spoken. `run/hold.request` is removed when the listener starts and when it
stops.
"""

from __future__ import annotations

import contextlib
import os
import signal
import sys
import threading
import time
import wave
from collections import deque
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import FrameType

import numpy as np

from autobot.config import Config, ConfigError, load_config, resolve_repo_path
from autobot.listener.ack import Acknowledgement, render
from autobot.listener.audio import AudioError, Microphone, WrongHostError, require_nuc
from autobot.listener.detector import (
    Clock,
    Detector,
    Frame,
    UnknownKindError,
    WakeEvent,
    build_detector,
    utc_now,
)
from autobot.listener.events import (
    SOCKET_PATH,
    AlreadyRunningError,
    Event,
    EventPublisher,
    ScoreEvent,
    TranscriptEvent,
)
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

# How many scored stretches are held, and how much microphone audio.
RECENT_STRETCHES = 5
BUFFER_SECONDS = 10.0
# A held stretch keeps at most its last this many seconds: speech that never
# pauses (a radio) must not grow in memory without bound.
MAX_STRETCH_SECONDS = 30.0

# The snapshot hand-off with `autobot.listener.control snapshot`.
SNAPSHOT_REQUEST = Path("run/snapshot.request")
SNAPSHOT_FILE = Path("run/snapshot.wav")
SNAPSHOT_ERROR = Path("run/snapshot.error")
# What the request file holds to ask for the rolling buffer.
SNAPSHOT_BUFFER = "buffer"
SNAPSHOT_SAMPLE_WIDTH = 2

# The hold hand-off with `autobot.listener.control hold`.
HOLD_REQUEST = Path("run/hold.request")
# The longest hold one request may ask for: a faulty request must not leave
# the listener deaf for long.
MAX_HOLD_SECONDS = 60.0


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

    `stretch_gate` is the gate whose stretches are numbered, scored and kept
    for the live monitor: the one with the longest pre-roll and the longest
    hangover any detector asks for, so that its stretches contain every
    detector's. It is one of the detectors' gates when one has both; else a
    gate of its own. After each `process`, `stretch_frames` holds the frames
    that gate released for the microphone frame, `stretch_ended` says whether
    the frame ended its stretch, and `stretch_scores` gives each detector's
    score for the stretch. `transcripts` holds the `(phrase, kind, text)` of
    each transcription a detector made during the frame, in the order they
    were made; a transcription with no text is left out.

    `names` is the `(phrase, kind)` of each detector, in order, for the score
    events; without it the detectors' own `phrase` and `kind` attributes are
    used, where they have them.
    """

    def __init__(
        self,
        gate: SpeechGate,
        detectors: Sequence[Detector],
        names: Sequence[tuple[str, str]] | None = None,
    ):
        self.detectors = tuple(detectors)
        self.names: tuple[tuple[str, str], ...] = (
            tuple(names)
            if names is not None
            else tuple(
                (
                    str(getattr(detector, "phrase", "")),
                    str(getattr(detector, "kind", "")),
                )
                for detector in self.detectors
            )
        )
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
        widest = (
            max((pre_roll for pre_roll, _hangover in by_margins), default=default[0]),
            max((hangover for _pre_roll, hangover in by_margins), default=default[1]),
        )
        self._stretch_gate_is_a_route = widest in by_margins
        self.stretch_gate = (
            by_margins[widest][0]
            if self._stretch_gate_is_a_route
            else gate.with_margins(*widest)
        )
        self.stretch_frames: tuple[Frame, ...] = ()
        self.stretch_ended = False
        self._stretch_open = False
        self.transcripts: list[tuple[str, str, str]] = []
        # Per detector (by `id`): its name, and the transcript it held when
        # last checked, so that a new transcription is seen once.
        self._names_by_id = {
            id(detector): name
            for detector, name in zip(self.detectors, self.names, strict=True)
        }
        self._transcripts_seen: dict[int, object] = {
            id(detector): getattr(detector, "last_transcript", None)
            for detector in self.detectors
        }
        # Per detector (by `id`), for the open stretch: the transcript it held
        # when the stretch opened, whether its own gate has ended since, and
        # the score it first fired at.
        self._transcripts_before: dict[int, object] = {}
        self._ended: set[int] = set()
        self._fired: dict[int, float] = {}
        # Counters for the log: frames seen, frames let through, stretches ended.
        self.frames = 0
        self.voiced_frames = 0
        self.stretches = 0

    def process(self, frame: Frame) -> list[WakeEvent]:
        """Take one microphone frame; return the wake events it completed."""
        probability = self._source.probability(frame)
        self.frames += 1
        self.transcripts = []
        # Every gate first: the stretch's bookkeeping must be in place before
        # a detector hears the frame.
        results = [gate.push_scored(frame, probability) for gate, _ in self._routes]
        stretch = (
            next(
                result
                for (gate, _), result in zip(self._routes, results, strict=True)
                if gate is self.stretch_gate
            )
            if self._stretch_gate_is_a_route
            else self.stretch_gate.push_scored(frame, probability)
        )
        if stretch.frames and not self._stretch_open:
            self._stretch_open = True
            self._transcripts_before = {
                id(detector): getattr(detector, "last_transcript", None)
                for detector in self.detectors
            }
            self._ended = set()
            self._fired = {}
        self.stretch_frames = stretch.frames
        self.stretch_ended = stretch.speech_ended
        events: list[WakeEvent] = []
        for (gate, detectors), result in zip(self._routes, results, strict=True):
            if gate is self.gate:
                self.voiced_frames += len(result.frames)
                self.stretches += result.speech_ended
            for voiced in result.frames:
                for detector in detectors:
                    event = detector.feed(voiced)
                    self._note_transcript(detector)
                    if event is not None:
                        events.append(event)
                        self._fired.setdefault(id(detector), event.score)
            if result.speech_ended:
                for detector in detectors:
                    event = detector.end_of_speech()
                    self._note_transcript(detector)
                    self._ended.add(id(detector))
                    if event is not None:
                        events.append(event)
                        self._fired.setdefault(id(detector), event.score)
        if stretch.speech_ended:
            self._stretch_open = False
        return events

    def _note_transcript(self, detector: Detector) -> None:
        """Record the detector's transcription, if it made one since last checked."""
        key = id(detector)
        transcript = getattr(detector, "last_transcript", None)
        if transcript is None or transcript is self._transcripts_seen.get(key):
            return
        self._transcripts_seen[key] = transcript
        text = str(getattr(transcript, "text", ""))
        if text.strip():
            phrase, kind = self._names_by_id[key]
            self.transcripts.append((phrase, kind, text))

    def stretch_scores(self) -> list[float | None]:
        """Each detector's score for the stretch open now, or the one that last ended.

        In the order of `detectors`. A transcribing detector gives the
        confidence of the last transcript it made in the stretch
        (`last_transcript.score`); a model-scored one the highest score of the
        stretch (`last_peak`) once its gate has ended, and before that the
        score it fired at. None when the detector has none of these for the
        stretch.
        """
        scores: list[float | None] = []
        for detector in self.detectors:
            key = id(detector)
            transcript = getattr(detector, "last_transcript", None)
            peak = getattr(detector, "last_peak", None)
            score: float | None = None
            if (
                transcript is not None
                and transcript is not self._transcripts_before.get(key)
            ):
                score = float(transcript.score)
            elif peak is not None and key in self._ended:
                score = float(peak)
            elif key in self._fired:
                score = self._fired[key]
            scores.append(None if score is None else round(score, 3))
        return scores

    def reset(self) -> None:
        """Forget everything buffered: audio was dropped."""
        self._source.reset()
        for gate, _detectors in self._routes:
            gate.reset()
        if not self._stretch_gate_is_a_route:
            self.stretch_gate.reset()
        for detector in self.detectors:
            detector.reset()
        self.stretch_frames = ()
        self.stretch_ended = False
        self._stretch_open = False
        self.transcripts = []


def build_pipeline(config: Config) -> Pipeline:
    """The gate and one detector per configured wake phrase."""
    gate = SpeechGate(SileroVad(config.audio.sample_rate))
    detectors = [build_detector(phrase, config.audio) for phrase in config.wake_phrases]
    names = [(phrase.phrase, phrase.kind) for phrase in config.wake_phrases]
    return Pipeline(gate, detectors, names)


@dataclass(frozen=True)
class Stretch:
    """A scored stretch of speech held in memory."""

    identifier: int
    # Its frames, pre-roll first. Shared with the recorder while the stretch
    # is still open, so a stretch scored early by a wake keeps growing.
    frames: deque[Frame]


class RecentAudio:
    """The last few scored stretches and the last seconds of microphone audio.

    In memory only: int16 frames, never written anywhere except by
    `write_snapshot`, on request. See the module docstring.
    """

    def __init__(
        self,
        *,
        stretches: int = RECENT_STRETCHES,
        buffer_frames: int = 125,
        max_stretch_frames: int = 375,
        clock: Clock = utc_now,
    ):
        self.stretches: deque[Stretch] = deque(maxlen=stretches)
        self.buffer: deque[Frame] = deque(maxlen=buffer_frames)
        self._max_stretch_frames = max_stretch_frames
        self._clock = clock
        # The frames of the stretch open now, and whether it has been scored.
        self._open: deque[Frame] | None = None
        self._scored = False
        self.next_identifier = 1

    @classmethod
    def for_config(cls, config: Config) -> RecentAudio:
        """Sized for the configured frames: ten seconds, and the stretch cap."""
        per_second = config.audio.sample_rate / config.audio.frame_samples
        return cls(
            buffer_frames=round(BUFFER_SECONDS * per_second),
            max_stretch_frames=round(MAX_STRETCH_SECONDS * per_second),
        )

    def observe(
        self, pipeline: Pipeline, frame: Frame, events: Sequence[WakeEvent]
    ) -> list[ScoreEvent]:
        """Take in what `pipeline.process(frame)` just did.

        Returns the score events of a stretch that this frame ended, or that a
        wake event in `events` scores early: publish them before `events`.
        """
        self.buffer.append(frame)
        if pipeline.stretch_frames:
            if self._open is None:
                self._open = deque(maxlen=self._max_stretch_frames)
                self._scored = False
            self._open.extend(pipeline.stretch_frames)
        scores: list[ScoreEvent] = []
        if (
            self._open is not None
            and not self._scored
            and (pipeline.stretch_ended or events)
        ):
            identifier = self.next_identifier
            self.next_identifier += 1
            self._scored = True
            self.stretches.append(Stretch(identifier, self._open))
            at = self._clock()
            scores = [
                ScoreEvent(phrase, kind, score, identifier, at)
                for (phrase, kind), score in zip(
                    pipeline.names, pipeline.stretch_scores(), strict=True
                )
            ]
        if pipeline.stretch_ended:
            self._open = None
        return scores

    def open_identifier(self) -> int:
        """The identifier of the stretch open now, or of the next one to open.

        That is the identifier the stretch has once scored, or will be given
        when it is scored. Ask before `observe` takes the frame in.
        """
        if self._open is not None and self._scored:
            return self.next_identifier - 1
        return self.next_identifier

    def transcript_events(self, pipeline: Pipeline) -> list[TranscriptEvent]:
        """The transcriptions `pipeline.process` just made, as events.

        Stamped with `open_identifier()`: call it before `observe`.
        """
        if not pipeline.transcripts:
            return []
        identifier = self.open_identifier()
        at = self._clock()
        return [
            TranscriptEvent(phrase, kind, text, identifier, at)
            for phrase, kind, text in pipeline.transcripts
        ]

    def reset(self) -> None:
        """Audio was dropped: the open stretch ends here. What is held stays."""
        self._open = None

    def identifiers(self) -> list[int]:
        """The identifiers of the stretches held, oldest first."""
        return [stretch.identifier for stretch in self.stretches]

    def stretch_audio(self, identifier: int) -> Frame | None:
        """The samples of a held stretch, pre-roll included; None when not held."""
        for stretch in self.stretches:
            if stretch.identifier == identifier:
                return np.concatenate(list(stretch.frames))
        return None

    def buffer_audio(self) -> Frame | None:
        """The samples of the rolling buffer; None when it is empty."""
        return np.concatenate(list(self.buffer)) if self.buffer else None


type Publish = Callable[[Event], object]


def step(
    pipeline: Pipeline, recent: RecentAudio, frame: Frame, publish: Publish
) -> list[WakeEvent]:
    """One microphone frame through the pipeline, with its events published.

    The frame's transcript events are published first, then a stretch's score
    events, then the wake events of the same frame. Returns the wake events.
    """
    events = pipeline.process(frame)
    for transcript in recent.transcript_events(pipeline):
        publish(transcript)
    for score in recent.observe(pipeline, frame, events):
        publish(score)
    for event in events:
        publish(event)
    return events


class SnapshotError(Exception):
    """A snapshot cannot be written. The message is one line."""


def write_snapshot(
    recent: RecentAudio, request: str, path: Path, sample_rate: int
) -> str:
    """Write the audio `request` names to `path` as a WAV; say what was written.

    `request` is a stretch identifier or `buffer`. The file appears whole or
    not at all: it is written under a temporary name and renamed into place.
    """
    wanted = request.strip()
    if wanted == SNAPSHOT_BUFFER:
        samples = recent.buffer_audio()
        what = "the rolling buffer"
        if samples is None:
            raise SnapshotError("the listener has heard no audio yet")
    else:
        try:
            identifier = int(wanted)
        except ValueError:
            raise SnapshotError(
                f"bad snapshot request {wanted[:40]!r}: "
                f"expected a stretch identifier or {SNAPSHOT_BUFFER!r}"
            ) from None
        samples = recent.stretch_audio(identifier)
        what = f"stretch {identifier}"
        if samples is None:
            held = recent.identifiers()
            holds = f"stretches {held[0]} to {held[-1]}" if held else "no stretches"
            gone = (
                "no longer held" if identifier < recent.next_identifier else "not held"
            )
            raise SnapshotError(
                f"stretch {identifier} is {gone}: the listener holds {holds}"
            )
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle, wave.open(handle, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(SNAPSHOT_SAMPLE_WIDTH)
        wav.setframerate(sample_rate)
        wav.writeframes(samples.astype("<i2").tobytes())
    os.replace(temporary, path)
    return f"{what} ({len(samples) / sample_rate:.2f} s)"


def snapshot_paths(run_dir: Path) -> tuple[Path, Path, Path]:
    """The request, WAV and error files of the snapshot hand-off in `run_dir`."""
    return (
        run_dir / SNAPSHOT_REQUEST.name,
        run_dir / SNAPSHOT_FILE.name,
        run_dir / SNAPSHOT_ERROR.name,
    )


def clear_snapshot_files(run_dir: Path) -> None:
    """Remove what a snapshot hand-off left in `run_dir`."""
    for path in snapshot_paths(run_dir):
        for leftover in (path, path.with_name(path.name + ".tmp")):
            with contextlib.suppress(OSError):
                leftover.unlink()


@contextlib.contextmanager
def snapshot_files_cleared(run_dir: Path) -> Iterator[None]:
    """Clear the snapshot files on the way in and on the way out."""
    clear_snapshot_files(run_dir)
    try:
        yield
    finally:
        clear_snapshot_files(run_dir)


def answer_snapshot_request(
    recent: RecentAudio, sample_rate: int, run_dir: Path
) -> None:
    """Do what `snapshot.request` in `run_dir` asks: the WAV, or the error file.

    Never raises: a snapshot that fails must not stop the listener.
    """
    request_path, wav_path, error_path = snapshot_paths(run_dir)
    try:
        try:
            request = request_path.read_text(errors="replace")
        except OSError:
            raise SnapshotError("no snapshot request was found") from None
        written = write_snapshot(recent, request, wav_path, sample_rate)
    except (SnapshotError, OSError) as error:
        reason = (
            str(error)
            if isinstance(error, SnapshotError)
            else f"cannot write {SNAPSHOT_FILE}: {error.strerror}"
        )
        log(f"snapshot refused: {reason}")
        with contextlib.suppress(OSError):
            temporary = error_path.with_name(error_path.name + ".tmp")
            temporary.write_text(reason + "\n")
            os.replace(temporary, error_path)
    else:
        with contextlib.suppress(OSError):
            error_path.unlink()
        log(f"snapshot written: {written}")
    with contextlib.suppress(OSError):
        request_path.unlink()


class Hold:
    """Until when the listener drops its audio, at another process's request.

    `extend(S)` holds until S + `ACK_TAIL_SECONDS` from now, or later if a
    hold already runs that long. `active` says whether the hold runs now;
    `ended()` is true once, at the first call after a hold has run out.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._until: float | None = None

    def extend(self, seconds: float) -> None:
        until = self._clock() + seconds + ACK_TAIL_SECONDS
        if self._until is None or until > self._until:
            self._until = until

    @property
    def active(self) -> bool:
        return self._until is not None and self._clock() < self._until

    def ended(self) -> bool:
        if self._until is None or self._clock() < self._until:
            return False
        self._until = None
        return True


class HoldError(Exception):
    """A hold request cannot be taken. The message is one line."""


def parse_hold_seconds(text: str) -> float:
    """The seconds a hold request asks for: more than 0, at most the cap."""
    wanted = text.strip()
    try:
        seconds = float(wanted)
    except ValueError:
        raise HoldError(
            f"bad hold request {wanted[:40]!r}: expected a number of seconds"
        ) from None
    if not 0.0 < seconds <= MAX_HOLD_SECONDS:
        raise HoldError(
            f"bad hold request {wanted[:40]!r}: "
            f"expected more than 0 and at most {MAX_HOLD_SECONDS:g} seconds"
        )
    return seconds


def hold_path(run_dir: Path) -> Path:
    """The request file of the hold hand-off in `run_dir`."""
    return run_dir / HOLD_REQUEST.name


def clear_hold_files(run_dir: Path) -> None:
    """Remove what a hold hand-off left in `run_dir`."""
    path = hold_path(run_dir)
    for leftover in (path, path.with_name(path.name + ".tmp")):
        with contextlib.suppress(OSError):
            leftover.unlink()


@contextlib.contextmanager
def hold_files_cleared(run_dir: Path) -> Iterator[None]:
    """Clear the hold files on the way in and on the way out."""
    clear_hold_files(run_dir)
    try:
        yield
    finally:
        clear_hold_files(run_dir)


def answer_hold_request(hold: Hold, run_dir: Path) -> None:
    """Take the hold `hold.request` in `run_dir` asks for, and remove the file.

    Never raises: a request that cannot be taken leaves no hold and is logged.
    """
    request_path = hold_path(run_dir)
    try:
        try:
            request = request_path.read_text(errors="replace")
        except OSError:
            raise HoldError("no hold request was found") from None
        seconds = parse_hold_seconds(request)
    except HoldError as error:
        log(f"hold refused: {error}")
    else:
        hold.extend(seconds)
        log(f"hold: {seconds:g} s")
    with contextlib.suppress(OSError):
        request_path.unlink()


def run(
    config: Config,
    stop: threading.Event,
    snapshot: threading.Event | None = None,
    hold_request: threading.Event | None = None,
) -> None:
    """Run until `stop` is set. Opens the microphone, the speaker and the socket.

    `snapshot` is set, by `SIGUSR1`, when a snapshot request is waiting;
    `hold_request`, by `SIGUSR2`, when a hold request is.
    """
    debug = bool(os.environ.get(DEBUG_ENV_VAR))
    pipeline = build_pipeline(config)
    recent = RecentAudio.for_config(config)
    hold = Hold()
    run_dir = resolve_repo_path(SNAPSHOT_REQUEST).parent
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
        # We hold the socket by now, so the files are nobody else's.
        snapshot_files_cleared(run_dir),
        hold_files_cleared(run_dir),
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
            if snapshot is not None and snapshot.is_set():
                # Cleared first: a request signalled meanwhile is not lost.
                snapshot.clear()
                answer_snapshot_request(recent, config.audio.sample_rate, run_dir)
            if hold_request is not None and hold_request.is_set():
                hold_request.clear()
                answer_hold_request(hold, run_dir)
            frame = microphone.read(READ_TIMEOUT_SECONDS)
            if frame is None:
                if time.monotonic() - last_audio > STALLED_SECONDS:
                    raise AudioError(
                        f"no audio from the input device for {STALLED_SECONDS:g} s"
                    )
                continue
            last_audio = time.monotonic()
            if hold.active:
                # Held: the frame is dropped, and nothing is scored or published.
                continue
            if hold.ended():
                # What the acknowledgement does once it has been spoken.
                microphone.discard()
                pipeline.reset()
                recent.reset()
                continue
            events = step(pipeline, recent, frame, publisher.publish)
            if debug:
                _log_debug(pipeline, was_in_speech)
                was_in_speech = pipeline.gate.in_speech
            if ack is not None and any(event.target == TARGET_ACK for event in events):
                _acknowledge(ack, microphone, pipeline)
                recent.reset()
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
    snapshot = threading.Event()
    hold_request = threading.Event()

    def on_signal(signum: int, frame: FrameType | None) -> None:
        stop.set()

    def on_snapshot(signum: int, frame: FrameType | None) -> None:
        # Only a flag: the main loop writes the snapshot between two frames.
        snapshot.set()

    def on_hold(signum: int, frame: FrameType | None) -> None:
        # Only a flag: the main loop takes the hold between two frames.
        hold_request.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGUSR1, on_snapshot)
    signal.signal(signal.SIGUSR2, on_hold)
    try:
        config = load_config()
        require_nuc(config, "the listener")
        run(config, stop, snapshot, hold_request)
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
