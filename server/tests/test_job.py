"""`autobot_server.job`: readiness, one running and one waiting, and losing the GPU (IS-23, IS-24)."""

from __future__ import annotations

import threading
import time

import pytest

from autobot_server.job import (
    ADMITTED,
    LOADING,
    NOT_READY,
    READY,
    RETRY_SECONDS,
    Busy,
    Job,
    NotReady,
    Unavailable,
    keep_warm,
)
from tests.server_fakes import FakeClock, FakeEngine

OUT_OF_MEMORY = RuntimeError("CUDA failed with error out of memory")


def wait_for(condition: object, seconds: float = 5.0) -> None:
    assert callable(condition)
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.005)


def test_a_job_is_loading_until_its_model_has_warmed_up() -> None:
    engine = FakeEngine("large-v3-turbo")
    job: Job[object, object] = Job("stt", engine)
    assert job.state == LOADING
    assert not job.health().ready
    assert job.health().reason == "loading"
    with pytest.raises(NotReady, match="stt is not ready: loading"):
        job.run("audio")
    job.start()
    assert (job.state, engine.loads, engine.warm_ups) == (READY, 1, 1)
    assert job.health().ready and job.health().reason == ""
    assert job.run("audio") == "audio"


def test_a_failed_load_is_a_reason_not_a_crash() -> None:
    engine = FakeEngine()
    engine.load_error = NotReady(
        "model directory missing: run make server-fetch-models"
    )
    job: Job[object, object] = Job("stt", engine)
    job.start()
    assert job.state == NOT_READY
    assert (
        job.health().reason == "model directory missing: run make server-fetch-models"
    )


def test_a_failed_warm_up_keeps_the_job_not_ready() -> None:
    engine = FakeEngine()
    engine.warm_error = RuntimeError("the warm-up failed\nwith a traceback below")
    job: Job[object, object] = Job("route", engine)
    job.start()
    assert job.reason == "the warm-up failed"


def test_a_third_concurrent_request_is_busy_at_once() -> None:
    engine = FakeEngine(hold=True)
    job: Job[object, object] = Job("chat", engine)
    job.start()
    answers: list[object] = []
    threads = [
        threading.Thread(target=lambda n=n: answers.append(job.run(n)))
        for n in range(ADMITTED)
    ]
    threads[0].start()
    assert engine.running.wait(timeout=5.0)
    threads[1].start()
    wait_for(lambda: job.admitted == ADMITTED)
    with pytest.raises(Busy, match="chat is busy"):
        job.run("third")
    engine.release()
    for thread in threads:
        thread.join(timeout=5.0)
    assert sorted(answers, key=str) == [0, 1]
    assert job.admitted == 0
    assert job.run("after") == "after"


def test_out_of_memory_marks_the_job_not_ready_and_retries_its_warm_up() -> None:
    clock = FakeClock()
    engine = FakeEngine()
    job: Job[object, object] = Job("stt", engine, clock=clock)
    job.start()
    engine.run_error = OUT_OF_MEMORY
    with pytest.raises(
        NotReady, match="stt is not ready: CUDA failed with error out of memory"
    ):
        job.run("audio")
    assert job.state == NOT_READY
    assert job.admitted == 0
    engine.warm_error = OUT_OF_MEMORY
    clock.advance(RETRY_SECONDS - 1)
    job.tick()
    assert engine.warm_ups == 1
    clock.advance(1)
    job.tick()
    assert (engine.warm_ups, job.state) == (2, NOT_READY)
    engine.warm_error = None
    clock.advance(RETRY_SECONDS)
    job.tick()
    assert job.state == READY
    assert engine.loads == 1, "the model is loaded once and never restarted"
    assert job.run("audio") == "audio"


def test_any_other_error_is_the_requests_and_the_job_stays_ready() -> None:
    engine = FakeEngine()
    job: Job[object, object] = Job("route", engine)
    job.start()
    engine.run_error = ValueError("the answer was not JSON")
    with pytest.raises(ValueError):
        job.run("transcript")
    assert job.state == READY


def test_a_ready_job_is_not_warmed_again() -> None:
    engine = FakeEngine()
    clock = FakeClock()
    job: Job[object, object] = Job("chat", engine, clock=clock)
    job.start()
    clock.advance(RETRY_SECONDS * 10)
    job.tick()
    assert engine.warm_ups == 1


def test_an_unavailable_engine_keeps_its_job_not_ready() -> None:
    job: Job[object, object] = Job(
        "chat", Unavailable("(none chosen)", "no model chosen")
    )
    job.start()
    assert job.health().model == "(none chosen)"
    assert job.health().reason == "no model chosen"


def test_keep_warm_starts_every_job_and_stops_when_told() -> None:
    engines = [FakeEngine(), FakeEngine()]
    jobs: list[Job[object, object]] = [Job(str(n), e) for n, e in enumerate(engines)]
    stop = threading.Event()
    worker = threading.Thread(target=keep_warm, args=(jobs, stop, 0.01))
    worker.start()
    wait_for(lambda: all(job.state == READY for job in jobs))
    stop.set()
    worker.join(timeout=5.0)
    assert not worker.is_alive()
