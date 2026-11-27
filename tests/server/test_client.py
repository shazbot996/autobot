"""`autobot.server.client`: each endpoint, its timeout and its one failure (IS-4, IS-13)."""

from __future__ import annotations

from pathlib import Path

import pytest

from autobot.server import ping
from autobot.server.client import ServerClient, ServerUnavailable
from autobot.server.contract import (
    ChatRequest,
    ChatResponse,
    Health,
    Message,
    RouteRequest,
    RouteResponse,
    StreamDone,
    StreamToken,
    SttResponse,
    Tokens,
)
from autobot.server.token import (
    NO_TOKEN,
    TokenError,
    main,
    read_token,
)
from autobot.server.token import (
    write_token as save,
)
from server.server_fakes import (
    TOKEN,
    FakeClock,
    FakeTransport,
    client_on,
    example,
    server_config,
    write_token,
)


def chat_request(stream: bool = False) -> ChatRequest:
    return ChatRequest(
        system="You are autobot.",
        messages=(Message(role="user", content="what time is it"),),
        stream=stream,
    )


# --- the endpoints ------------------------------------------------------------


def test_every_request_carries_the_bearer_token_and_its_timeout(tmp_path: Path) -> None:
    transport = FakeTransport()
    client = client_on(tmp_path, transport)
    assert client.health() == Health.from_json(example("health.json"))
    assert client.stt(b"RIFF", 2.0, ("autobot", "weather")) == SttResponse.from_json(
        example("stt-response.json")
    )
    route = RouteRequest.from_json(example("route-request.json"))
    assert client.route(route) == RouteResponse.from_json(
        example("route-response.json")
    )
    assert client.chat(chat_request()) == ChatResponse.from_json(
        example("chat-response.json")
    )
    assert [(sent.method, sent.path, sent.timeout) for sent in transport.sent] == [
        ("GET", "/health", 1.0),
        # 0.5 s plus 0.25 s for each of the clip's two seconds.
        ("POST", "/v1/stt", 1.0),
        ("POST", "/v1/route", 1.5),
        ("POST", "/v1/chat", 30.0),
    ]
    assert all(
        sent.headers["Authorization"] == f"Bearer {TOKEN}" for sent in transport.sent
    )
    assert transport.sent[1].body == {
        "audio_base64": "UklGRg==",
        "hints": ["autobot", "weather"],
    }
    assert transport.sent[2].body == example("route-request.json")


@pytest.mark.parametrize(
    ("error", "line"),
    [
        (TimeoutError("timed out"), "the server is away: timed out"),
        (
            ConnectionRefusedError(111, "Connection refused"),
            "the server is away: Connection refused",
        ),
        (OSError(113, "No route to host"), "the server is away: No route to host"),
    ],
)
def test_a_server_that_does_not_answer_is_one_failure_and_no_retry(
    tmp_path: Path, error: Exception, line: str
) -> None:
    transport = FakeTransport(error=error)
    client = client_on(tmp_path, transport)
    with pytest.raises(ServerUnavailable) as away:
        client.stt(b"RIFF", 1.0)
    assert str(away.value) == line
    assert len(transport.sent) == 1


def test_a_status_other_than_200_says_the_servers_own_reason(tmp_path: Path) -> None:
    transport = FakeTransport(
        answers={
            "/v1/route": (503, {"error": "route is busy: try again in a moment"}),
            "/health": (401, {"error": "missing or wrong bearer token"}),
            "/v1/chat": (502, b"<html>"),
        }
    )
    client = client_on(tmp_path, transport)
    with pytest.raises(ServerUnavailable) as busy:
        client.route(RouteRequest.from_json(example("route-request.json")))
    assert str(busy.value) == (
        "the server answered 503: route is busy: try again in a moment"
    )
    with pytest.raises(ServerUnavailable) as refused:
        client.health()
    assert (
        str(refused.value) == "the server answered 401: missing or wrong bearer token"
    )
    with pytest.raises(ServerUnavailable) as broken:
        client.chat(chat_request())
    assert str(broken.value) == "the server answered 502"
    assert len(transport.sent) == 3


def test_an_answer_that_is_not_the_contract_is_a_failure(tmp_path: Path) -> None:
    transport = FakeTransport(
        answers={"/health": (200, {"server_version": "0.1.0"}), "/v1/stt": (200, b"x")}
    )
    client = client_on(tmp_path, transport)
    with pytest.raises(ServerUnavailable) as wrong:
        client.health()
    assert str(wrong.value) == "Health is missing the field: contract_version"
    with pytest.raises(ServerUnavailable) as garbage:
        client.stt(b"RIFF", 1.0)
    assert str(garbage.value) == "the server's answer to /v1/stt is not JSON"


def test_without_a_token_nothing_is_sent(tmp_path: Path) -> None:
    transport = FakeTransport()
    client = ServerClient(server_config(tmp_path, token=False), transport)
    assert client.token_trouble() == "no server token: run make server-token"
    with pytest.raises(ServerUnavailable) as missing:
        client.health()
    assert str(missing.value) == NO_TOKEN
    assert transport.sent == []


# --- the stream ---------------------------------------------------------------


def test_a_streamed_chat_yields_the_tokens_then_done(tmp_path: Path) -> None:
    transport = FakeTransport()
    client = client_on(tmp_path, transport)
    events = list(client.stream_chat(chat_request(stream=True)))
    assert events == [
        StreamToken(text="It's"),
        StreamToken(text=" a quarter"),
        StreamToken(text=" past three."),
        StreamToken(text=" Anything"),
        StreamToken(text=" else?"),
        StreamDone(model="qwen3:8b", tokens=Tokens(prompt=212, completion=9)),
    ]
    (sent,) = transport.sent
    # Each wait for a line is bounded by the time allowed to the first token.
    assert (sent.path, sent.timeout) == ("/v1/chat", 3.0)
    assert sent.body["stream"] is True
    assert sent.headers["Authorization"] == f"Bearer {TOKEN}"
    assert transport.streams[0].closed


def test_a_first_token_that_is_late_raises(tmp_path: Path) -> None:
    transport = FakeTransport(stream_error=TimeoutError("timed out"))
    client = client_on(tmp_path, transport)
    with pytest.raises(ServerUnavailable) as late:
        list(client.stream_chat(chat_request(stream=True)))
    assert str(late.value) == "the chat stopped: timed out"
    assert transport.streams[0].closed


def test_a_stream_cut_short_or_too_long_raises_after_what_came(tmp_path: Path) -> None:
    transport = FakeTransport(
        stream_lines=["event: token", 'data: {"text": "It\'s"}', "", ""]
    )
    client = client_on(tmp_path, transport)
    events = client.stream_chat(chat_request(stream=True))
    assert next(events) == StreamToken(text="It's")
    with pytest.raises(ServerUnavailable) as cut:
        next(events)
    assert str(cut.value) == "the chat ended before it was done"

    clock = FakeClock()
    transport = FakeTransport()
    client = client_on(tmp_path, transport, clock)
    with pytest.raises(ServerUnavailable) as slow:
        for _event in client.stream_chat(chat_request(stream=True)):
            clock.advance(20.0)
    assert str(slow.value) == "the chat took over 30 s"

    transport = FakeTransport(answers={"/v1/chat": (503, {"error": "chat is busy"})})
    with pytest.raises(ServerUnavailable) as busy:
        list(client_on(tmp_path, transport).stream_chat(chat_request(stream=True)))
    assert str(busy.value) == "the server answered 503: chat is busy"


def test_the_two_chat_calls_refuse_the_other_ones_request(tmp_path: Path) -> None:
    client = client_on(tmp_path, FakeTransport())
    with pytest.raises(ValueError, match="does not stream"):
        client.chat(chat_request(stream=True))
    with pytest.raises(ValueError, match="streams"):
        list(client.stream_chat(chat_request()))


# --- the token file -------------------------------------------------------------


def test_the_token_file_must_be_0600_and_hold_a_token(tmp_path: Path) -> None:
    path = write_token(tmp_path)
    assert read_token(path) == TOKEN
    path.chmod(0o644)
    with pytest.raises(TokenError) as loose:
        read_token(path)
    assert "has mode 0644, not 0600" in str(loose.value)
    assert TOKEN not in str(loose.value)
    path.chmod(0o600)
    path.write_text("\n")
    with pytest.raises(TokenError) as empty:
        read_token(path)
    assert str(empty.value) == NO_TOKEN


def test_the_token_is_entered_hidden_and_written_at_0600(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "autobot.toml"
    target = tmp_path / "secrets" / "server-token"
    config.write_text(
        '[host]\n[audio]\ninput_device = "pipewire"\noutput_device = "pipewire"\n'
        '[[wake_phrases]]\nphrase = "autobot"\nkind = "text_match"\ntarget = "ack"\n'
        f'[server]\ntoken_file = "{target}"\n'
    )
    monkeypatch.setenv("AUTOBOT_CONFIG", str(config))
    assert main(prompt=lambda _question: "  entered-by-the-owner \n") == 0
    assert read_token(target) == "entered-by-the-owner"
    assert target.stat().st_mode & 0o777 == 0o600
    printed = capsys.readouterr()
    assert str(target) in printed.out
    assert "entered-by-the-owner" not in printed.out + printed.err
    # Nothing that is not one token is written.
    assert main(prompt=lambda _question: "two words") == 1
    assert main(prompt=lambda _question: "") == 1
    assert read_token(target) == "entered-by-the-owner"
    save(target, "another")
    assert read_token(target) == "another"


# --- make server-ping -----------------------------------------------------------


def test_ping_prints_up_and_the_contract_version(tmp_path: Path) -> None:
    config = server_config(tmp_path)
    watch = ping.ping(config, FakeTransport())
    lines = ping.describe(config, watch)
    assert lines[0] == "up"
    assert lines[1] == ("server: http://192.168.86.53:8300, version 0.1.0, contract 1")
    assert "  stt   large-v3-turbo: ready" in lines
    assert lines[-1] == "gpu: 7650 MiB used, 3360 MiB free"


def test_ping_says_why_the_server_is_not_up(tmp_path: Path) -> None:
    config = server_config(tmp_path)
    away = ping.ping(config, FakeTransport(error=TimeoutError("timed out")))
    assert ping.describe(config, away)[0] == "down: the server is away: timed out"

    health = example("health.json")
    health["jobs"]["chat"] = {"ready": False, "model": "", "reason": "loading"}
    degraded = ping.ping(config, FakeTransport(answers={"/health": (200, health)}))
    lines = ping.describe(config, degraded)
    assert lines[0] == "degraded: chat not ready (loading)"
    assert "  chat  : not ready: loading" in lines

    health = example("health.json")
    health["contract_version"] = "2"
    other = ping.ping(config, FakeTransport(answers={"/health": (200, health)}))
    assert ping.describe(config, other)[0] == "down: contract 2 is not 1"

    no_token = server_config(tmp_path / "none", token=False)
    assert ping.describe(no_token, ping.ping(no_token, FakeTransport()))[0] == (
        "down: no server token: run make server-token"
    )
    off = server_config(tmp_path, enabled=False)
    assert ping.describe(off, ping.ping(off, FakeTransport()))[0] == (
        "off: [server] enabled is false"
    )
