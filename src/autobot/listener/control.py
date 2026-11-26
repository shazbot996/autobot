"""Runtime control of the listener: what `make up`, `down`, `status` and `logs` run.

    python -m autobot.listener.control <subcommand>

    up         start the listener
    down       stop the listener
    status     show whether it runs, its uptime, CPU and memory
    logs       print the end of the log (`--follow` keeps following it)
    install    install the systemd user unit (does not start or enable it)
    uninstall  stop, disable and remove the unit
    enable     start the unit at boot: enable it and turn lingering on
    disable    stop starting the unit at boot

Every subcommand prints human-readable text on stdout. A failure is one line
on stderr and a non-zero exit status. `status` exits 0 whether or not the
listener runs: the first line says which.

Two modes, one set of commands:

- **Foreground mode** (the unit is not installed): `up` starts
  `python -m autobot.listener` in the background, detached, with its pid in
  `run/listener.pid` and its output appended to `run/listener.log`.
- **Service mode** (the unit is installed at
  `~/.config/systemd/user/autobot-listener.service`): `up`, `down` and
  `status` drive `systemctl --user`. The unit appends to the same
  `run/listener.log`, so `logs` is the same in both modes.

The unit file is rendered from the template `deploy/autobot-listener.service`
by replacing `@REPO_ROOT@` with this checkout's path.
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from autobot.config import ConfigError, load_config, repo_root, resolve_repo_path
from autobot.listener.audio import AudioError, require_nuc
from autobot.listener.service import READY_MESSAGE

UNIT_NAME = "autobot-listener.service"
UNIT_TEMPLATE = Path("deploy") / UNIT_NAME
REPO_ROOT_PLACEHOLDER = "@REPO_ROOT@"
RUN_DIR = Path("run")
PID_FILE = RUN_DIR / "listener.pid"
LOG_FILE = RUN_DIR / "listener.log"
LISTENER_MODULE = "autobot.listener"

START_TIMEOUT_SECONDS = 45.0
STOP_TIMEOUT_SECONDS = 10.0
DEFAULT_LOG_LINES = 40
DEFAULT_SAMPLE_SECONDS = 1.0


class ControlError(Exception):
    """A control command failed. The message is one line."""


# --- paths -------------------------------------------------------------------


def unit_path() -> Path:
    """Where the systemd user unit is installed."""
    config_home = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(config_home) / "systemd" / "user" / UNIT_NAME


def unit_installed() -> bool:
    return unit_path().is_file()


def render_unit(template: str, root: Path) -> str:
    """The unit file text for a checkout at `root`."""
    if any(character.isspace() for character in str(root)):
        raise ControlError(
            f"cannot install the unit: the repository path has a space in it: {root}"
        )
    return template.replace(REPO_ROOT_PLACEHOLDER, str(root))


# --- processes ---------------------------------------------------------------


@dataclass(frozen=True)
class ProcessStats:
    pid: int
    # Seconds of CPU time used since the process started.
    cpu_seconds: float
    # Seconds since the process started.
    uptime_seconds: float
    # Resident memory in bytes.
    rss_bytes: int


def process_stats(pid: int) -> ProcessStats | None:
    """CPU time, uptime and resident memory of `pid` from /proc; None when gone."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        status = Path(f"/proc/{pid}/status").read_text()
        system_uptime = float(Path("/proc/uptime").read_text().split()[0])
    except OSError:
        return None
    # The command name, field 2, may hold spaces: split after its closing bracket.
    fields = stat.rsplit(")", 1)[1].split()
    ticks = os.sysconf("SC_CLK_TCK")
    cpu_seconds = (int(fields[11]) + int(fields[12])) / ticks
    started = int(fields[19]) / ticks
    rss_kib = 0
    for line in status.splitlines():
        if line.startswith("VmRSS:"):
            rss_kib = int(line.split()[1])
    return ProcessStats(
        pid=pid,
        cpu_seconds=cpu_seconds,
        uptime_seconds=max(0.0, system_uptime - started),
        rss_bytes=rss_kib * 1024,
    )


def is_listener(pid: int) -> bool:
    """True when `pid` is alive and is a listener process."""
    try:
        arguments = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return False
    return LISTENER_MODULE.encode() in arguments


def foreground_pid() -> int | None:
    """The pid of the listener started by `up` in foreground mode, if it runs."""
    try:
        pid = int(resolve_repo_path(PID_FILE).read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if is_listener(pid) else None


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
        return resolve_repo_path(LOG_FILE).stat().st_size
    except OSError:
        return 0


def _log_since(offset: int) -> str:
    try:
        with resolve_repo_path(LOG_FILE).open("rb") as handle:
            handle.seek(offset)
            return handle.read().decode(errors="replace")
    except OSError:
        return ""


def _last_line(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else "nothing was logged"


def _wait_until_ready(offset: int, alive: object, what: str) -> None:
    """Wait for the ready line in the log written since `offset`.

    `alive` is a callable that says whether the listener is still starting or
    running. Raises `ControlError` when it dies or takes too long.
    """
    assert callable(alive)
    deadline = time.monotonic() + START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        written = _log_since(offset)
        if f"listener: {READY_MESSAGE}" in written:
            return
        if not alive():
            # Read once more: the last line may have landed after the first read.
            raise ControlError(
                f"{what} did not start: {_last_line(_log_since(offset))}"
            )
        time.sleep(0.2)
    raise ControlError(
        f"{what} is not ready after {START_TIMEOUT_SECONDS:g} s: see {LOG_FILE}"
    )


# --- subcommands -------------------------------------------------------------


def cmd_up(args: argparse.Namespace) -> int:
    resolve_repo_path(RUN_DIR).mkdir(parents=True, exist_ok=True)
    if unit_installed():
        return _service_up()
    pid = foreground_pid()
    if pid is not None:
        print(f"listener already running (pid {pid})")
        return 0
    offset = _log_size()
    with resolve_repo_path(LOG_FILE).open("ab") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", LISTENER_MODULE],
            cwd=repo_root(),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
    pid_file = resolve_repo_path(PID_FILE)
    pid_file.write_text(f"{process.pid}\n")
    try:
        _wait_until_ready(offset, lambda: process.poll() is None, "the listener")
    except ControlError:
        if process.poll() is None:
            process.terminate()
        pid_file.unlink(missing_ok=True)
        raise
    print(f"listener started (pid {process.pid}); log: {LOG_FILE}")
    return 0


def _service_up() -> int:
    pid = service_pid()
    if pid is not None:
        print(f"listener already running (systemd unit {UNIT_NAME}, pid {pid})")
        return 0
    stray = foreground_pid()
    if stray is not None:
        raise ControlError(
            f"a foreground listener (pid {stray}) is still running: "
            "stop it with 'down' first"
        )
    offset = _log_size()
    _systemctl_checked("start", UNIT_NAME)

    def alive() -> bool:
        return unit_properties().get("ActiveState") in ("active", "activating")

    _wait_until_ready(offset, alive, f"the unit {UNIT_NAME}")
    print(
        f"listener started (systemd unit {UNIT_NAME}, pid {service_pid()}); "
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
        print(f"listener stopped (systemd unit {UNIT_NAME})")
        stopped = True
    pid = foreground_pid()
    if pid is not None:
        _stop_foreground(pid)
        print(f"listener stopped (pid {pid})")
        stopped = True
    resolve_repo_path(PID_FILE).unlink(missing_ok=True)
    if not stopped:
        print("listener is not running")
    return 0


def _stop_foreground(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if not is_listener(pid):
            return
        time.sleep(0.1)
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGKILL)
    time.sleep(0.2)
    if is_listener(pid):
        raise ControlError(f"the listener (pid {pid}) did not stop")


def cmd_status(args: argparse.Namespace) -> int:
    installed = unit_installed()
    properties = unit_properties() if installed else {}
    pid = service_pid() if installed else None
    how = f"systemd unit {UNIT_NAME}"
    if pid is None:
        pid = foreground_pid()
        how = "foreground, run/listener.pid"
    before = process_stats(pid) if pid is not None else None
    if pid is None or before is None:
        print("listener: not running")
    else:
        time.sleep(max(0.1, args.sample))
        after = process_stats(pid)
        if after is None:
            print("listener: not running (it stopped while being measured)")
        else:
            elapsed = after.uptime_seconds - before.uptime_seconds
            recent = (
                100.0 * (after.cpu_seconds - before.cpu_seconds) / elapsed
                if elapsed > 0
                else 0.0
            )
            average = (
                100.0 * after.cpu_seconds / after.uptime_seconds
                if after.uptime_seconds > 0
                else 0.0
            )
            started = datetime.fromtimestamp(time.time() - after.uptime_seconds)
            print(f"listener: running (pid {pid}; {how})")
            print(
                f"uptime:   {_duration(after.uptime_seconds)} "
                f"(since {started:%Y-%m-%d %H:%M:%S})"
            )
            print(
                f"cpu:      {recent:.1f}% of one core over the last {elapsed:.0f} s; "
                f"{average:.1f}% average since start "
                f"({after.cpu_seconds:.1f} s of CPU time)"
            )
            print(f"memory:   {after.rss_bytes / 2**20:.1f} MiB resident")
    if installed:
        linger = {True: "on", False: "off", None: "unknown"}[linger_enabled()]
        print(
            f"unit:     installed, {properties.get('UnitFileState', 'unknown')}; "
            f"state {properties.get('ActiveState', 'unknown')} "
            f"({properties.get('SubState', 'unknown')}), "
            f"{properties.get('NRestarts', '0')} restarts; lingering {linger}"
        )
    else:
        print("unit:     not installed (foreground mode)")
    return 0


def _duration(seconds: float) -> str:
    whole = int(seconds)
    days, rest = divmod(whole, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days} d {hours} h {minutes} min"
    if hours:
        return f"{hours} h {minutes} min"
    if minutes:
        return f"{minutes} min {secs} s"
    return f"{secs} s"


def cmd_logs(args: argparse.Namespace) -> int:
    log = resolve_repo_path(LOG_FILE)
    if not log.is_file():
        raise ControlError(f"no log yet: {LOG_FILE} appears after the first 'up'")
    if args.follow:
        sys.stdout.flush()
        os.execvp("tail", ["tail", "-n", str(args.lines), "-F", str(log)])
    lines = log.read_text(errors="replace").splitlines()
    for line in lines[-args.lines :]:
        print(line)
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    require_nuc(load_config(), "the listener service")
    root = repo_root()
    template = resolve_repo_path(UNIT_TEMPLATE)
    try:
        text = render_unit(template.read_text(), root)
    except OSError as error:
        raise ControlError(
            f"cannot read the unit template {template}: {error.strerror}"
        ) from None
    if not (root / ".venv" / "bin" / "python").is_file():
        raise ControlError("no .venv: run 'make setup' first")
    stray = foreground_pid()
    if stray is not None:
        _stop_foreground(stray)
        resolve_repo_path(PID_FILE).unlink(missing_ok=True)
        print(
            f"stopped the foreground listener (pid {stray}); start it again with 'up'"
        )
    resolve_repo_path(RUN_DIR).mkdir(parents=True, exist_ok=True)
    target = unit_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    except OSError as error:
        raise ControlError(f"cannot write {target}: {error.strerror}") from None
    _systemctl_checked("daemon-reload")
    print(f"unit installed: {target}")
    print("up, down and status now drive systemd; 'enable' starts it at boot")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    target = unit_path()
    if not target.is_file():
        print("unit is not installed")
        return 0
    _systemctl("stop", UNIT_NAME)
    _systemctl("disable", UNIT_NAME)
    try:
        target.unlink()
    except OSError as error:
        raise ControlError(f"cannot remove {target}: {error.strerror}") from None
    _systemctl_checked("daemon-reload")
    print(f"unit stopped, disabled and removed: {target}")
    print("up, down and status are back in foreground mode")
    return 0


def cmd_enable(args: argparse.Namespace) -> int:
    _require_installed()
    _systemctl_checked("enable", UNIT_NAME)
    print(f"unit enabled: {UNIT_NAME} starts when the user's systemd instance starts")
    user = getpass.getuser()
    if linger_enabled():
        print(
            f"lingering is on for {user}: the unit starts at boot with nobody logged in"
        )
        return 0
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
        print(
            f"lingering turned on for {user}: the unit starts at boot with nobody logged in"
        )
        return 0
    detail = (result.stderr or result.stdout).strip().splitlines()
    reason = detail[-1] if detail else "loginctl reported no reason"
    raise ControlError(
        f"unit enabled, but lingering was refused ({reason}): the listener will "
        f"start at login, not at boot, until 'sudo loginctl enable-linger {user}' is run"
    )


def cmd_disable(args: argparse.Namespace) -> int:
    _require_installed()
    _systemctl_checked("disable", UNIT_NAME)
    print(
        f"unit disabled: {UNIT_NAME} no longer starts by itself (lingering is unchanged)"
    )
    return 0


def _require_installed() -> None:
    if not unit_installed():
        raise ControlError("the unit is not installed: run 'install' first")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m autobot.listener.control",
        description="Runtime control of the autobot listener.",
    )
    commands = parser.add_subparsers(
        dest="command", required=True, metavar="subcommand"
    )
    commands.add_parser("up", help="start the listener").set_defaults(run=cmd_up)
    commands.add_parser("down", help="stop the listener").set_defaults(run=cmd_down)
    status = commands.add_parser("status", help="show state, uptime, CPU and memory")
    status.add_argument(
        "--sample",
        type=float,
        default=DEFAULT_SAMPLE_SECONDS,
        metavar="SECONDS",
        help="how long to measure CPU for (default: %(default)s)",
    )
    status.set_defaults(run=cmd_status)
    logs = commands.add_parser("logs", help="print the end of the log")
    logs.add_argument("-n", "--lines", type=int, default=DEFAULT_LOG_LINES)
    logs.add_argument("-f", "--follow", action="store_true", help="keep following")
    logs.set_defaults(run=cmd_logs)
    commands.add_parser("install", help="install the systemd user unit").set_defaults(
        run=cmd_install
    )
    commands.add_parser("uninstall", help="remove the systemd user unit").set_defaults(
        run=cmd_uninstall
    )
    commands.add_parser("enable", help="start the unit at boot").set_defaults(
        run=cmd_enable
    )
    commands.add_parser("disable", help="stop starting the unit at boot").set_defaults(
        run=cmd_disable
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.run(args))
    except (ControlError, ConfigError, AudioError) as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
