"""`autobot.tui.runner`: commands and background jobs, with harmless real processes."""

import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from autobot.tui.runner import (
    BackgroundJob,
    CommandResult,
    SubprocessRunner,
    listener_ack_argv,
    listener_control_argv,
    module_available,
    run_command,
    tail_lines,
)


def python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def wait_until(condition: Callable[[], bool], seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting"
        time.sleep(0.02)


def test_run_command_captures_output_and_status() -> None:
    result = run_command(
        python("import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)")
    )
    assert result.returncode == 3
    assert not result.ok
    assert sorted(result.lines) == ["err", "out"]


def test_a_missing_command_is_a_result_not_an_exception() -> None:
    result = run_command(["autobot-no-such-command"])
    assert (result.returncode, result.summary()) == (
        127,
        "command not found: autobot-no-such-command",
    )


def test_a_command_that_hangs_times_out() -> None:
    result = run_command(python("import time; time.sleep(30)"), timeout=0.3)
    assert result.returncode == 124
    assert "timed out" in result.summary()


def test_summary_is_the_last_line_or_the_status() -> None:
    assert (
        CommandResult(1, "first\nlistener is not installed\n\n").summary()
        == "listener is not installed"
    )
    assert CommandResult(0, "").summary() == "done"
    assert CommandResult(2, "\n").summary() == "failed (exit status 2)"


def test_a_task_runs_off_the_calling_thread() -> None:
    task = SubprocessRunner().start(
        python("import time; time.sleep(0.2); print('done')")
    )
    assert task.poll() is None
    wait_until(lambda: task.poll() is not None)
    result = task.poll()
    assert result is not None and result.ok and result.lines == ["done"]


def test_a_task_can_be_cancelled() -> None:
    task = SubprocessRunner().start(python("import time; time.sleep(30)"))
    task.cancel()
    wait_until(lambda: task.poll() is not None)
    result = task.poll()
    assert result is not None and (result.returncode, result.output) == (
        130,
        "cancelled",
    )


def test_a_task_times_out() -> None:
    task = SubprocessRunner().start(python("import time; time.sleep(30)"), timeout=0.2)
    wait_until(lambda: task.poll() is not None)
    result = task.poll()
    assert result is not None and result.returncode == 124


def test_a_task_for_a_missing_command_fails_at_once() -> None:
    result = SubprocessRunner().start(["autobot-no-such-command"]).poll()
    assert result is not None and result.returncode == 127


def test_module_available() -> None:
    assert module_available("autobot.config")
    assert not module_available("autobot.no_such_package.control")
    assert not module_available("autobot.config.no_such_module")


def test_listener_commands_use_this_interpreter() -> None:
    assert listener_control_argv("status") == [
        sys.executable,
        "-m",
        "autobot.listener.control",
        "status",
    ]
    assert listener_ack_argv() == [sys.executable, "-m", "autobot.listener.ack"]


def test_tail_lines(tmp_path: Path) -> None:
    log = tmp_path / "log"
    assert tail_lines(log, 5) == []
    log.write_text("one\ntwo\n 10%\r 50%\r100%\nlast\n", encoding="utf-8")
    assert tail_lines(log, 3) == ["two", "100%", "last"]
    assert tail_lines(log, 0) == []


def test_tail_lines_of_a_big_log_drops_the_cut_line(tmp_path: Path) -> None:
    log = tmp_path / "log"
    log.write_text(
        "".join(f"line {number}\n" for number in range(1000)), encoding="utf-8"
    )
    lines = tail_lines(log, 1000, max_bytes=100)
    assert lines[-1] == "line 999"
    assert all(line.startswith("line 9") for line in lines)


def job(tmp_path: Path, marker: str = "train-marker") -> BackgroundJob:
    return BackgroundJob(
        tmp_path / "run" / "job.log", tmp_path / "run" / "job.pid", marker
    )


def test_a_job_logs_to_its_file_and_reports_its_status(tmp_path: Path) -> None:
    background = job(tmp_path)
    assert not background.running()
    assert background.returncode is None
    background.start(python("print('epoch 1'); print('epoch 2'); raise SystemExit(4)"))
    wait_until(lambda: not background.running())
    assert background.tail(10) == ["epoch 1", "epoch 2"]
    assert background.returncode == 4
    assert not background.pid_path.exists()


def test_a_job_can_be_cancelled_and_not_started_twice(tmp_path: Path) -> None:
    background = job(tmp_path)
    background.start(
        python("import time; print('started', flush=True); time.sleep(30)")
    )
    assert background.running()
    assert background.pid() is not None
    with pytest.raises(RuntimeError, match="already running"):
        background.start(python("pass"))
    assert background.cancel()
    wait_until(lambda: not background.running())
    assert background.returncode != 0
    assert not background.cancel()


def test_a_job_left_running_is_found_again_by_its_pid_file(tmp_path: Path) -> None:
    first = job(tmp_path)
    first.start(python("import time; time.sleep(30)  # train-marker"))
    try:
        later = job(tmp_path)
        assert later.running()
        assert later.pid() == first.pid()
        with pytest.raises(RuntimeError, match="already running"):
            later.start(python("pass"))
        assert later.cancel()
    finally:
        first.cancel()
    wait_until(lambda: not first.running())
    wait_until(lambda: not later.running())


def test_a_stale_pid_file_is_not_mistaken_for_the_job(tmp_path: Path) -> None:
    first = job(tmp_path)
    first.start(python("import time; time.sleep(30)"))
    try:
        # The pid is alive, but its command line does not carry this marker.
        other = job(tmp_path, marker="some-other-command")
        assert not other.running()
    finally:
        first.cancel()
    wait_until(lambda: not first.running())


def test_a_job_whose_command_is_missing_says_so(tmp_path: Path) -> None:
    with pytest.raises(
        RuntimeError, match="command not found: autobot-no-such-command"
    ):
        job(tmp_path).start(["autobot-no-such-command"])
