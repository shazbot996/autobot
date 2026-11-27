"""`autobot.server.watch`: the states, the schedule and the reasons (IS-22, IS-23)."""

from __future__ import annotations

import itertools
from pathlib import Path

from autobot.server.client import ServerClient
from autobot.server.watch import DEGRADED, DOWN, OFF, UNKNOWN, UP, ServerWatch, major
from server.server_fakes import (
    FakeClock,
    FakeTransport,
    FakeWatch,
    example,
    inline,
    server_config,
    write_token,
)


def watch_on(
    tmp_path: Path, transport: FakeTransport, *, token: bool = True, **server: object
) -> tuple[ServerWatch, FakeClock]:
    clock = FakeClock()
    config = server_config(tmp_path, token=token, **server)
    client = ServerClient(config, transport, clock=clock)
    return ServerWatch(config, client, clock=clock, spawn=inline), clock


def test_one_healthy_answer_marks_the_server_up(tmp_path: Path) -> None:
    transport = FakeTransport()
    watch, _clock = watch_on(tmp_path, transport)
    assert (watch.state, watch.ready("stt")) == (UNKNOWN, False)
    watch.tick()
    assert (watch.state, watch.reason) == (UP, "")
    assert watch.ready_jobs() == ("stt", "route", "chat")
    assert watch.model("chat") == "qwen3:8b"
    assert transport.paths() == ["/health"]


def test_two_failures_mark_it_down_and_one_success_up_again(tmp_path: Path) -> None:
    transport = FakeTransport()
    watch, clock = watch_on(tmp_path, transport)
    watch.tick()
    assert watch.state == UP
    transport.error = TimeoutError("timed out")
    clock.advance(5.0)
    watch.tick()
    # One failure is not yet down: the jobs it last reported still stand.
    assert watch.state == UP and watch.ready("chat")
    assert watch.last_failure == "the server is away: timed out"
    clock.advance(5.0)
    watch.tick()
    assert (watch.state, watch.reason) == (DOWN, "the server is away: timed out")
    assert not watch.ready("stt") and watch.ready_jobs() == ()
    transport.error = None
    clock.advance(10.0)
    watch.tick()
    assert (watch.state, watch.reason, watch.last_failure) == (UP, "", "")


def test_from_unknown_two_failures_are_needed_too(tmp_path: Path) -> None:
    watch, clock = watch_on(tmp_path, FakeTransport(error=TimeoutError("timed out")))
    watch.tick()
    assert watch.state == UNKNOWN
    clock.advance(5.0)
    watch.tick()
    assert watch.state == DOWN


def test_it_polls_every_five_seconds_up_and_backs_off_to_a_minute_down(
    tmp_path: Path,
) -> None:
    transport = FakeTransport()
    watch, clock = watch_on(tmp_path, transport)

    def asks_over(seconds: float) -> list[float]:
        """When, from now, the watcher asked, ticking ten times a second."""
        asked: list[float] = []
        before = len(transport.sent)
        start = clock.now
        for step in range(round(seconds * 10)):
            # Set, not added to, so that the tenths do not drift.
            clock.now = start + step / 10
            watch.tick()
            if len(transport.sent) > before:
                asked.append(step / 10)
                before = len(transport.sent)
        clock.now = start + seconds
        return asked

    assert asks_over(12.0) == [0.0, 5.0, 10.0]
    transport.error = ConnectionRefusedError(111, "Connection refused")
    clock.advance(3.0)
    # The waits after each failure: 5, 10, 20, 40, then 60 and 60.
    asked = asks_over(200.0)
    gaps = [round(later - earlier) for earlier, later in itertools.pairwise(asked)]
    assert gaps == [5, 10, 20, 40, 60, 60]
    assert watch.state == DOWN
    transport.error = None
    clock.advance(60.0)
    assert asks_over(11.0) == [0.0, 5.0, 10.0]
    assert watch.state == UP


def test_a_job_that_is_not_ready_is_degraded_with_its_reason(tmp_path: Path) -> None:
    health = example("health.json")
    health["jobs"]["chat"] = {
        "ready": False,
        "model": "qwen3:8b",
        "reason": "the runtime does not have the model qwen3:8b",
    }
    health["jobs"]["route"] = {"ready": False, "model": "qwen3:8b", "reason": ""}
    watch, _clock = watch_on(
        tmp_path, FakeTransport(answers={"/health": (200, health)})
    )
    watch.tick()
    assert watch.state == DEGRADED
    assert watch.reason == (
        "route not ready (no reason given); "
        "chat not ready (the runtime does not have the model qwen3:8b)"
    )
    assert watch.ready("stt") and not watch.ready("chat")
    assert watch.ready_jobs() == ("stt",)


def test_a_missing_token_is_the_reason_and_nothing_is_sent(tmp_path: Path) -> None:
    transport = FakeTransport()
    watch, clock = watch_on(tmp_path, transport, token=False)
    watch.tick()
    assert (watch.state, watch.reason) == (
        DOWN,
        "no server token: run make server-token",
    )
    assert transport.sent == []
    # Once the owner has made the token, the next ask finds the server.
    write_token(tmp_path)
    clock.advance(5.0)
    watch.tick()
    assert watch.state == UP


def test_a_token_anyone_can_read_is_the_reason(tmp_path: Path) -> None:
    watch, _clock = watch_on(tmp_path, FakeTransport())
    (tmp_path / "server-token").chmod(0o644)
    watch.tick()
    assert watch.state == DOWN
    assert watch.reason.startswith("the server token has mode 0644, not 0600")


def test_another_major_contract_version_is_down_at_once(tmp_path: Path) -> None:
    health = example("health.json")
    health["contract_version"] = "2.0"
    transport = FakeTransport(answers={"/health": (200, health)})
    watch, clock = watch_on(tmp_path, transport)
    watch.tick()
    assert (watch.state, watch.reason) == (DOWN, "contract 2.0 is not 1")
    assert not watch.ready("stt")
    # A minor version is the same contract.
    health["contract_version"] = "1.4"
    clock.advance(5.0)
    watch.tick()
    assert watch.state == UP
    assert (major("1.4"), major("2"), major(" 3 ")) == ("1", "2", "3")


def test_a_server_that_is_not_enabled_is_never_asked(tmp_path: Path) -> None:
    transport = FakeTransport()
    watch, clock = watch_on(tmp_path, transport, enabled=False)
    for _ in range(3):
        watch.tick()
        clock.advance(60.0)
    watch.poll_now()
    assert (watch.state, watch.ready("chat"), transport.sent) == (OFF, False, [])


def test_tick_does_not_wait_for_an_ask_that_is_still_out(tmp_path: Path) -> None:
    from autobot.tui.runner import FunctionTask

    started: list[FunctionTask] = []

    class Pending(FunctionTask):
        def __init__(self) -> None:
            self.value = None
            self._result = None
            self._abandoned = False

    def never(function: object) -> FunctionTask:
        started.append(Pending())
        return started[-1]

    clock = FakeClock()
    config = server_config(tmp_path)
    watch = ServerWatch(
        config, ServerClient(config, FakeTransport()), clock=clock, spawn=never
    )
    for _ in range(5):
        watch.tick()
        clock.advance(10.0)
    assert len(started) == 1 and watch.state == UNKNOWN


def test_the_fake_watch_reads_what_it_is_told() -> None:
    watch = FakeWatch()
    assert watch.state == UP and watch.ready("chat")
    watch.set(DOWN)
    assert not watch.ready("chat") and watch.reason == "the server is away: timed out"
    watch.set(DEGRADED, chat="loading")
    assert watch.ready("stt") and not watch.ready("chat")
    assert watch.reason == "chat not ready (loading)"
