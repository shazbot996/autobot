"""Publish the listener's events as JSON lines: wakes, scores and transcripts.

Every event is one JSON object on one line, UTF-8, ending in a newline. `at`
is ISO 8601 in UTC with milliseconds. There are three events.

**`wake`**: a wake phrase was heard (`WakeEvent`). It goes to the log and to
the socket. It is the hand-off seam to the routing layer:

    {"event": "wake", "phrase": "autobot", "target": "ack", "kind": "text_match", "score": 0.79, "at": "2026-11-25T21:40:03.512+00:00"}

**`score`**: what one detector made of one stretch of speech (`ScoreEvent`).
It goes to the socket only, never to the log, so the log holds what it always
held. The live monitor reads it:

    {"event": "score", "phrase": "autobot", "kind": "openwakeword", "score": 0.912, "stretch": 7, "at": "2026-11-26T18:02:11.480+00:00"}

- `phrase` and `kind` are the configured wake phrase and its detector kind.
  A stretch yields one `score` line per configured wake phrase, in the order
  of the configuration, all with the same `stretch`.
- `score` is the detector's score for the stretch, from 0 to 1, rounded to
  three decimals: for `openwakeword` the model's highest score over the
  stretch (`last_peak`); for `text_match` the confidence of the stretch's
  last transcript (`last_transcript.score`). It is `null` when the detector
  produced no score for the stretch (a detector kind with neither figure, or
  a `text_match` stretch that was not transcribed).
- `stretch` is the stretch identifier: an integer that starts at 1 with each
  listener run and rises by one per stretch. It is the name to give
  `python -m autobot.listener.control snapshot --stretch`. The listener holds
  the audio of the last five only.
- `at` is when the line was published: the end of the stretch, or the wake
  that closed its score early (below).

**`transcript`**: the text of one transcription a detector made of speech in
a stretch (`TranscriptEvent`). It goes to the socket only, never to the log,
like `score`: a transcript of the room is not something to keep. The face's
ticker reads it:

    {"event": "transcript", "phrase": "autobot", "kind": "text_match", "text": "Hey autobot.", "stretch": 7, "at": "2026-11-26T18:02:11.480+00:00"}

- `phrase` and `kind` are the configured wake phrase and the detector kind
  whose transcription it is. Only a detector that transcribes speech
  (`text_match`) yields any; a detector makes one per transcription, so a
  long stretch can yield several.
- `text` is what the detector's speech-to-text wrote, as it wrote it. A
  transcription with no text is not published.
- `stretch` is the identifier of the stretch the transcribed speech belongs
  to: the same as its `score` lines.
- `at` is when the line was published: when the transcription was made.

**Order.** For each stretch: transcripts, then scores, then wakes. A
stretch's `transcript` lines are published as the transcriptions are made,
so before its `score` lines; its `score` lines are published before any
`wake` it causes, so a reader ties a `wake` to the stretch of the `score`
lines just before it. A stretch is scored once: when it ends, or, when a
detector fires inside it, at that moment, just before the first `wake` (the
`score` of the detector that fired is then the score it fired at). A
transcription made in a stretch after it was scored early is published after
that stretch's `score` and `wake` lines, with the same `stretch`. See
`autobot.listener.service` for which audio a stretch is.

Socket semantics (`run/listener.sock`):

- A Unix domain **stream** socket that the listener binds and listens on.
  Readers connect to it; any number may be connected at once.
- Every event is **broadcast** to every reader connected at that moment.
  There is no backlog and no replay: a reader gets the events published after
  it connected. The log holds the history of the wakes.
- The listener only writes. Anything a reader sends is ignored.
- Publishing never blocks the audio loop. Connections are accepted and written
  without blocking; with no reader connected a wake goes to the log only. A
  reader that has disconnected, or is so far behind that its socket buffer is
  full, is dropped: it must reconnect.
- The socket file is created with mode 0600, when the listener starts, and
  removed when it stops. A socket file something still answers on means a
  listener is already running, and a second one refuses to start.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Self, TextIO

from autobot.listener.detector import WakeEvent

SOCKET_PATH = Path("run/listener.sock")
SOCKET_BACKLOG = 8


class AlreadyRunningError(Exception):
    """Another listener holds the socket. The message is one line."""


@dataclass(frozen=True)
class ScoreEvent:
    """What one detector made of one stretch of speech."""

    # The configured phrase, as written in the configuration.
    phrase: str
    # The detector kind that scored it.
    kind: str
    # The detector's score for the stretch, from 0 to 1; None when it has none.
    score: float | None
    # The stretch identifier: 1 for the first stretch of a listener run.
    stretch: int
    # When the score was published: timezone-aware, UTC.
    at: datetime


@dataclass(frozen=True)
class TranscriptEvent:
    """The text of one transcription a detector made of speech in a stretch."""

    # The configured phrase whose detector made the transcription.
    phrase: str
    # The detector kind that made it.
    kind: str
    # What speech-to-text wrote; never empty.
    text: str
    # The identifier of the stretch the speech belongs to.
    stretch: int
    # When the transcript was published: timezone-aware, UTC.
    at: datetime


type Event = WakeEvent | ScoreEvent | TranscriptEvent


def to_json_line(event: Event) -> str:
    """The event as its one JSON line, without the newline."""
    if isinstance(event, TranscriptEvent):
        return json.dumps(
            {
                "event": "transcript",
                "phrase": event.phrase,
                "kind": event.kind,
                "text": event.text,
                "stretch": event.stretch,
                "at": event.at.isoformat(timespec="milliseconds"),
            }
        )
    if isinstance(event, ScoreEvent):
        return json.dumps(
            {
                "event": "score",
                "phrase": event.phrase,
                "kind": event.kind,
                "score": event.score,
                "stretch": event.stretch,
                "at": event.at.isoformat(timespec="milliseconds"),
            }
        )
    return json.dumps(
        {
            "event": "wake",
            "phrase": event.phrase,
            "target": event.target,
            "kind": event.kind,
            "score": event.score,
            "at": event.at.isoformat(timespec="milliseconds"),
        }
    )


class EventPublisher:
    """Writes events to the log stream and to the readers of the socket."""

    def __init__(self, socket_path: Path, log_stream: TextIO | None = None):
        self._path = socket_path
        self._log_stream = log_stream
        self._server: socket.socket | None = None
        self._readers: list[socket.socket] = []

    def __enter__(self) -> Self:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    @property
    def reader_count(self) -> int:
        return len(self._readers)

    def open(self) -> None:
        """Bind and listen. Removes a stale socket file; refuses a live one."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists() or self._path.is_symlink():
            if _answers(self._path):
                raise AlreadyRunningError(
                    f"a listener is already running: {self._path} is in use"
                )
            self._path.unlink()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            old_mask = os.umask(0o177)
            try:
                server.bind(str(self._path))
            finally:
                os.umask(old_mask)
            server.listen(SOCKET_BACKLOG)
            server.setblocking(False)
        except OSError:
            server.close()
            raise
        self._server = server

    def close(self) -> None:
        for reader in self._readers:
            with contextlib.suppress(OSError):
                reader.close()
        self._readers = []
        if self._server is not None:
            with contextlib.suppress(OSError):
                self._server.close()
            self._server = None
            with contextlib.suppress(OSError):
                self._path.unlink()

    def poll(self) -> None:
        """Accept waiting readers. Never blocks; call it often."""
        if self._server is None:
            return
        while True:
            try:
                reader, _address = self._server.accept()
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                return
            reader.setblocking(False)
            self._readers.append(reader)

    def publish(self, event: Event) -> str:
        """Write the event's line to every reader; return the line.

        A wake event goes to the log as well. A score or transcript event
        does not.
        """
        line = to_json_line(event)
        if isinstance(event, WakeEvent):
            stream = self._log_stream if self._log_stream is not None else sys.stdout
            print(line, file=stream, flush=True)
        self.poll()
        data = (line + "\n").encode()
        kept: list[socket.socket] = []
        for reader in self._readers:
            try:
                sent = reader.send(data)
            except OSError:
                sent = -1
            if sent == len(data):
                kept.append(reader)
            else:
                # Gone, or too far behind to take a whole line: drop it.
                with contextlib.suppress(OSError):
                    reader.close()
        self._readers = kept
        return line


def _answers(path: Path) -> bool:
    """True when something accepts connections on the socket at `path`."""
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(0.5)
    try:
        probe.connect(str(path))
    except OSError:
        return False
    finally:
        probe.close()
    return True
