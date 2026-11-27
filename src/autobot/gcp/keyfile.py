"""Whether the service account's key file is safe to use (GC-7).

The key is *ready* only when all three hold: the file exists, git ignores its
path, and nobody but its owner can read it. Anything else names its cause in
one line, and the application then does not use the key. This module reads
the file's metadata only, never its contents.
"""

from __future__ import annotations

import os
import stat
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from autobot.config import GcpConfig

# Asks git whether it ignores a path: (repository, path) -> the exit status of
# `git check-ignore -q`, which is 0 when ignored and 1 when not.
type GitCheckIgnore = Callable[[Path, Path], int]

IGNORED = 0
NOT_IGNORED = 1
NO_GIT = 127


def git_check_ignore(repo: Path, path: Path) -> int:
    """The real `GitCheckIgnore`: run `git check-ignore -q` in the repository."""
    try:
        done = subprocess.run(
            ["git", "check-ignore", "-q", "--", str(path)],
            cwd=repo,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return NO_GIT
    return done.returncode


@dataclass(frozen=True)
class KeyState:
    """What `check_key` found."""

    ready: bool
    # The absolute path of the key file.
    path: Path
    # The path as the configuration spells it, for messages.
    shown: str
    # One line: why the key is not used. Empty when it is ready.
    reason: str = ""


def key_path(config: GcpConfig, repo: Path) -> Path:
    """The key file's absolute path: `credentials_file` under the repository."""
    path = Path(config.credentials_file).expanduser()
    return path if path.is_absolute() else repo / path


def check_key(
    config: GcpConfig, repo: Path, git: GitCheckIgnore = git_check_ignore
) -> KeyState:
    """Whether the key at `[gcp] credentials_file` exists, is ignored and is private."""
    path = key_path(config, repo)
    shown = config.credentials_file

    def refused(reason: str) -> KeyState:
        return KeyState(False, path, shown, reason)

    try:
        info = path.stat()
    except FileNotFoundError:
        return refused(f"no key at {shown} yet")
    except OSError as error:
        return refused(f"cannot look at {shown}: {error.strerror}")
    if not stat.S_ISREG(info.st_mode):
        return refused(f"{shown} is not a file: the key is not used")
    status = git(repo, path)
    if status == NOT_IGNORED:
        return refused(f"git does not ignore {shown}: the key is not used")
    if status != IGNORED:
        return refused(
            f"git cannot say whether it ignores {shown}: the key is not used"
        )
    mode = stat.S_IMODE(info.st_mode)
    if mode & 0o077:
        return refused(
            f"{shown} can be read by others (mode {mode:04o}, not 0600): "
            "the key is not used"
        )
    if info.st_uid != os.getuid():
        return refused(f"{shown} belongs to another user: the key is not used")
    return KeyState(True, path, shown)
