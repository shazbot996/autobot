"""`make server-check`: a loopback smoke test of every endpoint (IS-1, IS-25).

    python -m autobot_server.check

Run on `GAMEON1` against the server that is up there, at `127.0.0.1` on the
port of `[server] url`, with the server's own token. It is the one caller
the server has besides the NUC (IS-1). It asks:

- `GET /health`, and that the contract version is this checkout's;
- `POST /v1/stt`, with the committed synthetic fixture
  `tests/fixtures/audio/hey_autobot.wav`, which is not a recording of the
  owner (TS-5), and the hints of the contract's example;
- `POST /v1/route` and `POST /v1/chat`, with the contract's examples.

Each answer must be `200` and have the contract's shape. One line is printed
per check, and the exit status is non-zero when any failed. Nothing is
written anywhere, and the token is never printed.
"""

from __future__ import annotations

import json
import socket
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from autobot_server.auth import TokenError, read_token, token_path
from autobot_server.config import (
    ServerConfigError,
    ServerSettings,
    load_settings,
    resolve_checkout_path,
)
from autobot_server.contract import (
    NO_AGENT,
    ChatResponse,
    ContractError,
    Health,
    RouteRequest,
    RouteResponse,
    SttRequest,
    SttResponse,
    contract_dir,
    contract_version,
)
from autobot_server.hostcheck import WrongHostError, require_server_host

LOOPBACK = "127.0.0.1"
FIXTURE = Path("tests/fixtures/audio/hey_autobot.wav")
# How long one answer may take, in seconds: a first request may find the
# model still loading onto the card.
TIMEOUT = 120.0

# Sends one request to the server: method, path, body or none; the status and the body back.
type Sender = Callable[[str, str, bytes | None], tuple[int, bytes]]


class CheckError(Exception):
    """A check could not be made or failed. The message is one line."""


@dataclass(frozen=True)
class Result:
    """One check's outcome, as its printed line has it."""

    name: str
    ok: bool
    detail: str

    def line(self) -> str:
        return f"{'ok  ' if self.ok else 'FAIL'} {self.name}: {self.detail}"


def loopback(settings: ServerSettings, timeout: float = TIMEOUT) -> Sender:
    """A sender to this host's own server, carrying the server's token."""
    token = read_token(token_path())
    base = f"http://{LOOPBACK}:{settings.port}"

    def send(method: str, path: str, body: bytes | None) -> tuple[int, bytes]:
        headers = {"Authorization": f"Bearer {token}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{base}{path}", data=body, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as reply:
                return int(reply.status), reply.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()
        except OSError as error:
            reason = getattr(error, "reason", None) or error.strerror or error
            raise CheckError(
                f"the server at {base} is not answering: {reason}"
            ) from None

    return send


def ask(send: Sender, method: str, path: str, body: object | None = None) -> object:
    """The parsed JSON of a `200` answer; `CheckError` with the server's reason else."""
    encoded = None if body is None else json.dumps(body).encode("utf-8")
    status, raw = send(method, path, encoded)
    try:
        data: object = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CheckError(f"{status}, and a body that is not JSON") from None
    if status != 200:
        reason = data.get("error") if isinstance(data, dict) else None
        raise CheckError(f"{status} {reason or ''}".strip())
    return data


def read_health(send: Sender) -> Health:
    """The server's `/health`, as the contract's shape."""
    try:
        return Health.from_json(ask(send, "GET", "/health"))
    except ContractError as error:
        raise CheckError(str(error)) from None


def check_health(send: Sender) -> str:
    health = read_health(send)
    expected = contract_version()
    if health.contract_version != expected:
        raise CheckError(
            f"the server's contract is {health.contract_version}, "
            f"this checkout's is {expected}: restart the server"
        )
    waiting = [
        f"{name} not ready ({getattr(health.jobs, name).reason})"
        for name in ("stt", "route", "chat")
        if not getattr(health.jobs, name).ready
    ]
    if waiting:
        raise CheckError("; ".join(waiting))
    return (
        f"version {health.server_version}, contract {health.contract_version}, "
        f"stt, route and chat ready; {health.gpu.free_mib} MiB of the card free"
    )


def check_stt(send: Sender, audio: bytes) -> str:
    hints = SttRequest.from_json(_example("stt-request.json")).hints
    request = SttRequest.from_wav(audio, hints)
    answer = SttResponse.from_json(ask(send, "POST", "/v1/stt", request.to_json()))
    return (
        f"{answer.text!r} by {answer.model}, {answer.duration_seconds:g} s of "
        f"audio in {answer.elapsed_seconds:g} s"
    )


def check_route(send: Sender) -> str:
    example = _example("route-request.json")
    request = RouteRequest.from_json(example)
    answer = RouteResponse.from_json(ask(send, "POST", "/v1/route", example))
    names = {agent.name for agent in request.agents} | {NO_AGENT}
    if answer.agent not in names:
        raise CheckError(f"the answer names {answer.agent!r}, which is not on the list")
    return (
        f"{request.transcript!r} -> {answer.agent}"
        f"{' ' + answer.mode if answer.mode else ''} "
        f"({answer.confidence:.2f}) {answer.parameters or ''}".rstrip()
    )


def check_chat(send: Sender) -> str:
    answer = ChatResponse.from_json(
        ask(send, "POST", "/v1/chat", _example("chat-request.json"))
    )
    said = answer.message.content or f"{len(answer.message.tool_calls)} tool call(s)"
    return (
        f"{said!r} by {answer.model}, "
        f"{answer.tokens.prompt}+{answer.tokens.completion} tokens"
    )


def run_checks(send: Sender, audio: bytes) -> list[Result]:
    """Every check, in order, each with its line; none stops the others."""
    checks: tuple[tuple[str, Callable[[], str]], ...] = (
        ("GET /health", lambda: check_health(send)),
        ("POST /v1/stt", lambda: check_stt(send, audio)),
        ("POST /v1/route", lambda: check_route(send)),
        ("POST /v1/chat", lambda: check_chat(send)),
    )
    results: list[Result] = []
    for name, check in checks:
        try:
            results.append(Result(name, True, check()))
        except (CheckError, ContractError) as error:
            results.append(Result(name, False, str(error)))
    return results


def _example(name: str) -> object:
    path = contract_dir() / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise CheckError(f"cannot read the contract example {path}") from None


def main(hostname: Callable[[], str] = socket.gethostname) -> int:
    try:
        settings = load_settings()
        require_server_host(hostname(), settings.hostname)
        send = loopback(settings)
        fixture = resolve_checkout_path(FIXTURE)
        try:
            audio = fixture.read_bytes()
        except OSError:
            raise CheckError(f"the fixture {fixture} is missing") from None
    except (CheckError, ServerConfigError, TokenError, WrongHostError) as error:
        print(error, file=sys.stderr)
        return 1
    results = run_checks(send, audio)
    for result in results:
        print(result.line())
    failed = sum(not result.ok for result in results)
    if failed:
        print(f"{failed} of {len(results)} checks failed", file=sys.stderr)
        return 1
    print("the server answers every endpoint")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
