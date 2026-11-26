"""Publish each `WakeEvent` as one JSON line: to the log and to a socket.

The line is the hand-off seam to the routing layer (plan section 9 item 8):

    {"event": "wake", "phrase": "autobot", "target": "ack",
     "kind": "text_match", "score": 0.79, "at": "2026-11-25T21:40:03.512+00:00"}

on one line, UTF-8, ending in a newline. `at` is ISO 8601 in UTC.

Socket semantics (`run/listener.sock`):

- A Unix domain **stream** socket that the listener binds and listens on.
  Readers connect to it; any number may be connected at once.
- Every event is **broadcast** to every reader connected at that moment.
  There is no backlog and no replay: a reader gets the events published after
  it connected. The log holds the history.
- The listener only writes. Anything a reader sends is ignored.
- Publishing never blocks the audio loop. Connections are accepted and written
  without blocking; with no reader connected the line goes to the log only. A
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
from pathlib import Path
from types import TracebackType
from typing import Self, TextIO

from autobot.listener.detector import WakeEvent

SOCKET_PATH = Path("run/listener.sock")
SOCKET_BACKLOG = 8


class AlreadyRunningError(Exception):
    """Another listener holds the socket. The message is one line."""


def to_json_line(event: WakeEvent) -> str:
    """The event as its one JSON line, without the newline."""
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

    def publish(self, event: WakeEvent) -> str:
        """Write the event's line to the log and to every reader; return the line."""
        line = to_json_line(event)
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
