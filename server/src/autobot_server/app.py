"""The server's dispatch: one request in, one response out, with no socket.

`App.handle(method, path, headers, body)` is a pure function of the request
and the server's state, so the tests drive every endpoint through it and
never open a port (plan choice 1). `autobot_server.http` is the only caller
that has a socket.

Every request is checked for the bearer token first, whatever its path: one
without it gets `401` (IS-13). Then:

- `GET /health` answers what the server can do (IS-11);
- `/v1/tts` answers `501`: text-to-speech is reserved (IS-10);
- the job endpoints under `/v1` (IS-22) are those the server was built with,
  each a function of the request's body. A job that is busy answers `503`
  with `Retry-After` (IS-24), one that is not ready `503`, and a body that
  is not the contract's shape `400`.

Each request leaves one log line, of its method, endpoint, size, duration
and status, and nothing else: no body, no header, no token (IS-5).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from autobot_server.auth import authorized
from autobot_server.contract import ContractError
from autobot_server.gpu import Gpu
from autobot_server.health import health
from autobot_server.job import Busy, NotReady, Readiness, one_line

HEALTH_PATH = "/health"
TTS_PATH = "/v1/tts"
# How long a client told `503` busy waits before it may ask again, in seconds.
RETRY_AFTER = "1"

JSON_TYPE = "application/json"
EVENT_STREAM_TYPE = "text/event-stream"

TTS_RESERVED = (
    "text-to-speech is reserved and not in this version: piper on the NUC is the voice"
)


@dataclass(frozen=True)
class Response:
    """What the server answers: a status, its headers and its body.

    A body of bytes is sent whole; any other iterable of bytes is streamed,
    a chunk at a time, as the chat's server-sent events are (IS-9).
    """

    status: int
    headers: tuple[tuple[str, str], ...] = ()
    body: bytes | Iterable[bytes] = b""


# A job endpoint: the request's body in, the response out.
type Endpoint = Callable[[bytes], Response]


def json_response(
    status: int, data: Mapping[str, Any], headers: Iterable[tuple[str, str]] = ()
) -> Response:
    """A response whose body is `data` as JSON."""
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return Response(status, (("Content-Type", JSON_TYPE), *headers), body)


def error_response(
    status: int, message: str, headers: Iterable[tuple[str, str]] = ()
) -> Response:
    """A response that says, in one line, why the request was not answered."""
    return json_response(status, {"error": message}, headers)


def request_json(body: bytes) -> object:
    """The request's body as parsed JSON; `ContractError` when it is not JSON."""
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractError("the request body is not valid JSON") from None


class App:
    """The server's endpoints, its token and its jobs."""

    def __init__(
        self,
        *,
        token: str,
        jobs: Mapping[str, Readiness],
        gpu: Gpu,
        contract_version: str,
        endpoints: Mapping[str, Endpoint] | None = None,
        log: Callable[[str], None] = print,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._token = token
        self._jobs = jobs
        self._gpu = gpu
        self._contract_version = contract_version
        # The `POST` endpoints, by path.
        self._endpoints = dict(endpoints or {})
        self._log = log
        self._clock = clock

    def handle(
        self, method: str, path: str, headers: Mapping[str, str], body: bytes
    ) -> Response:
        """The response to one request, and its log line."""
        started = self._clock()
        endpoint = urlsplit(path).path or "/"
        lowered = {name.lower(): value for name, value in headers.items()}
        response = self._dispatch(method, endpoint, lowered, body)
        self.log_request(method, endpoint, len(body), self._clock() - started, response)
        return response

    def refuse(
        self, method: str, path: str, size: int, status: int, message: str
    ) -> Response:
        """The response to a request whose body was not read, and its log line."""
        response = error_response(status, message)
        self.log_request(method, urlsplit(path).path or "/", size, 0.0, response)
        return response

    def log_request(
        self, method: str, endpoint: str, size: int, seconds: float, response: Response
    ) -> None:
        """One line: method, endpoint, size, duration and status (IS-5)."""
        self._log(f"{method} {endpoint} {size} B {seconds:.3f} s {response.status}")

    def _dispatch(
        self, method: str, endpoint: str, headers: Mapping[str, str], body: bytes
    ) -> Response:
        if not authorized(headers, self._token):
            return error_response(
                401,
                "missing or wrong bearer token",
                (("WWW-Authenticate", "Bearer"),),
            )
        if endpoint == HEALTH_PATH:
            if method != "GET":
                return _not_allowed("GET")
            answer = health(self._jobs, self._gpu, self._contract_version)
            return json_response(200, answer.to_json())
        if endpoint == TTS_PATH:
            return error_response(501, TTS_RESERVED)
        run = self._endpoints.get(endpoint)
        if run is None:
            return error_response(404, f"no endpoint {endpoint}")
        if method != "POST":
            return _not_allowed("POST")
        try:
            return run(body)
        except Busy as busy:
            return error_response(503, str(busy), (("Retry-After", RETRY_AFTER),))
        except NotReady as not_ready:
            return error_response(503, str(not_ready))
        except ContractError as wrong:
            return error_response(400, str(wrong))
        except Exception as error:  # noqa: BLE001
            # Any other failure is one line to the NUC, which falls back
            # (IS-4), and the server goes on serving.
            return error_response(500, f"the server failed: {one_line(error)}")


def _not_allowed(method: str) -> Response:
    return error_response(
        405, f"this endpoint answers {method} only", (("Allow", method),)
    )
