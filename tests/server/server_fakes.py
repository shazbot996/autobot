"""Fakes for the NUC's side of the inference server: no socket, no token of the owner's.

`FakeTransport` stands in for the one place the client opens a socket. It
answers each path with what the test scripted (the contract's examples
unless told otherwise), raises what the test tells it to, and keeps every
request. `FakeWatch` is a watcher that reads what the test says. The token
is a stand-in written in a temporary directory at mode `0600`.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from autobot.config import Config, ServerConfig, parse_config
from autobot.server.client import ServerClient
from autobot.server.contract import Health, contract_dir
from autobot.server.watch import DOWN, JOBS, UP, ServerWatch
from autobot.tui.runner import FunctionTask

TOKEN = "a-test-token-that-is-not-a-secret"

CONFIG_TEXT = """
[host]
nuc_hostname = "nuc"

[audio]
input_device = "pipewire"
output_device = "pipewire"

[[wake_phrases]]
phrase = "autobot"
kind = "text_match"
target = "ack"
"""


def example(name: str) -> Any:
    """One example of `server/contract/`, parsed."""
    return json.loads((contract_dir() / name).read_text(encoding="utf-8"))


def example_text(name: str) -> str:
    return (contract_dir() / name).read_text(encoding="utf-8")


def write_token(directory: Path, token: str = TOKEN, mode: int = 0o600) -> Path:
    """A stand-in token file in `directory`, at `mode`."""
    path = directory / "server-token"
    path.write_text(f"{token}\n", encoding="utf-8")
    path.chmod(mode)
    return path


def server_config(
    tmp_path: Path, *, token: bool = True, **changes: Any
) -> ServerConfig:
    """`[server]` with its token file in `tmp_path`, written unless `token` is false."""
    path = tmp_path / "server-token"
    if token:
        write_token(tmp_path)
    return replace(ServerConfig(), token_file=str(path), **changes)


def config_with(tmp_path: Path, extra: str = "", **server: Any) -> Config:
    """A whole configuration whose `[server]` is `server_config(tmp_path, **server)`."""
    config = parse_config(tomllib.loads(CONFIG_TEXT + extra))
    return replace(config, server=server_config(tmp_path, **server))


class FakeClock:
    """A monotonic clock that moves only when told to."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class Sent:
    """One request the client made."""

    method: str
    path: str
    headers: dict[str, str]
    body: Any
    timeout: float


class FakeLines:
    """A streamed answer's lines; `error` is raised after `error_after` of them."""

    def __init__(
        self, lines: list[str], error: Exception | None = None, error_after: int = 0
    ) -> None:
        self.lines = lines
        self.error = error
        self.error_after = error_after
        self.closed = False

    def __iter__(self) -> Iterator[str]:
        for number, line in enumerate(self.lines):
            if self.error is not None and number == self.error_after:
                raise self.error
            yield line
        if self.error is not None and self.error_after >= len(self.lines):
            raise self.error

    def close(self) -> None:
        self.closed = True


# The contract's example that answers each path.
EXAMPLES = {
    "/health": "health.json",
    "/v1/stt": "stt-response.json",
    "/v1/route": "route-response.json",
    "/v1/chat": "chat-response.json",
}


@dataclass
class FakeTransport:
    """Answers each path as scripted, or raises `error`; keeps what was sent.

    `answers` maps a path to its status and JSON body; a path not in it is
    answered with the contract's example for it. `error` is raised by every
    request while it is set. `on_send` runs with each request before it is
    answered, so a test can move its clock.
    """

    answers: dict[str, tuple[int, Any]] = field(default_factory=dict)
    error: Exception | None = None
    sent: list[Sent] = field(default_factory=list)
    stream_lines: list[str] | None = None
    stream_error: Exception | None = None
    stream_error_after: int = 0
    streams: list[FakeLines] = field(default_factory=list)
    on_send: Callable[[Sent], None] | None = None

    def _record(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> str:
        path = "/" + url.split("/", 3)[3]
        sent = Sent(
            method, path, dict(headers), json.loads(body) if body else None, timeout
        )
        self.sent.append(sent)
        if self.on_send is not None:
            self.on_send(sent)
        if self.error is not None:
            raise self.error
        return path

    def send(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> tuple[int, bytes]:
        path = self._record(method, url, headers, body, timeout)
        if path in self.answers:
            status, data = self.answers[path]
        else:
            status, data = 200, example(EXAMPLES[path])
        raw = data if isinstance(data, bytes) else json.dumps(data).encode("utf-8")
        return status, raw

    def stream(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> tuple[int, FakeLines]:
        path = self._record(method, url, headers, body, timeout)
        if path in self.answers:
            status, data = self.answers[path]
            lines = FakeLines(json.dumps(data).splitlines())
        else:
            status = 200
            text = (
                example_text("chat-stream.txt")
                if self.stream_lines is None
                else "\n".join(self.stream_lines)
            )
            lines = FakeLines(
                text.splitlines(keepends=True),
                self.stream_error,
                self.stream_error_after,
            )
        self.streams.append(lines)
        return status, lines

    def paths(self) -> list[str]:
        return [sent.path for sent in self.sent]


def inline(function: Callable[[], object]) -> FunctionTask:
    """A `spawn` that runs the function before returning."""
    return FunctionTask(function, inline=True)


def health(**not_ready: str) -> Health:
    """The contract's `/health`, with the jobs named in `not_ready` not ready, each for its reason."""
    data = example("health.json")
    for job, reason in not_ready.items():
        data["jobs"][job] = {
            "ready": False,
            "model": data["jobs"][job]["model"],
            "reason": reason,
        }
    return Health.from_json(data)


class FakeWatch(ServerWatch):
    """A watcher that reads what the test says, and asks nothing.

    `FakeWatch()` is a server that is up with every job ready;
    `FakeWatch(DOWN)` is one that is away. `set` changes it.
    """

    def __init__(self, state: str = UP, reason: str = "", **not_ready: str) -> None:
        super().__init__(ServerConfig(), _NoSource())
        self.set(state, reason, **not_ready)

    def set(self, state: str, reason: str = "", **not_ready: str) -> None:
        self.state = state
        self.reason = reason
        self.health = health(**not_ready) if state != DOWN else None
        if state == DOWN and not reason:
            self.reason = "the server is away: timed out"
        if not_ready and not reason:
            self.reason = "; ".join(
                f"{job} not ready ({not_ready[job]})"
                for job in JOBS
                if job in not_ready
            )

    def tick(self) -> None:
        pass


class _NoSource:
    def token_trouble(self) -> str:
        return ""

    def health(self) -> Health:
        raise AssertionError("a fake watcher asks nothing")


def client_on(
    tmp_path: Path,
    transport: FakeTransport,
    clock: FakeClock | None = None,
    **server: Any,
) -> ServerClient:
    """A client on `transport`, with a stand-in token in `tmp_path`."""
    return ServerClient(
        server_config(tmp_path, **server), transport, clock=clock or FakeClock()
    )
