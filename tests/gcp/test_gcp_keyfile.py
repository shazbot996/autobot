"""`autobot.gcp.keyfile`: the key is used only when it exists, is ignored and is private."""

import os
import subprocess
from pathlib import Path

import pytest

from autobot.config import GcpConfig
from autobot.gcp.keyfile import (
    NO_GIT,
    NOT_IGNORED,
    check_key,
    git_check_ignore,
    key_path,
)
from gcp.gcp_fakes import git_ignores, write_key

CONFIG = GcpConfig()
KEY = "secrets/service-account-autobot-agent.json"


def never(repo: Path, path: Path) -> int:
    raise AssertionError("git was asked about a file that is not there")


def test_a_missing_file_is_not_ready_and_git_is_not_asked(tmp_path: Path) -> None:
    state = check_key(CONFIG, tmp_path, never)
    assert not state.ready
    assert state.reason == f"no key at {KEY} yet"
    assert state.path == tmp_path / KEY


def test_an_ignored_file_at_0600_is_ready(tmp_path: Path) -> None:
    path = write_key(CONFIG, tmp_path)
    asked: list[tuple[Path, Path]] = []

    def git(repo: Path, key: Path) -> int:
        asked.append((repo, key))
        return 0

    state = check_key(CONFIG, tmp_path, git)
    assert state.ready and state.reason == ""
    assert state.path == path and state.shown == KEY
    assert asked == [(tmp_path, path)]


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660])
def test_a_file_others_can_read_is_not_used(tmp_path: Path, mode: int) -> None:
    write_key(CONFIG, tmp_path, mode)
    state = check_key(CONFIG, tmp_path, git_ignores)
    assert not state.ready
    assert state.reason == (
        f"{KEY} can be read by others (mode {mode:04o}, not 0600): the key is not used"
    )


def test_a_path_git_does_not_ignore_is_not_used(tmp_path: Path) -> None:
    write_key(CONFIG, tmp_path)
    state = check_key(CONFIG, tmp_path, lambda repo, path: NOT_IGNORED)
    assert not state.ready
    assert state.reason == f"git does not ignore {KEY}: the key is not used"


def test_when_git_cannot_say_the_key_is_not_used(tmp_path: Path) -> None:
    write_key(CONFIG, tmp_path)
    for status in (128, NO_GIT):

        def git(repo: Path, path: Path, status: int = status) -> int:
            return status

        state = check_key(CONFIG, tmp_path, git)
        assert not state.ready
        assert "git cannot say whether it ignores" in state.reason


def test_a_directory_is_not_a_key(tmp_path: Path) -> None:
    (tmp_path / KEY).mkdir(parents=True)
    state = check_key(CONFIG, tmp_path, git_ignores)
    assert state.reason == f"{KEY} is not a file: the key is not used"


def test_another_users_file_is_not_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_key(CONFIG, tmp_path)
    monkeypatch.setattr(os, "getuid", lambda: os.stat(tmp_path).st_uid + 1)
    state = check_key(CONFIG, tmp_path, git_ignores)
    assert state.reason == f"{KEY} belongs to another user: the key is not used"


def test_every_reason_is_one_line(tmp_path: Path) -> None:
    write_key(CONFIG, tmp_path, 0o644)
    for git in (git_ignores, lambda repo, path: NOT_IGNORED):
        assert "\n" not in check_key(CONFIG, tmp_path, git).reason


def test_the_file_is_never_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_key(CONFIG, tmp_path)

    def refuse(*args: object, **kwargs: object) -> object:
        raise AssertionError("the key file was opened")

    monkeypatch.setattr(Path, "open", refuse)
    monkeypatch.setattr(Path, "read_text", refuse)
    monkeypatch.setattr(Path, "read_bytes", refuse)
    monkeypatch.setattr("builtins.open", refuse)
    assert check_key(CONFIG, tmp_path, git_ignores).ready


def test_an_absolute_path_is_kept(tmp_path: Path) -> None:
    config = GcpConfig(credentials_file=str(tmp_path / "elsewhere" / "key.json"))
    assert key_path(config, Path("/repo")) == tmp_path / "elsewhere" / "key.json"


def test_the_real_git_is_asked_in_the_repository(tmp_path: Path) -> None:
    """`git check-ignore` against a scratch repository: no network, no key."""
    made = subprocess.run(
        ["git", "init", "-q", str(tmp_path)], capture_output=True, check=False
    )
    if made.returncode:
        pytest.skip("git is not installed")
    (tmp_path / ".gitignore").write_text("secrets/\n", encoding="utf-8")
    assert git_check_ignore(tmp_path, tmp_path / "secrets" / "key.json") == 0
    assert git_check_ignore(tmp_path, tmp_path / "notes.txt") == NOT_IGNORED
    # Outside any repository git cannot say.
    outside = tmp_path.parent
    assert git_check_ignore(outside, Path("/nowhere/key.json")) not in (0, NOT_IGNORED)
