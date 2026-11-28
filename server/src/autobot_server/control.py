"""Runtime control of the server on `GAMEON1`: what the `server-*` targets run (IS-25).

    python -m autobot_server.control <subcommand>

    up        start the server
    down      stop the server
    status    show whether it runs, and what its /health says
    logs      print the end of the log (`--follow` keeps following it)
    install   install the systemd user unit, enable it and turn lingering on

It mirrors the NUC's `autobot.listener.control`. Every subcommand prints
human-readable text on stdout; a failure is one line on stderr and a
non-zero exit status. Every subcommand refuses on any host but `[server]
hostname`, as the server itself does (IS-1).

Two modes, one set of commands:

- **Foreground mode** (the unit is not installed): `up` starts
  `python -m autobot_server` in the background, detached, with its pid in
  `run/server.pid` and its output appended to `run/server.log`.
- **Service mode** (the unit is installed at
  `~/.config/systemd/user/autobot-server.service`): `up`, `down` and
  `status` drive `systemctl --user`. The unit appends to the same
  `run/server.log`, so `logs` is the same in both modes.

`install` renders the unit from the template `deploy/autobot-server.service`
by replacing `@REPO_ROOT@` with this checkout's path, enables it, and turns
lingering on with `loginctl enable-linger`, so that the server starts when
WSL starts, with nobody logged in (IS-14). The Windows task that starts WSL
at boot is the owner's (`INFRASTRUCTURE.md`).

The server is up as soon as its port is bound; its models load after that,
and `status` shows each job's readiness from `/health`.
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from autobot_server.__main__ import READY_MESSAGE
from autobot_server.auth import TokenError
from autobot_server.check import CheckError, loopback, read_health
from autobot_server.config import (
    ServerConfigError,
    checkout_root,
    load_settings,
    resolve_checkout_path,
)
from autobot_server.hostcheck import WrongHostError, require_server_host

UNIT_NAME = "autobot-server.service"
UNIT_TEMPLATE = Path("deploy") / UNIT_NAME
REPO_ROOT_PLACEHOLDER = "@REPO_ROOT@"
CUDA_LIBRARY_PATH_PLACEHOLDER = "@CUDA_LIBRARY_PATH@"
# The server's own virtual environment, made by `make server-setup`.
VENV = Path(".venv-server")
RUN_DIR = Path("run")
PID_FILE = RUN_DIR / "server.pid"
LOG_FILE = RUN_DIR / "server.log"
SERVER_MODULE = "autobot_server"

START_TIMEOUT_SECONDS = 20.0
STOP_TIMEOUT_SECONDS = 10.0
DEFAULT_LOG_LINES = 40


class ControlError(Exception):
    """A control command failed. The message is one line."""


# --- paths -------------------------------------------------------------------


def unit_path() -> Path:
    """Where the systemd user unit is installed."""
    config_home = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(config_home) / "systemd" / "user" / UNIT_NAME


def unit_installed() -> bool:
    return unit_path().is_file()


def cuda_library_dirs(venv: Path) -> list[Path]:
    """The `lib` directories of the NVIDIA wheels installed in `venv`.

    cuBLAS and cuDNN come from pip wheels (`nvidia-cublas-cu12`,
    `nvidia-cudnn-cu12`), and the dynamic loader does not search their
    directories: without them on `LD_LIBRARY_PATH`, CTranslate2 fails to load
    the speech model with "libcublas.so.12 is not found". The driver's own
    `libcuda.so` is found through WSL's `/etc/ld.so.conf.d/ld.wsl.conf`.
    """
    return sorted(
        path
        for path in venv.glob("lib/python*/site-packages/nvidia/*/lib")
        if path.is_dir()
    )


def server_environment(base: Mapping[str, str], venv: Path) -> dict[str, str]:
    """The environment the foreground server is started with.

    It is the unit's: unbuffered output, and the NVIDIA wheels' libraries
    ahead of any `LD_LIBRARY_PATH` already set, so that both modes load the
    same libraries.
    """
    environment = {**base, "PYTHONUNBUFFERED": "1"}
    directories = [str(path) for path in cuda_library_dirs(venv)]
    existing = base.get("LD_LIBRARY_PATH", "")
    if existing:
        directories.append(existing)
    if directories:
        environment["LD_LIBRARY_PATH"] = ":".join(directories)
    return environment


def render_unit(template: str, root: Path, library_dirs: Sequence[Path] = ()) -> str:
    """The unit file text for a checkout at `root`.

    `library_dirs` fills the unit's `LD_LIBRARY_PATH` (`cuda_library_dirs`).
    """
    if any(character.isspace() for character in str(root)):
        raise ControlError(
            f"cannot install the unit: the checkout's path has a space in it: {root}"
        )
    library_path = ":".join(str(path) for path in library_dirs)
    return template.replace(REPO_ROOT_PLACEHOLDER, str(root)).replace(
        CUDA_LIBRARY_PATH_PLACEHOLDER, library_path
    )


# --- processes ---------------------------------------------------------------


def is_server(pid: int) -> bool:
    """True when `pid` is alive and is a server process."""
    try:
        arguments = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return False
    return SERVER_MODULE.encode() in arguments


def foreground_pid() -> int | None:
    """The pid of the server started by `up` in foreground mode, if it runs."""
    try:
        pid = int(resolve_checkout_path(PID_FILE).read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if is_server(pid) else None


def _systemctl(*arguments: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["systemctl", "--user", *arguments],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        raise ControlError("systemctl is not available on this machine") from None


def _systemctl_checked(*arguments: str) -> str:
    result = _systemctl(*arguments)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        reason = detail[-1] if detail else f"exit status {result.returncode}"
        raise ControlError(f"systemctl --user {' '.join(arguments)} failed: {reason}")
    return result.stdout


def unit_properties() -> dict[str, str]:
    output = _systemctl(
        "show",
        UNIT_NAME,
        "--property=ActiveState,SubState,MainPID,UnitFileState,Result,NRestarts",
    ).stdout
    return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)


def service_pid() -> int | None:
    """The main pid of the unit when it is active."""
    properties = unit_properties()
    pid = int(properties.get("MainPID", "0") or 0)
    return pid if properties.get("ActiveState") == "active" and pid > 0 else None


def linger_enabled() -> bool | None:
    """Whether lingering is on for this user; None when it cannot be told."""
    try:
        result = subprocess.run(
            [
                "loginctl",
                "show-user",
                getpass.getuser(),
                "--property=Linger",
                "--value",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    value = result.stdout.strip()
    return value == "yes" if result.returncode == 0 and value else None


# --- the log -----------------------------------------------------------------


def _log_size() -> int:
    try:
        return resolve_checkout_path(LOG_FILE).stat().st_size
    except OSError:
        return 0


def _log_since(offset: int) -> str:
    try:
        with resolve_checkout_path(LOG_FILE).open("rb") as handle:
            handle.seek(offset)
            return handle.read().decode(errors="replace")
    except OSError:
        return ""


def _last_line(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else "nothing was logged"


def wait_until_serving(
    offset: int,
    alive: Callable[[], bool],
    what: str,
    *,
    timeout: float = START_TIMEOUT_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Wait for the serving line in the log written since `offset`.

    Raises `ControlError` when the server dies first, with the last line it
    logged, or takes longer than `timeout`.
    """
    deadline = clock() + timeout
    while clock() < deadline:
        if f"server: {READY_MESSAGE}" in _log_since(offset):
            return
        if not alive():
            # Read once more: the last line may have landed after the first read.
            raise ControlError(
                f"{what} did not start: {_last_line(_log_since(offset))}"
            )
        sleep(0.2)
    raise ControlError(f"{what} is not serving after {timeout:g} s: see {LOG_FILE}")


# --- subcommands -------------------------------------------------------------


def cmd_up(args: argparse.Namespace) -> int:
    resolve_checkout_path(RUN_DIR).mkdir(parents=True, exist_ok=True)
    if unit_installed():
        return _service_up()
    pid = foreground_pid()
    if pid is not None:
        print(f"server already running (pid {pid})")
        return 0
    offset = _log_size()
    with resolve_checkout_path(LOG_FILE).open("ab") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", SERVER_MODULE],
            cwd=checkout_root(),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=server_environment(os.environ, resolve_checkout_path(VENV)),
        )
    pid_file = resolve_checkout_path(PID_FILE)
    pid_file.write_text(f"{process.pid}\n")
    try:
        wait_until_serving(offset, lambda: process.poll() is None, "the server")
    except ControlError:
        if process.poll() is None:
            process.terminate()
        pid_file.unlink(missing_ok=True)
        raise
    print(f"server started (pid {process.pid}); log: {LOG_FILE}")
    print("its models load now: 'make server-status' shows when each job is ready")
    return 0


def _service_up() -> int:
    pid = service_pid()
    if pid is not None:
        print(f"server already running (systemd unit {UNIT_NAME}, pid {pid})")
        return 0
    stray = foreground_pid()
    if stray is not None:
        raise ControlError(
            f"a foreground server (pid {stray}) is still running: "
            "stop it with 'make server-down' first"
        )
    offset = _log_size()
    _systemctl_checked("start", UNIT_NAME)

    def alive() -> bool:
        return unit_properties().get("ActiveState") in ("active", "activating")

    wait_until_serving(offset, alive, f"the unit {UNIT_NAME}")
    print(
        f"server started (systemd unit {UNIT_NAME}, pid {service_pid()}); "
        f"log: {LOG_FILE}"
    )
    return 0


def cmd_down(args: argparse.Namespace) -> int:
    stopped = False
    if unit_installed() and unit_properties().get("ActiveState") in (
        "active",
        "activating",
        "deactivating",
    ):
        _systemctl_checked("stop", UNIT_NAME)
        print(f"server stopped (systemd unit {UNIT_NAME})")
        stopped = True
    pid = foreground_pid()
    if pid is not None:
        _stop_foreground(pid)
        print(f"server stopped (pid {pid})")
        stopped = True
    resolve_checkout_path(PID_FILE).unlink(missing_ok=True)
    if not stopped:
        print("server is not running")
    return 0


def _stop_foreground(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if not is_server(pid):
            return
        time.sleep(0.1)
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGKILL)
    time.sleep(0.2)
    if is_server(pid):
        raise ControlError(f"the server (pid {pid}) did not stop")


def cmd_status(args: argparse.Namespace) -> int:
    installed = unit_installed()
    properties = unit_properties() if installed else {}
    pid = service_pid() if installed else None
    how = f"systemd unit {UNIT_NAME}"
    if pid is None:
        pid = foreground_pid()
        how = "foreground, run/server.pid"
    if pid is None:
        print("server: not running")
    else:
        print(f"server: running (pid {pid}; {how})")
        for line in health_lines():
            print(line)
    if installed:
        linger = {True: "on", False: "off", None: "unknown"}[linger_enabled()]
        print(
            f"unit:   installed, {properties.get('UnitFileState', 'unknown')}; "
            f"state {properties.get('ActiveState', 'unknown')} "
            f"({properties.get('SubState', 'unknown')}), "
            f"{properties.get('NRestarts', '0')} restarts; lingering {linger}"
        )
    else:
        print("unit:   not installed (foreground mode)")
    return 0


def health_lines() -> list[str]:
    """What the running server's `/health` says, a line to a job; or why it cannot be read."""
    try:
        health = read_health(loopback(load_settings()))
    except (CheckError, TokenError, ServerConfigError) as error:
        return [f"health: {error}"]
    lines = [
        f"health: version {health.server_version}, contract {health.contract_version}"
    ]
    for name in ("stt", "route", "chat"):
        job = getattr(health.jobs, name)
        state = "ready" if job.ready else f"not ready: {job.reason}"
        lines.append(f"  {name:<6}{job.model}: {state}")
    lines.append(
        f"gpu:    {health.gpu.used_mib} MiB used, {health.gpu.free_mib} MiB free; "
        f"budget {health.budget.total_mib} MiB"
    )
    return lines


def cmd_logs(args: argparse.Namespace) -> int:
    log = resolve_checkout_path(LOG_FILE)
    if not log.is_file():
        raise ControlError(
            f"no log yet: {LOG_FILE} appears after the first 'make server-up'"
        )
    if args.follow:
        sys.stdout.flush()
        os.execvp("tail", ["tail", "-n", str(args.lines), "-F", str(log)])
    lines = log.read_text(errors="replace").splitlines()
    for line in lines[-args.lines :]:
        print(line)
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    root = checkout_root()
    template = resolve_checkout_path(UNIT_TEMPLATE)
    try:
        text = render_unit(template.read_text(), root, cuda_library_dirs(root / VENV))
    except OSError as error:
        raise ControlError(
            f"cannot read the unit template {template}: {error.strerror}"
        ) from None
    if not (root / VENV / "bin" / "python").is_file():
        raise ControlError(f"no {VENV}: run 'make server-setup' first")
    stray = foreground_pid()
    if stray is not None:
        _stop_foreground(stray)
        resolve_checkout_path(PID_FILE).unlink(missing_ok=True)
        print(f"stopped the foreground server (pid {stray})")
    resolve_checkout_path(RUN_DIR).mkdir(parents=True, exist_ok=True)
    target = unit_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    except OSError as error:
        raise ControlError(f"cannot write {target}: {error.strerror}") from None
    _systemctl_checked("daemon-reload")
    print(f"unit installed: {target}")
    _systemctl_checked("enable", UNIT_NAME)
    print(f"unit enabled: {UNIT_NAME} starts when the user's systemd instance starts")
    _enable_linger()
    return _service_up()


def _enable_linger() -> None:
    user = getpass.getuser()
    if linger_enabled():
        print(f"lingering is on for {user}: the unit starts with nobody logged in")
        return
    try:
        result = subprocess.run(
            ["loginctl", "enable-linger", user],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        raise ControlError("unit enabled, but loginctl is not available") from None
    if result.returncode == 0 and linger_enabled():
        print(f"lingering turned on for {user}: the unit starts with nobody logged in")
        return
    detail = (result.stderr or result.stdout).strip().splitlines()
    reason = detail[-1] if detail else "loginctl reported no reason"
    raise ControlError(
        f"unit enabled, but lingering was refused ({reason}): the server will "
        f"start at login only, until 'sudo loginctl enable-linger {user}' is run"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m autobot_server.control",
        description="Runtime control of autobot's inference server on GAMEON1.",
    )
    commands = parser.add_subparsers(
        dest="command", required=True, metavar="subcommand"
    )
    commands.add_parser("up", help="start the server").set_defaults(run=cmd_up)
    commands.add_parser("down", help="stop the server").set_defaults(run=cmd_down)
    commands.add_parser("status", help="show its state and its /health").set_defaults(
        run=cmd_status
    )
    logs = commands.add_parser("logs", help="print the end of the log")
    logs.add_argument("-n", "--lines", type=int, default=DEFAULT_LOG_LINES)
    logs.add_argument("-f", "--follow", action="store_true", help="keep following")
    logs.set_defaults(run=cmd_logs)
    commands.add_parser(
        "install", help="install and enable the systemd user unit, with lingering"
    ).set_defaults(run=cmd_install)
    return parser


def main(
    argv: list[str] | None = None,
    *,
    hostname: Callable[[], str] = socket.gethostname,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        require_server_host(hostname(), load_settings().hostname)
        return int(args.run(args))
    except (ControlError, ServerConfigError, WrongHostError) as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
