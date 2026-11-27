"""`autobot_server.app`: the dispatch, driven with no socket (IS-11, IS-13, IS-24)."""

from __future__ import annotations

import json
import threading
import time

import pytest

from autobot_server.app import RETRY_AFTER, Response
from autobot_server.contract import contract_dir
from autobot_server.job import Job
from tests.server_fakes import (
    BEARER,
    TOKEN,
    FakeClock,
    FakeEngine,
    echo_endpoint,
    make_app,
)


def body_of(response: Response) -> dict[str, object]:
    assert isinstance(response.body, bytes)
    data = json.loads(response.body)
    assert isinstance(data, dict)
    return data


def header(response: Response, name: str) -> str | None:
    return dict(response.headers).get(name)


def test_health_on_fakes_is_the_contract_example() -> None:
    response = make_app().handle("GET", "/health", BEARER, b"")
    assert response.status == 200
    assert header(response, "Content-Type") == "application/json"
    expected = json.loads((contract_dir() / "health.json").read_text(encoding="utf-8"))
    assert body_of(response) == expected
    assert body_of(response)["tts"] is False


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}],
)
@pytest.mark.parametrize("path", ["/health", "/v1/stt", "/v1/tts", "/nowhere"])
def test_a_request_without_the_right_bearer_gets_401(
    headers: dict[str, str], path: str
) -> None:
    response = make_app().handle("GET", path, headers, b"")
    assert response.status == 401
    assert header(response, "WWW-Authenticate") == "Bearer"
    assert TOKEN not in json.dumps(body_of(response))


def test_header_names_are_matched_without_regard_to_case() -> None:
    headers = {"authorization": f"Bearer {TOKEN}"}
    assert make_app().handle("GET", "/health", headers, b"").status == 200


def test_tts_is_reserved() -> None:
    response = make_app().handle("POST", "/v1/tts", BEARER, b"{}")
    assert response.status == 501
    assert "reserved" in str(body_of(response)["error"])


def test_an_unknown_endpoint_is_404_and_a_wrong_method_405() -> None:
    app = make_app()
    assert app.handle("GET", "/v1/nothing", BEARER, b"").status == 404
    response = app.handle("POST", "/health", BEARER, b"")
    assert (response.status, header(response, "Allow")) == (405, "GET")


def test_a_job_endpoint_answers_through_its_job() -> None:
    job: Job[object, object] = Job("route", FakeEngine())
    job.start()
    app = make_app(endpoints={"/v1/route": echo_endpoint(job)})
    response = app.handle("POST", "/v1/route", BEARER, b'{"transcript": "hi"}')
    assert (response.status, body_of(response)) == (200, {"transcript": "hi"})
    assert app.handle("GET", "/v1/route", BEARER, b"").status == 405


def test_a_body_that_is_not_json_is_400() -> None:
    job: Job[object, object] = Job("route", FakeEngine())
    job.start()
    app = make_app(endpoints={"/v1/route": echo_endpoint(job)})
    response = app.handle("POST", "/v1/route", BEARER, b"not json")
    assert response.status == 400
    assert body_of(response)["error"] == "the request body is not valid JSON"


def test_a_job_not_ready_is_503() -> None:
    job: Job[object, object] = Job("chat", FakeEngine())
    app = make_app(endpoints={"/v1/chat": echo_endpoint(job)})
    response = app.handle("POST", "/v1/chat", BEARER, b"{}")
    assert response.status == 503
    assert body_of(response)["error"] == "chat is not ready: loading"
    assert header(response, "Retry-After") is None


def test_a_third_concurrent_request_gets_503_with_retry_after() -> None:
    engine = FakeEngine(hold=True)
    job: Job[object, object] = Job("stt", engine)
    job.start()
    app = make_app(endpoints={"/v1/stt": echo_endpoint(job)})
    statuses: list[int] = []

    def send() -> None:
        statuses.append(app.handle("POST", "/v1/stt", BEARER, b"{}").status)

    first, second = threading.Thread(target=send), threading.Thread(target=send)
    first.start()
    assert engine.running.wait(timeout=5.0)
    second.start()
    deadline = time.monotonic() + 5.0
    while job.admitted < 2:
        assert time.monotonic() < deadline
        time.sleep(0.005)
    third = app.handle("POST", "/v1/stt", BEARER, b"{}")
    assert third.status == 503
    assert header(third, "Retry-After") == RETRY_AFTER
    engine.release()
    first.join(timeout=5.0)
    second.join(timeout=5.0)
    assert statuses == [200, 200]


def test_a_failing_endpoint_is_500_in_one_line() -> None:
    def broken(body: bytes) -> Response:
        raise RuntimeError("it broke\nand said more")

    app = make_app(endpoints={"/v1/chat": broken})
    response = app.handle("POST", "/v1/chat", BEARER, b"{}")
    assert (response.status, body_of(response)["error"]) == (
        500,
        "the server failed: it broke",
    )


def test_each_request_leaves_one_log_line_without_body_or_token() -> None:
    clock = FakeClock()
    lines: list[str] = []
    job: Job[object, object] = Job("route", FakeEngine())
    job.start()

    def slow(body: bytes) -> Response:
        clock.advance(0.25)
        return echo_endpoint(job)(body)

    app = make_app(endpoints={"/v1/route": slow}, log=lines, clock=clock)
    app.handle("POST", "/v1/route?x=1", BEARER, b'{"transcript": "secret words"}')
    app.handle("GET", "/health", {}, b"")
    assert lines == [
        "POST /v1/route 30 B 0.250 s 200",
        "GET /health 0 B 0.000 s 401",
    ]
    assert not any(TOKEN in line or "secret" in line for line in lines)


def test_a_refused_body_is_logged_too() -> None:
    lines: list[str] = []
    response = make_app(log=lines).refuse("POST", "/v1/stt", 99, 413, "too large")
    assert response.status == 413
    assert lines == ["POST /v1/stt 99 B 0.000 s 413"]
