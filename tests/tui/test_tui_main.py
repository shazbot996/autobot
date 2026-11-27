"""The main screen shows the inference server beside the listener (IS-13, IS-22, IS-23)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tui_fakes import F10, Key, make_harness

from autobot.server.client import ServerClient
from autobot.server.watch import DEGRADED, UP, ServerWatch
from server.server_fakes import (
    FakeClock,
    FakeTransport,
    FakeWatch,
    example,
    inline,
    server_config,
)

# Tab opens the face.
FACE: Key = 9


def watch_on(
    tmp_path: Path, transport: FakeTransport, *, token: bool = True
) -> tuple[ServerWatch, FakeClock]:
    """A real watcher on a fake transport and a fake clock, asking inline."""
    clock = FakeClock()
    config = server_config(tmp_path / "server", token=token)
    client = ServerClient(config, transport, clock=clock)
    return ServerWatch(config, client, clock=clock, spawn=inline), clock


def frames_with(harness_frames: list[str], text: str) -> list[int]:
    return [number for number, frame in enumerate(harness_frames) if text in frame]


def test_the_server_line_changes_from_down_to_up_within_the_frames(
    tmp_path: Path,
) -> None:
    (tmp_path / "server").mkdir()
    transport = FakeTransport(error=TimeoutError("timed out"))
    watch, clock = watch_on(tmp_path, transport)

    def comes_up() -> None:
        transport.error = None
        clock.advance(10.0)

    keys: list[Key] = [
        # Two asks that fail, five seconds apart: the server is down.
        lambda: clock.advance(5.0),
        -1,
        comes_up,
        -1,
        "q",
    ]
    harness = make_harness(tmp_path, keys, cols=100, server_watch=watch)
    assert harness.run() == 0
    frames = harness.screen.frames
    looking = frames_with(frames, "Server  looking")
    down = frames_with(frames, "Server  down: the server is away: timed out")
    up = frames_with(frames, "Server  up (stt, route, chat)")
    assert looking and down and up
    assert looking[-1] < down[0] <= down[-1] < up[0]
    # The badge stands beside the listener's on the top line.
    assert "SERVER DOWN" in frames[down[0]].splitlines()[0]
    assert "SERVER UP" in frames[up[0]].splitlines()[0]
    assert "UNTRAINED" in frames[up[0]].splitlines()[0]


def test_a_missing_token_is_the_reason_on_the_main_screen(tmp_path: Path) -> None:
    (tmp_path / "server").mkdir()
    transport = FakeTransport()
    watch, _clock = watch_on(tmp_path, transport, token=False)
    harness = make_harness(tmp_path, [-1, "q"], cols=100, server_watch=watch)
    assert harness.run() == 0
    assert harness.screen.saw("Server  down: no server token: run make server-token")
    assert transport.sent == []


def test_a_contract_mismatch_is_the_reason_on_the_main_screen(tmp_path: Path) -> None:
    (tmp_path / "server").mkdir()
    health = example("health.json")
    health["contract_version"] = "2"
    watch, _clock = watch_on(
        tmp_path, FakeTransport(answers={"/health": (200, health)})
    )
    harness = make_harness(tmp_path, [-1, "q"], cols=100, server_watch=watch)
    assert harness.run() == 0
    assert harness.screen.saw("Server  down: contract 2 is not 1")


def test_a_degraded_server_says_which_job_is_not_ready(tmp_path: Path) -> None:
    watch = FakeWatch(DEGRADED, chat="model missing")
    harness = make_harness(tmp_path, [-1, "q"], cols=120, server_watch=watch)
    assert harness.run() == 0
    assert harness.screen.saw("Server  degraded: chat not ready (model missing)")
    assert harness.screen.saw("SERVER DEGRADED")


def test_the_badge_is_on_every_screen(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [FACE, -1, F10], server_watch=FakeWatch(UP))
    assert harness.run() == 0
    face = next(frame for frame in harness.screen.frames if "═ Face " in frame)
    assert "SERVER UP" in face.splitlines()[0]


def test_the_faces_ticker_records_the_server_coming_up(tmp_path: Path) -> None:
    (tmp_path / "server").mkdir()
    transport = FakeTransport(error=TimeoutError("timed out"))
    watch, clock = watch_on(tmp_path, transport)
    ticker: list[str] = []

    def comes_up() -> None:
        transport.error = None
        clock.advance(10.0)

    def read_ticker() -> None:
        view: Any = harness.app.views[-1]
        ticker.append(view.model.ticker.text)

    keys: list[Key] = [
        # Two asks that fail, five seconds apart: the server is down.
        lambda: clock.advance(5.0),
        -1,
        FACE,
        -1,
        read_ticker,
        comes_up,
        -1,
        -1,
        read_ticker,
        F10,
    ]
    harness = make_harness(tmp_path, keys, cols=100, server_watch=watch)
    assert harness.run() == 0
    assert watch.state == UP
    assert ticker == ["", "server up"]
