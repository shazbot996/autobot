"""`autobot.listener.events`: the JSON line and the socket's semantics."""

from __future__ import annotations

import io
import json
import socket
import stat
from pathlib import Path

import pytest

from autobot.listener.detector import WakeEvent
from autobot.listener.events import AlreadyRunningError, EventPublisher, to_json_line

from .conftest import FIXED_TIME

EVENT = WakeEvent(
    phrase="autobot", target="ack", kind="text_match", score=0.79, at=FIXED_TIME
)
EXPECTED = {
    "event": "wake",
    "phrase": "autobot",
    "target": "ack",
    "kind": "text_match",
    "score": 0.79,
    "at": "2026-11-25T21:30:00.500+00:00",
}


def connect(path: Path) -> socket.socket:
    reader = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    reader.settimeout(2.0)
    reader.connect(str(path))
    return reader


def test_the_json_line_is_one_line_with_the_event_fields() -> None:
    line = to_json_line(EVENT)
    assert "\n" not in line
    assert json.loads(line) == EXPECTED


def test_publish_with_no_reader_writes_the_log_and_does_not_block(
    tmp_path: Path,
) -> None:
    log = io.StringIO()
    with EventPublisher(tmp_path / "listener.sock", log) as publisher:
        line = publisher.publish(EVENT)
        assert publisher.reader_count == 0
    assert log.getvalue() == line + "\n"
    assert json.loads(log.getvalue()) == EXPECTED


def test_every_connected_reader_gets_every_line(tmp_path: Path) -> None:
    path = tmp_path / "listener.sock"
    with EventPublisher(path, io.StringIO()) as publisher:
        first, second = connect(path), connect(path)
        try:
            publisher.publish(EVENT)
            publisher.publish(EVENT)
            assert publisher.reader_count == 2
            for reader in (first, second):
                with reader.makefile() as stream:
                    assert json.loads(stream.readline()) == EXPECTED
                    assert json.loads(stream.readline()) == EXPECTED
        finally:
            first.close()
            second.close()


def test_a_reader_gets_only_events_published_after_it_connected(
    tmp_path: Path,
) -> None:
    path = tmp_path / "listener.sock"
    with EventPublisher(path, io.StringIO()) as publisher:
        publisher.publish(EVENT)
        reader = connect(path)
        try:
            later = WakeEvent("autobot", "ack", "text_match", 0.9, FIXED_TIME)
            publisher.publish(later)
            assert json.loads(reader.makefile().readline())["score"] == 0.9
        finally:
            reader.close()


def test_a_reader_that_went_away_is_dropped(tmp_path: Path) -> None:
    path = tmp_path / "listener.sock"
    with EventPublisher(path, io.StringIO()) as publisher:
        reader = connect(path)
        publisher.poll()
        assert publisher.reader_count == 1
        reader.close()
        publisher.publish(EVENT)
        publisher.publish(EVENT)
        assert publisher.reader_count == 0


def test_a_reader_that_never_reads_is_dropped_not_waited_for(tmp_path: Path) -> None:
    path = tmp_path / "listener.sock"
    with EventPublisher(path, io.StringIO()) as publisher:
        reader = connect(path)
        try:
            # Far more than a socket buffer holds; publish must keep returning.
            for _ in range(20000):
                publisher.publish(EVENT)
                if publisher.reader_count == 0:
                    break
            assert publisher.reader_count == 0
        finally:
            reader.close()


def test_the_socket_file_is_private_and_removed_on_close(tmp_path: Path) -> None:
    path = tmp_path / "run" / "listener.sock"
    publisher = EventPublisher(path, io.StringIO())
    publisher.open()
    try:
        mode = path.stat().st_mode
        assert stat.S_ISSOCK(mode)
        assert stat.S_IMODE(mode) == 0o600
    finally:
        publisher.close()
    assert not path.exists()


def test_a_stale_socket_file_is_replaced(tmp_path: Path) -> None:
    path = tmp_path / "listener.sock"
    dead = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    dead.bind(str(path))
    dead.close()
    assert path.exists()
    with EventPublisher(path, io.StringIO()) as publisher:
        reader = connect(path)
        publisher.publish(EVENT)
        assert json.loads(reader.makefile().readline()) == EXPECTED
        reader.close()


def test_a_second_listener_on_a_live_socket_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "listener.sock"
    with EventPublisher(path, io.StringIO()):
        with pytest.raises(AlreadyRunningError, match="already running"):
            EventPublisher(path, io.StringIO()).open()
        # The refusal must not have removed the live socket.
        assert path.exists()
