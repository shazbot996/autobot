"""Run the commands the terminal user interface drives, without blocking it.

Two shapes of command:

- A `CommandTask` is a short command (a listener control action, playing the
  acknowledgement). It runs on a thread; the interface polls it and shows its
  output when it ends.
- A `BackgroundJob` is a long one (`make train-wakeword`, which can run for
  hours). It runs in its own session with its output in a log file under
  `run/`, so the interface can show the log as it grows, be left, and even be
  closed while the job carries on. A pid file lets a later run of the
  interface find the job again.

The listener is driven only through these subprocess contracts; nothing here
imports it.
"""

from __future__ import annotations

import importlib.util
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

DEFAULT_TIMEOUT = 60.0

LISTENER_CONTROL_MODULE = "autobot.listener.control"
LISTENER_ACK_MODULE = "autobot.listener.ack"
TRAIN_TARGET = "train-wakeword"


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    output: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def lines(self) -> list[str]:
        return [line.rstrip() for line in self.output.splitlines()]

    def summary(self) -> str:
        """One line: the last line of output, or the exit status."""
        for line in reversed(self.lines):
            if line.strip():
                return line.strip()
        return "done" if self.ok else f"failed (exit status {self.returncode})"


class Task(Protocol):
    def poll(self) -> CommandResult | None:
        """The result once the command has ended, else None."""

    def cancel(self) -> None:
        """Stop the command."""


class Runner(Protocol):
    def start(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> Task:
        """Start `argv` and return at once."""


def run_command(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    env: Mapping[str, str] | None = None,
) -> CommandResult:
    """Run `argv` to completion. Never raises: a failure is a non-zero result."""
    try:
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            env=None if env is None else dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return CommandResult(127, f"command not found: {argv[0]}")
    except subprocess.TimeoutExpired:
        return CommandResult(124, f"timed out after {timeout:g} s: {' '.join(argv)}")
    except OSError as error:
        return CommandResult(126, f"cannot run {argv[0]}: {error.strerror}")
    return CommandResult(completed.returncode, completed.stdout or "")


class CommandTask:
    """One short command running on a thread."""

    def __init__(
        self, argv: Sequence[str], *, cwd: Path | None, timeout: float
    ) -> None:
        self._argv = list(argv)
        self._timeout = timeout
        self._lock = threading.Lock()
        self._result: CommandResult | None = None
        self._cancelled = False
        self._process: subprocess.Popen[str] | None = None
        try:
            self._process = subprocess.Popen(
                self._argv,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                start_new_session=True,
            )
        except FileNotFoundError:
            self._result = CommandResult(127, f"command not found: {self._argv[0]}")
            return
        except OSError as error:
            self._result = CommandResult(
                126, f"cannot run {self._argv[0]}: {error.strerror}"
            )
            return
        threading.Thread(target=self._wait, daemon=True).start()

    def _wait(self) -> None:
        process = self._process
        assert process is not None
        timed_out = False
        try:
            output, _ = process.communicate(timeout=self._timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _signal_group(process.pid, signal.SIGKILL)
            output, _ = process.communicate()
        with self._lock:
            if self._cancelled:
                self._result = CommandResult(130, "cancelled")
            elif timed_out:
                self._result = CommandResult(
                    124, f"timed out after {self._timeout:g} s"
                )
            else:
                self._result = CommandResult(process.returncode, output or "")

    def poll(self) -> CommandResult | None:
        with self._lock:
            return self._result

    def cancel(self) -> None:
        with self._lock:
            if self._result is not None or self._process is None:
                return
            self._cancelled = True
        _signal_group(self._process.pid, signal.SIGTERM)


class SubprocessRunner:
    """The real `Runner`."""

    def start(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> Task:
        return CommandTask(argv, cwd=cwd, timeout=timeout)


def _signal_group(pid: int, signum: int) -> None:
    """Signal the process group `pid` leads; a group already gone is fine."""
    try:
        os.killpg(pid, signum)
    except (ProcessLookupError, PermissionError):
        pass


def module_available(name: str) -> bool:
    """Whether `python -m name` can run: the module exists in this environment."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def listener_control_argv(action: str) -> list[str]:
    """`python -m autobot.listener.control <action>` in this interpreter."""
    return [sys.executable, "-m", LISTENER_CONTROL_MODULE, action]


def listener_ack_argv() -> list[str]:
    """`python -m autobot.listener.ack`: render and play the acknowledgement once."""
    return [sys.executable, "-m", LISTENER_ACK_MODULE]


def train_argv() -> list[str]:
    return ["make", TRAIN_TARGET]


def tail_lines(path: Path, limit: int, max_bytes: int = 256 * 1024) -> list[str]:
    """The last `limit` lines of a log file; empty when it cannot be read.

    A line a progress bar redraws with carriage returns is reduced to its
    final state.
    """
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            data = handle.read()
    except OSError:
        return []
    # Split on newlines only: a carriage return redraws a line, it does not end it.
    lines = data.decode("utf-8", errors="replace").split("\n")
    if lines and not lines[-1]:
        lines.pop()
    if size > max_bytes and lines:
        lines = lines[1:]  # the first line is probably cut in half
    cleaned = [line.rstrip("\r").rsplit("\r", 1)[-1].rstrip() for line in lines]
    return cleaned[-limit:] if limit > 0 else []


class BackgroundJob:
    """A long command in its own session, logging to a file.

    State lives in two files: `log_path` (the command's output) and `pid_path`
    (its process id while it runs). `marker` is a word from the command line,
    used to tell that a pid found in the pid file is still this job and not a
    reused number.
    """

    def __init__(self, log_path: Path, pid_path: Path, marker: str) -> None:
        self.log_path = log_path
        self.pid_path = pid_path
        self.marker = marker
        self._process: subprocess.Popen[bytes] | None = None
        self._returncode: int | None = None

    def start(self, argv: Sequence[str], cwd: Path | None = None) -> None:
        """Start the command. Raises `RuntimeError` with a one-line message."""
        if self.running():
            raise RuntimeError("it is already running")
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("wb") as log:
                process = subprocess.Popen(
                    list(argv),
                    cwd=cwd,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            self.pid_path.write_text(f"{process.pid}\n", encoding="utf-8")
        except FileNotFoundError:
            raise RuntimeError(f"command not found: {argv[0]}") from None
        except OSError as error:
            raise RuntimeError(f"cannot start {argv[0]}: {error.strerror}") from None
        self._process = process
        self._returncode = None

    def pid(self) -> int | None:
        """The pid of the running job, ours or one an earlier interface left."""
        if self._process is not None:
            return self._process.pid if self._process.poll() is None else None
        try:
            pid = int(self.pid_path.read_text(encoding="utf-8").strip())
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        except (OSError, ValueError):
            return None
        return pid if self.marker.encode() in cmdline else None

    def running(self) -> bool:
        if self._process is not None:
            code = self._process.poll()
            if code is None:
                return True
            self._returncode = code
            self._process = None
            self._forget_pid()
            return False
        if self.pid() is not None:
            return True
        self._forget_pid()
        return False

    @property
    def returncode(self) -> int | None:
        """Exit status of the last run this interface started and saw end."""
        self.running()
        return self._returncode

    def cancel(self) -> bool:
        """Ask the job, and everything it started, to stop. False if not running."""
        pid = self.pid()
        if pid is None:
            return False
        _signal_group(pid, signal.SIGTERM)
        return True

    def tail(self, limit: int) -> list[str]:
        return tail_lines(self.log_path, limit)

    def _forget_pid(self) -> None:
        try:
            self.pid_path.unlink()
        except OSError:
            pass
