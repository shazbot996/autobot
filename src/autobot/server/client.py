"""The NUC's client of the inference server: one method to an endpoint (IS-4, IS-13).

`ServerClient` speaks the contract of `autobot.server.contract` to the
server at `[server] url`, over the standard library's `urllib`, with the
shared bearer token on every request. The token is read from `[server]
token_file` for each call and is never logged or put in a message.

The NUC never depends on the server, so every call has its short timeout
from `[server]` (IS-4):

| Call | Timeout |
| --- | --- |
| `health` | `health_timeout` |
| `stt` | `stt_timeout_base` plus `stt_timeout_per_second` per second of audio |
| `route` | `route_timeout` |
| `chat` | `chat_timeout` |
| `stream_chat` | `chat_first_token_timeout` to the first token, `chat_timeout` in all |

A timeout, a refused connection, a status other than `200`, an answer that
is not the contract's shape, a missing token: each is one `ServerUnavailable`
whose message is one line. The request is not tried again; the caller falls
back at once.

Every call blocks, so the face makes it on a worker thread. The `Transport`
protocol is the one place that opens a socket, and what the tests fake.

Only the house's LAN is crossed: the server is the owner's own machine and
no cloud service is involved (`CN-1`, `CN-2`, IS-7).
"""

from __future__ import annotations

import contextlib
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Iterator, Mapping
from typing import Protocol

from autobot.config import ServerConfig
from autobot.server.contract import (
    ChatRequest,
    ChatResponse,
    ContractError,
    Health,
    RouteRequest,
    RouteResponse,
    Shape,
    StreamEvent,
    StreamToken,
    SttRequest,
    SttResponse,
    iter_stream,
)
from autobot.server.token import TokenError, read_token

HEALTH_PATH = "/health"
STT_PATH = "/v1/stt"
ROUTE_PATH = "/v1/route"
CHAT_PATH = "/v1/chat"

JSON_TYPE = "application/json"
EVENT_STREAM_TYPE = "text/event-stream"


class ServerUnavailable(Exception):
    """The server could not answer this request. The message is one line."""


class Lines(Protocol):
    """An open streamed answer: its lines as they arrive, and a close."""

    def __iter__(self) -> Iterator[str]: ...

    def close(self) -> None: ...


class Transport(Protocol):
    """What carries a request to the server; `OSError` when it cannot."""

    def send(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> tuple[int, bytes]:
        """The status and the body of the whole answer."""
        ...

    def stream(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> tuple[int, Lines]:
        """The status and the answer's lines; `timeout` bounds each wait for one."""
        ...


class _ReplyLines:
    """The lines of an open `urllib` response, decoded."""

    def __init__(self, reply: object) -> None:
        self._reply = reply

    def __iter__(self) -> Iterator[str]:
        lines: Iterable[bytes] = self._reply  # type: ignore[assignment]
        for line in lines:
            yield line.decode("utf-8", errors="replace")

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._reply.close()  # type: ignore[attr-defined]


class UrllibTransport:
    """The real transport: `urllib`, with no proxy and no retry."""

    def __init__(self) -> None:
        # The server is on the LAN: a proxy from the environment is never used.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _open(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> object:
        request = urllib.request.Request(
            url, data=body, headers=dict(headers), method=method
        )
        return self._opener.open(request, timeout=timeout)

    def send(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> tuple[int, bytes]:
        try:
            with self._open(method, url, headers, body, timeout) as reply:  # type: ignore[attr-defined]
                return int(reply.status), reply.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def stream(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> tuple[int, Lines]:
        try:
            reply = self._open(method, url, headers, body, timeout)
        except urllib.error.HTTPError as error:
            text = error.read().decode("utf-8", errors="replace")
            return error.code, _Text(text)
        return int(reply.status), _ReplyLines(reply)  # type: ignore[attr-defined]


class _Text:
    """A whole body as lines: what a refused stream answers."""

    def __init__(self, text: str) -> None:
        self._text = text

    def __iter__(self) -> Iterator[str]:
        return iter(self._text.splitlines())

    def close(self) -> None:
        pass


def _why(error: OSError) -> str:
    """An `OSError` from the transport as a few words."""
    if isinstance(error, TimeoutError):
        return "timed out"
    reason = getattr(error, "reason", None)
    if isinstance(reason, TimeoutError):
        return "timed out"
    if isinstance(reason, OSError):
        return reason.strerror or type(reason).__name__
    return str(reason or error.strerror or type(error).__name__)


def _refusal(status: int, body: bytes) -> str:
    """A status other than 200, with the server's own one-line reason when it gave one."""
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        data = None
    reason = data.get("error") if isinstance(data, dict) else None
    line = " ".join(str(reason).split()) if reason else ""
    return f"the server answered {status}{': ' + line if line else ''}"


class ServerClient:
    """The inference server, as the NUC calls it."""

    def __init__(
        self,
        config: ServerConfig,
        transport: Transport | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._transport = transport if transport is not None else UrllibTransport()
        self._clock = clock
        self._base = config.url.rstrip("/")

    @property
    def config(self) -> ServerConfig:
        return self._config

    def token_trouble(self) -> str:
        """Why the token cannot be used, in one line; empty when it can."""
        try:
            read_token(self._config.token_path)
        except TokenError as error:
            return str(error)
        return ""

    # --- the endpoints ---------------------------------------------------------

    def health(self) -> Health:
        """`GET /health`: what the server can do (IS-11)."""
        return self._ask(Health, "GET", HEALTH_PATH, None, self._config.health_timeout)

    def stt(
        self, audio: bytes, seconds: float, hints: Iterable[str] = ()
    ) -> SttResponse:
        """`POST /v1/stt`: the WAV file `audio`, `seconds` long, transcribed (IS-8)."""
        timeout = (
            self._config.stt_timeout_base
            + self._config.stt_timeout_per_second * max(0.0, seconds)
        )
        request = SttRequest.from_wav(audio, hints)
        return self._ask(SttResponse, "POST", STT_PATH, request, timeout)

    def route(self, request: RouteRequest) -> RouteResponse:
        """`POST /v1/route`: one agent of the request's list, or `none` (IS-9)."""
        return self._ask(
            RouteResponse, "POST", ROUTE_PATH, request, self._config.route_timeout
        )

    def chat(self, request: ChatRequest) -> ChatResponse:
        """`POST /v1/chat`: the model's whole answer (IS-9)."""
        if request.stream:
            raise ValueError("chat takes a request that does not stream")
        return self._ask(
            ChatResponse, "POST", CHAT_PATH, request, self._config.chat_timeout
        )

    def stream_chat(self, request: ChatRequest) -> Iterator[StreamEvent]:
        """`POST /v1/chat` streamed: each token as it comes, then the `done` event.

        `ServerUnavailable` when the first token is later than
        `chat_first_token_timeout`, when the whole answer takes longer than
        `chat_timeout`, or when the stream ends without its `done`.
        """
        if not request.stream:
            raise ValueError("stream_chat takes a request that streams")
        started = self._clock()
        first = self._config.chat_first_token_timeout
        body = json.dumps(request.to_json(), ensure_ascii=False).encode("utf-8")
        headers = {**self._headers(), "Accept": EVENT_STREAM_TYPE}
        try:
            status, lines = self._transport.stream(
                "POST", f"{self._base}{CHAT_PATH}", headers, body, first
            )
        except OSError as error:
            raise ServerUnavailable(f"the server is away: {_why(error)}") from None
        try:
            if status != 200:
                raise ServerUnavailable(
                    _refusal(status, "\n".join(lines).encode("utf-8"))
                )
            ended = False
            for event in iter_stream(lines):
                if self._clock() - started > self._config.chat_timeout:
                    raise ServerUnavailable(
                        f"the chat took over {self._config.chat_timeout:g} s"
                    )
                yield event
                if not isinstance(event, StreamToken):
                    ended = True
                    break
            if not ended:
                raise ServerUnavailable("the chat ended before it was done")
        except OSError as error:
            raise ServerUnavailable(f"the chat stopped: {_why(error)}") from None
        except ContractError as error:
            raise ServerUnavailable(str(error)) from None
        finally:
            lines.close()

    # --- one request -------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        try:
            token = read_token(self._config.token_path)
        except TokenError as error:
            raise ServerUnavailable(str(error)) from None
        return {"Authorization": f"Bearer {token}", "Content-Type": JSON_TYPE}

    def _ask[S: Shape](
        self,
        shape: type[S],
        method: str,
        path: str,
        request: Shape | None,
        timeout: float,
    ) -> S:
        body = (
            None
            if request is None
            else json.dumps(request.to_json(), ensure_ascii=False).encode("utf-8")
        )
        try:
            status, answer = self._transport.send(
                method, f"{self._base}{path}", self._headers(), body, timeout
            )
        except OSError as error:
            raise ServerUnavailable(f"the server is away: {_why(error)}") from None
        if status != 200:
            raise ServerUnavailable(_refusal(status, answer))
        try:
            return shape.from_json(json.loads(answer.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ServerUnavailable(
                f"the server's answer to {path} is not JSON"
            ) from None
        except ContractError as error:
            raise ServerUnavailable(str(error)) from None
