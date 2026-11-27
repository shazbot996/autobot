"""`autobot.listener.control hold`: what runs without a listener."""

from __future__ import annotations

import argparse
import os
import signal
from pathlib import Path

import pytest

from autobot.listener import control, service
from autobot.listener.service import Hold, answer_hold_request


def hold_args(*arguments: str) -> argparse.Namespace:
    return control.build_parser().parse_args(["hold", *arguments])


def test_the_hold_command_takes_seconds_up_to_the_cap() -> None:
    assert hold_args("--seconds", "2.40").seconds == pytest.approx(2.4)
    assert hold_args("--seconds", "60").seconds == 60.0
    for bad in (
        [],
        ["--seconds", "0"],
        ["--seconds", "-1"],
        ["--seconds", "60.01"],
        ["--seconds", "x"],
        ["--seconds", "nan"],
    ):
        with pytest.raises(SystemExit):
            hold_args(*bad)


def test_the_hold_help_lists_the_option(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exited:
        control.main(["hold", "--help"])
    assert exited.value.code == 0
    assert "--seconds S" in capsys.readouterr().out


def test_hold_fails_in_one_line_when_the_listener_is_not_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(control, "resolve_repo_path", lambda path: tmp_path / path)
    monkeypatch.setattr(control, "listener_pid", lambda: None)
    assert control.main(["hold", "--seconds", "2"]) == 1
    captured = capsys.readouterr()
    assert captured.err == "the listener is not running\n"
    assert captured.out == ""
    assert list(tmp_path.iterdir()) == []


def fake_listener(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hold: Hold
) -> list[int]:
    """Make `control` see a running listener that answers SIGUSR2 at once."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "listener.sock").touch()
    signals: list[int] = []

    def kill(pid: int, signum: int) -> None:
        assert pid == 4242
        signals.append(signum)
        answer_hold_request(hold, run_dir)

    monkeypatch.setattr(control, "resolve_repo_path", lambda path: tmp_path / path)
    monkeypatch.setattr(control, "listener_pid", lambda: 4242)
    monkeypatch.setattr(control, "is_listener", lambda pid: True)
    monkeypatch.setattr(os, "kill", kill)
    return signals


def test_hold_signals_the_listener_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    hold = Hold()
    signals = fake_listener(tmp_path, monkeypatch, hold)
    assert control.main(["hold", "--seconds", "2.40"]) == 0
    captured = capsys.readouterr()
    assert captured.out.splitlines()[-1] == "listener holding for 2.4 s"
    assert "listener: hold: 2.4 s" in captured.out
    assert captured.err == ""
    assert signals == [signal.SIGUSR2]
    assert hold.active
    assert not service.hold_path(tmp_path / "run").exists()


def test_hold_gives_up_when_the_listener_does_not_take_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_listener(tmp_path, monkeypatch, Hold())
    monkeypatch.setattr(os, "kill", lambda pid, signum: None)
    monkeypatch.setattr(control, "HOLD_TIMEOUT_SECONDS", 0.2)
    assert control.main(["hold", "--seconds", "1"]) == 1
    assert capsys.readouterr().err == (
        "the listener did not take the hold within 0.2 s\n"
    )
    assert not service.hold_path(tmp_path / "run").exists()
