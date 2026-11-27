"""The live monitor's logic: the listener's event stream, and what a mark saves.

No drawing and no curses: `autobot.tui.screens.monitor` draws this.

The listener broadcasts one JSON object per line on the Unix stream socket
`run/listener.sock` (the contract is `autobot.listener.events`, which is not
imported here: the interface reaches the listener through its socket and its
control command only, TS-10):

    {"event": "score", "phrase": "autobot", "kind": "openwakeword", "score": 0.851, "stretch": 2, "at": "..."}
    {"event": "wake", "phrase": "autobot", "target": "ack", "kind": "openwakeword", "score": 0.851, "at": "..."}

- `EventStream` is the client. It never blocks: `poll` returns the complete
  lines that have arrived, keeps a partial line for the next call, and tries
  to connect again every two seconds while the socket is absent or dropped.
- `recent_stretches` turns events into the stretches the listener still
  holds: the last five by stretch identifier, each with its score lines and
  the wakes it caused. A wake carries no identifier; it belongs to the stretch
  whose score line came just before it.
- `MonitorLog` is the screen's model: the lines of the scrolling log and the
  events of the current connection.
- `read_snapshot` and `mark_record` are what a mark does with the WAV the
  listener wrote: read it, delete it, and describe it for the voice dataset.

The socket has no replay, and stretch identifiers start again at 1 with each
listener run. So the stretches of an earlier connection are forgotten when the
connection drops, and a stretch identifier lower than the one before it starts
the list afresh: the monitor never names a stretch the listener may not hold.
"""

from __future__ import annotations

import contextlib
import json
import socket
import time
import wave
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from autobot.tui.audio import Clip
from autobot.wakeword.dataset import (
    LABEL_NEGATIVE,
    LABEL_POSITIVE,
    SOURCE_MONITOR,
    ClipRecord,
    recorded_now,
)

# The listener's socket, relative to the repository root.
SOCKET_PATH = Path("run/listener.sock")
# How long to wait before trying to connect again, in seconds.
RECONNECT_SECONDS = 2.0
# How many stretches the listener holds the audio of.
HELD_STRETCHES = 5
# What `snapshot --buffer` holds, in words: the pick's last entry.
LAST_TEN_SECONDS = "the last ten seconds"
# How many lines the log keeps to scroll back through.
LOG_LINES = 500
# A line longer than this is not an event: the buffer is dropped.
MAX_LINE_BYTES = 64 * 1024

EVENT_SCORE = "score"
EVENT_WAKE = "wake"

type Event = Mapping[str, Any]


class MonitorError(Exception):
    """A mark could not be saved. The message is one line."""


# --- the socket client ---------------------------------------------------------


class EventSource(Protocol):
    """Where the monitor's events come from: `EventStream`, or a test's fake."""

    @property
    def connected(self) -> bool:
        """Whether the listener's socket is connected right now."""

    def poll(self) -> list[dict[str, Any]]:
        """The events that arrived since the last call. Never blocks."""

    def close(self) -> None:
        """Disconnect; the next `poll` connects again."""


class EventStream:
    """A non-blocking client of the listener's socket.

    Nothing is connected until the first `poll`. A socket file that is absent,
    refuses the connection, or is closed by the listener leaves the stream
    disconnected, and `poll` tries again `RECONNECT_SECONDS` later. Anything
    that is not a JSON object on one line is skipped.
    """

    def __init__(self, path: Path, clock: Callable[[], float] = time.monotonic) -> None:
        self.path = path
        self._clock = clock
        self._socket: socket.socket | None = None
        self._buffer = b""
        # When the last attempt to connect was made; None before the first.
        self._tried_at: float | None = None

    @property
    def connected(self) -> bool:
        return self._socket is not None

    def close(self) -> None:
        self._drop()
        # Closed on purpose: the next poll connects at once.
        self._tried_at = None

    def _drop(self) -> None:
        if self._socket is not None:
            with contextlib.suppress(OSError):
                self._socket.close()
        self._socket = None
        self._buffer = b""

    def _connect(self) -> None:
        now = self._clock()
        if self._tried_at is not None and now - self._tried_at < RECONNECT_SECONDS:
            return
        self._tried_at = now
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            # A Unix socket connects at once or not at all: without blocking,
            # a listener whose backlog is full is "not now", never a wait.
            client.setblocking(False)
            client.connect(str(self.path))
        except OSError:
            client.close()
            return
        self._socket = client

    def poll(self) -> list[dict[str, Any]]:
        if self._socket is None:
            self._connect()
        if self._socket is None:
            return []
        while True:
            try:
                data = self._socket.recv(65536)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                data = b""
            if not data:
                # The listener stopped, or dropped a reader that fell behind.
                # Whatever complete lines came first are still events.
                events = self._take_lines()
                self._drop()
                self._tried_at = self._clock()
                return events
            self._buffer += data
        return self._take_lines()

    def _take_lines(self) -> list[dict[str, Any]]:
        *lines, rest = self._buffer.split(b"\n")
        self._buffer = rest if len(rest) <= MAX_LINE_BYTES else b""
        events: list[dict[str, Any]] = []
        for line in lines:
            try:
                event = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(event, dict):
                events.append(event)
        return events


# --- stretches -----------------------------------------------------------------


@dataclass(frozen=True)
class Score:
    """What one detector made of a stretch: one `score` line."""

    phrase: str
    kind: str
    # None when the detector produced no score for the stretch.
    score: float | None


@dataclass(frozen=True)
class Wake:
    """One `wake` line, attached to its stretch."""

    phrase: str
    kind: str
    score: float | None
    at: str


@dataclass(frozen=True)
class Stretch:
    """One stretch of speech the listener holds, as the monitor knows it."""

    # The identifier `control snapshot --stretch` takes.
    id: int
    # When its first score line was published, as the listener wrote it.
    at: str
    # One per configured wake phrase, in the order they arrived.
    scores: tuple[Score, ...]
    # The detections it caused; empty for a stretch that woke nothing.
    wakes: tuple[Wake, ...] = ()

    @property
    def detected(self) -> bool:
        return bool(self.wakes)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _stretch_id(event: Event) -> int | None:
    value = event.get("stretch")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def recent_stretches(events: Iterable[Event]) -> list[Stretch]:
    """The stretches the listener still holds, oldest first: the last five.

    Score lines are grouped by their stretch identifier. A wake line belongs
    to the stretch of the score line just before it; a wake with no score
    line before it belongs to none and is left out. An identifier lower than
    the one before it means the listener started again, and the earlier
    stretches, which it no longer holds, are forgotten.
    """
    stretches: dict[int, Stretch] = {}
    current: int | None = None
    for event in events:
        kind = event.get("event")
        if kind == EVENT_SCORE:
            identifier = _stretch_id(event)
            if identifier is None:
                continue
            if current is not None and identifier < current:
                stretches.clear()
            current = identifier
            score = Score(
                str(event.get("phrase", "")),
                str(event.get("kind", "")),
                _number(event.get("score")),
            )
            known = stretches.get(identifier)
            stretches[identifier] = (
                Stretch(identifier, str(event.get("at", "")), (score,))
                if known is None
                else Stretch(identifier, known.at, (*known.scores, score), known.wakes)
            )
        elif kind == EVENT_WAKE and current is not None and current in stretches:
            known = stretches[current]
            wake = Wake(
                str(event.get("phrase", "")),
                str(event.get("kind", "")),
                _number(event.get("score")),
                str(event.get("at", "")),
            )
            stretches[current] = Stretch(
                known.id, known.at, known.scores, (*known.wakes, wake)
            )
    return [stretches[key] for key in sorted(stretches)][-HELD_STRETCHES:]


def latest_detection(stretches: Sequence[Stretch]) -> Stretch | None:
    """The newest held stretch that caused a wake: what R and W are about."""
    for stretch in reversed(stretches):
        if stretch.detected:
            return stretch
    return None


def clock_time(at: str) -> str:
    """An event's `at` as local wall-clock time, `17:32:23`."""
    try:
        return datetime.fromisoformat(at).astimezone().strftime("%H:%M:%S")
    except ValueError:
        return "--:--:--"


def score_text(score: float | None) -> str:
    return "-" if score is None else f"{score:.3f}"


def describe_stretch(stretch: Stretch) -> str:
    """One line for the pick of recent stretches: `stretch 7  17:32:23  autobot 0.851  WAKE`."""
    scores = "  ".join(
        f"{score.phrase} {score_text(score.score)}" for score in stretch.scores
    )
    text = f"stretch {stretch.id}  {clock_time(stretch.at)}  {scores}"
    return f"{text}  WAKE" if stretch.detected else text


# --- the log ---------------------------------------------------------------------


@dataclass(frozen=True)
class LogLine:
    """One line of the scrolling log."""

    time: str
    # The stretch the line is about; None for a note.
    stretch: int | None
    text: str
    # True for a detection: the screen draws `WAKE` in the error colour.
    wake: bool = False
    # True for a line the monitor wrote itself (connected, a mark saved).
    note: bool = False


class MonitorLog:
    """What the monitor has seen: the log's lines and the stretches held.

    `feed` takes what `EventSource.poll` returned. `disconnected` forgets the
    stretches (module docstring); the log's lines stay to be read.
    """

    def __init__(self) -> None:
        self.lines: deque[LogLine] = deque(maxlen=LOG_LINES)
        # The score and wake events of the current connection that can still
        # matter: `recent_stretches` of them is what the listener holds.
        self._events: list[Event] = []
        self.stretches: list[Stretch] = []
        # Counts every line ever added, so a scrolled-back view can stay put.
        self.added = 0

    def _add(self, line: LogLine) -> None:
        self.lines.append(line)
        self.added += 1

    def note(self, text: str, stretch: int | None = None) -> None:
        now = datetime.now().astimezone().strftime("%H:%M:%S")
        self._add(LogLine(now, stretch, text, note=True))

    def feed(self, events: Sequence[Event]) -> None:
        for event in events:
            kind = event.get("event")
            if kind not in (EVENT_SCORE, EVENT_WAKE):
                continue
            self._events.append(event)
            self.stretches = recent_stretches(self._events)
            at = clock_time(str(event.get("at", "")))
            phrase = str(event.get("phrase", ""))
            detector = str(event.get("kind", ""))
            score = score_text(_number(event.get("score")))
            if kind == EVENT_SCORE:
                identifier = _stretch_id(event)
                if identifier is None:
                    continue
                self._add(
                    LogLine(at, identifier, f"{phrase:<12} {detector:<13} {score:>5}")
                )
            else:
                current = self.stretches[-1].id if self.stretches else None
                target = str(event.get("target", ""))
                self._add(
                    LogLine(
                        at,
                        current,
                        f"{phrase:<12} {detector:<13} {score:>5}  WAKE -> {target}",
                        wake=True,
                    )
                )
        # Only the events of the stretches still held can matter again.
        held = {stretch.id for stretch in self.stretches}
        kept: list[Event] = []
        keeping = False
        for event in self._events:
            if event.get("event") == EVENT_SCORE:
                keeping = _stretch_id(event) in held
            if keeping:
                kept.append(event)
        self._events = kept

    def disconnected(self) -> None:
        self._events = []
        self.stretches = []

    def detection(self) -> Stretch | None:
        return latest_detection(self.stretches)


# --- marks -----------------------------------------------------------------------


@dataclass(frozen=True)
class Mark:
    """What the owner says a piece of audio was."""

    # The key's word: right, wrong, missed.
    name: str
    label: str
    # The manifest's `prompt` for the clip.
    prompt: str


MARK_RIGHT = Mark("right", LABEL_POSITIVE, "monitor: right detection")
MARK_WRONG = Mark("wrong", LABEL_NEGATIVE, "monitor: false accept")
MARK_MISSED = Mark("missed", LABEL_POSITIVE, "monitor: missed wake")


def read_snapshot(path: Path) -> Clip:
    """The audio the listener wrote for a mark, deleted once it is read.

    The owner's voice does not linger outside the voice dataset: the file is
    removed whether or not it could be read. Raises `MonitorError`.
    """
    try:
        with wave.open(str(path), "rb") as wav:
            shape = (wav.getnchannels(), wav.getsampwidth())
            rate = wav.getframerate()
            frames = wav.readframes(wav.getnframes())
    except (OSError, EOFError, wave.Error):
        raise MonitorError(f"cannot read {path} as a WAV file") from None
    finally:
        with contextlib.suppress(OSError):
            path.unlink()
    if shape != (1, 2):
        raise MonitorError(f"{path} is not a mono 16-bit WAV file")
    samples = np.frombuffer(frames, dtype="<i2").astype(np.int16)
    if not len(samples):
        raise MonitorError(f"{path} holds no audio")
    return Clip(samples, rate)


def mark_record(mark: Mark, phrase: str, speaker: str, device: str) -> ClipRecord:
    """The manifest line of a marked clip: `source = "monitor"`, no prompt identifier."""
    return ClipRecord(
        file="",
        phrase=phrase,
        prompt=mark.prompt,
        prompt_id="",
        label=mark.label,
        source=SOURCE_MONITOR,
        speaker=speaker,
        device=device,
        recorded_at=recorded_now(),
    )
