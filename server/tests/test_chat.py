"""`autobot_server.chat` and the runtime behind it, on fakes (IS-9, IS-10, IS-21, IS-24)."""

from __future__ import annotations

import io
import json
import urllib.error
from collections.abc import Iterator
from typing import Any

import pytest

from autobot_server.app import Response
from autobot_server.chat import CHAT_PATH, EventStream, chat_endpoint
from autobot_server.contract import (
    ChatRequest,
    Message,
    Parameter,
    Tokens,
    Tool,
    ToolCall,
    contract_dir,
)
from autobot_server.job import NOT_READY, READY, Job, NotReady
from autobot_server.ollama import KEEP_ALIVE, Ollama, Reply
from autobot_server.runtime import (
    NO_MODEL_CHOSEN,
    Answer,
    LanguageModel,
    Request,
    Token,
    parameters_schema,
    whole,
)
from tests.server_fakes import BEARER, FakeClock, FakeRuntime, llm_job, make_app


def example(name: str) -> Any:
    return json.loads((contract_dir() / name).read_text(encoding="utf-8"))


def body_of(response: Response) -> dict[str, Any]:
    assert isinstance(response.body, bytes)
    data = json.loads(response.body)
    assert isinstance(data, dict)
    return data


def post(
    runtime: FakeRuntime, request: dict[str, Any]
) -> tuple[Response, Job[Request, Answer | Iterator[Token]]]:
    job = llm_job("chat", runtime)
    app = make_app(endpoints={CHAT_PATH: chat_endpoint(job, runtime.model)})
    body = json.dumps(request).encode("utf-8")
    return app.handle("POST", CHAT_PATH, BEARER, body), job


def streamed(response: Response) -> str:
    assert not isinstance(response.body, bytes)
    return b"".join(response.body).decode("utf-8")


# --- the whole answer ---------------------------------------------------------


def test_the_contract_example_yields_the_contract_response() -> None:
    expected = example("chat-response.json")
    runtime = FakeRuntime(
        Answer(
            content=expected["message"]["content"],
            tokens=Tokens(prompt=486, completion=24),
        )
    )
    response, _ = post(runtime, example("chat-request.json"))
    assert response.status == 200
    assert body_of(response) == expected


def test_the_request_reaches_the_model_whole() -> None:
    runtime = FakeRuntime()
    request = example("chat-request.json")
    post(runtime, request)
    (call,) = runtime.calls
    sent = ChatRequest.from_json(request)
    assert call.system == sent.system
    assert call.messages == sent.messages
    assert call.schema == sent.response_schema
    assert call.tools == sent.tools
    assert call.stream is False


def test_tool_calls_come_back_and_are_run_nowhere_here() -> None:
    call = ToolCall(id="call-1", name="set_timer", arguments={"minutes": 9})
    runtime = FakeRuntime(Answer(content="", tool_calls=(call,)))
    response, _ = post(runtime, example("chat-request.json"))
    assert body_of(response)["message"] == {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": "call-1", "name": "set_timer", "arguments": {"minutes": 9}}
        ],
    }


def test_a_request_that_is_not_the_contract_is_400() -> None:
    response, _ = post(FakeRuntime(), {"system": "", "messages": [{"role": "robot"}]})
    assert response.status == 400


# --- the stream ---------------------------------------------------------------


def stream_request() -> dict[str, Any]:
    return {
        "system": "You are autobot.",
        "messages": [{"role": "user", "content": "what time is it"}],
        "stream": True,
    }


def test_a_streamed_chat_produces_the_lines_of_the_contract_stream() -> None:
    runtime = FakeRuntime(
        Answer(
            content="It's a quarter past three. Anything else?",
            tokens=Tokens(prompt=212, completion=9),
        ),
        pieces=("It's", " a quarter", " past three.", " Anything", " else?"),
    )
    response, job = post(runtime, stream_request())
    assert response.status == 200
    assert dict(response.headers)["Content-Type"] == "text/event-stream"
    expected = (contract_dir() / "chat-stream.txt").read_text(encoding="utf-8")
    assert streamed(response) == expected
    assert runtime.calls[0].stream is True
    assert job.admitted == 0


def test_a_stream_holds_its_place_until_it_ends() -> None:
    runtime = FakeRuntime(Answer(content="one two three"))
    response, job = post(runtime, stream_request())
    assert not isinstance(response.body, bytes)
    events = iter(response.body)
    assert next(events) == b'event: token\ndata: {"text": "one"}\n\n'
    assert job.admitted == 1
    rest = b"".join(events).decode("utf-8")
    assert rest.endswith(
        'event: done\ndata: {"model": "qwen3:8b", '
        '"tokens": {"prompt": 0, "completion": 0}}\n\n'
    )
    assert job.admitted == 0


def test_a_stream_the_nuc_hangs_up_on_gives_its_place_back() -> None:
    runtime = FakeRuntime(Answer(content="one two three"))
    response, job = post(runtime, stream_request())
    assert not isinstance(response.body, bytes)
    next(iter(response.body))
    assert job.admitted == 1
    assert isinstance(response.body, EventStream)
    response.body.close()
    assert job.admitted == 0
    # And a stream nobody reads gives it back when it is dropped.
    response, job = post(runtime, stream_request())
    assert job.admitted == 1
    del response
    assert job.admitted == 0


def test_streamed_tool_calls_come_in_the_done_event() -> None:
    call = ToolCall(id="call-1", name="set_timer", arguments={"minutes": 9})
    runtime = FakeRuntime(Answer(content="", tool_calls=(call,)), pieces=())
    response, _ = post(runtime, stream_request())
    assert streamed(response) == (
        'event: done\ndata: {"model": "qwen3:8b", '
        '"tokens": {"prompt": 0, "completion": 0}, "tool_calls": '
        '[{"id": "call-1", "name": "set_timer", "arguments": {"minutes": 9}}]}\n\n'
    )


def test_a_stream_that_loses_the_gpu_ends_without_done_and_the_job_is_not_ready() -> (
    None
):
    runtime = FakeRuntime(Answer(content="one two three"))
    runtime.stream_error = RuntimeError("CUDA error: out of memory")
    response, job = post(runtime, stream_request())
    assert streamed(response) == 'event: token\ndata: {"text": "one"}\n\n'
    assert job.state == NOT_READY
    assert job.reason == "CUDA error: out of memory"
    assert job.admitted == 0


def test_a_stream_is_refused_before_it_starts_when_the_model_fails() -> None:
    runtime = FakeRuntime()
    job = llm_job("chat", runtime)
    app = make_app(endpoints={CHAT_PATH: chat_endpoint(job, runtime.model)})
    runtime.error = NotReady("the runtime at http://127.0.0.1:11434 is not answering")
    body = json.dumps(stream_request()).encode("utf-8")
    response = app.handle("POST", CHAT_PATH, BEARER, body)
    assert response.status == 503
    assert job.admitted == 0


# --- the engine ---------------------------------------------------------------


def test_the_job_is_ready_once_the_runtime_has_the_model_and_answered() -> None:
    runtime = FakeRuntime()
    job: Job[Request, Answer | Iterator[Token]] = Job(
        "chat", LanguageModel(runtime), clock=FakeClock()
    )
    job.start()
    assert job.state == READY
    assert runtime.checks >= 1 and len(runtime.calls) == 1


def test_a_missing_model_is_a_reason_and_is_looked_for_again() -> None:
    clock = FakeClock()
    runtime = FakeRuntime()
    runtime.missing = True
    job: Job[Request, Answer | Iterator[Token]] = Job(
        "chat", LanguageModel(runtime), clock=clock, retry_seconds=30.0
    )
    job.start()
    assert job.state == NOT_READY
    assert job.health().reason.startswith(
        "the runtime does not have the model qwen3:8b"
    )
    assert runtime.calls == []
    runtime.missing = False
    clock.advance(31.0)
    job.tick()
    assert job.state == READY


def test_no_model_chosen_is_a_reason() -> None:
    engine = LanguageModel(FakeRuntime(model=""))
    assert engine.model == "(none chosen)"
    with pytest.raises(NotReady) as refused:
        engine.load()
    assert str(refused.value) == NO_MODEL_CHOSEN


def test_whole_gathers_a_stream() -> None:
    final = Answer(content="a b", tokens=Tokens(prompt=1, completion=2))
    assert whole(iter((Token("a"), Token(" b"), Token("", final=final)))) == final
    assert whole(iter((Token("a"), Token(" b")))) == Answer(content="a b")


def test_parameters_become_a_json_schema() -> None:
    schema = parameters_schema(
        (
            Parameter(name="minutes", type="integer", required=True),
            Parameter(name="label", type="string", values=("eggs", "tea")),
        )
    )
    assert schema == {
        "type": "object",
        "properties": {
            "minutes": {"type": "integer"},
            "label": {"type": "string", "enum": ["eggs", "tea"]},
        },
        "required": ["minutes"],
    }


# --- Ollama, on a fake opener ---------------------------------------------------


class FakeReply:
    def __init__(self, lines: list[bytes]):
        self.lines = lines
        self.closed = False

    def __iter__(self) -> Iterator[bytes]:
        return iter(self.lines)

    def close(self) -> None:
        self.closed = True


class FakeOllama:
    """Answers `/api/tags` and `/api/chat` as Ollama does, and keeps the requests."""

    def __init__(self, models: tuple[str, ...] = ("qwen3:8b",)):
        self.models = models
        self.chat: list[bytes] = []
        self.requests: list[tuple[str, dict[str, Any] | None, float]] = []
        self.error: Exception | None = None
        self.replies: list[FakeReply] = []

    def __call__(self, url: str, body: bytes | None, timeout: float) -> Reply:
        self.requests.append((url, json.loads(body) if body else None, timeout))
        if self.error is not None:
            raise self.error
        if url.endswith("/api/tags"):
            data = {"models": [{"name": name, "model": name} for name in self.models]}
            lines = [json.dumps(data).encode("utf-8")]
        else:
            lines = self.chat
        self.replies.append(FakeReply(lines))
        return self.replies[-1]


def ollama(server: FakeOllama, model: str = "qwen3:8b") -> Ollama:
    return Ollama("http://127.0.0.1:11434/", model, 8192, opener=server)


def test_check_passes_when_ollama_has_the_model_and_never_pulls() -> None:
    server = FakeOllama(models=("llama3.1:latest", "qwen3:8b"))
    ollama(server).check()
    ollama(server, "llama3.1").check()
    assert [url for url, _, _ in server.requests] == [
        "http://127.0.0.1:11434/api/tags"
    ] * 2
    with pytest.raises(NotReady) as refused:
        ollama(server, "other:8b").check()
    assert str(refused.value) == (
        "the runtime does not have the model other:8b: "
        "'ollama pull other:8b' fetches it (the server never pulls)"
    )
    assert not any("pull" in url for url, _, _ in server.requests)
    assert all(reply.closed for reply in server.replies)


def test_a_runtime_that_is_down_is_a_one_line_reason() -> None:
    server = FakeOllama()
    server.error = urllib.error.URLError("Connection refused")
    with pytest.raises(NotReady) as refused:
        ollama(server).check()
    assert str(refused.value) == (
        "the runtime at http://127.0.0.1:11434 is not answering: Connection refused"
    )
    server.error = urllib.error.HTTPError(
        "http://127.0.0.1:11434/api/chat",
        404,
        "Not Found",
        None,  # type: ignore[arg-type]
        io.BytesIO(b'{"error": "model not found"}'),
    )
    with pytest.raises(NotReady) as refused:
        ollama(server).generate("", ())
    assert str(refused.value) == "the runtime refused /api/chat: 404 model not found"


def test_generate_sends_the_schema_the_context_and_keeps_the_model_resident() -> None:
    server = FakeOllama()
    server.chat = [
        json.dumps(
            {
                "message": {"role": "assistant", "content": '{"summary": "Hi."}'},
                "done": True,
                "prompt_eval_count": 40,
                "eval_count": 7,
            }
        ).encode("utf-8")
    ]
    request = ChatRequest.from_json(example("chat-request.json"))
    answer = ollama(server).generate(
        request.system, request.messages, request.response_schema, request.tools
    )
    assert answer == Answer(
        content='{"summary": "Hi."}', tokens=Tokens(prompt=40, completion=7)
    )
    url, body, _ = server.requests[0]
    assert url == "http://127.0.0.1:11434/api/chat"
    assert body is not None
    assert body["model"] == "qwen3:8b"
    assert body["stream"] is False
    assert body["think"] is False
    assert body["keep_alive"] == KEEP_ALIVE
    assert body["options"] == {"num_ctx": 8192}
    assert body["format"] == request.response_schema
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "set_timer",
                "description": "Starts a timer on the NUC that rings when it ends.",
                "parameters": {
                    "type": "object",
                    "properties": {"minutes": {"type": "integer"}},
                    "required": ["minutes"],
                },
            },
        }
    ]
    roles = [message["role"] for message in body["messages"]]
    assert roles == ["system", "user", "assistant", "user", "assistant", "tool"]
    assert body["messages"][4]["tool_calls"] == [
        {"function": {"name": "set_timer", "arguments": {"minutes": 9}}}
    ]
    assert body["messages"][5]["tool_name"] == "set_timer"
    assert server.replies[0].closed


def test_generate_without_a_schema_or_tools_sends_neither() -> None:
    server = FakeOllama()
    server.chat = [b'{"message": {"content": "ready"}, "done": true}']
    ollama(server).generate("", (Message(role="user", content="hi"),))
    _, body, _ = server.requests[0]
    assert body is not None
    assert "format" not in body and "tools" not in body
    assert body["messages"] == [{"role": "user", "content": "hi"}]


def test_a_stream_yields_tokens_then_the_whole_answer_with_its_tool_calls() -> None:
    server = FakeOllama()
    server.chat = [
        b'{"message": {"content": "It\'s"}, "done": false}\n',
        b'{"message": {"content": " three."}, "done": false}\n',
        b"\n",
        json.dumps(
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "set_timer", "arguments": {"minutes": 9}}}
                    ],
                },
                "done": False,
            }
        ).encode("utf-8"),
        json.dumps(
            {
                "message": {"content": ""},
                "done": True,
                "prompt_eval_count": 12,
                "eval_count": 5,
            }
        ).encode("utf-8"),
    ]
    tokens = ollama(server).generate(
        "", (Message(role="user", content="time"),), stream=True
    )
    assert not isinstance(tokens, Answer)
    got = list(tokens)
    assert [token.text for token in got] == ["It's", " three.", ""]
    assert got[-1].final == Answer(
        content="It's three.",
        tool_calls=(ToolCall(id="call-1", name="set_timer", arguments={"minutes": 9}),),
        tokens=Tokens(prompt=12, completion=5),
    )
    assert server.requests[0][1] is not None
    assert server.requests[0][1]["stream"] is True
    assert server.replies[0].closed


def test_an_error_line_from_ollama_is_a_reason() -> None:
    server = FakeOllama()
    server.chat = [b'{"error": "CUDA error: out of memory\\nmore"}']
    with pytest.raises(NotReady) as refused:
        ollama(server).generate("", ())
    assert str(refused.value) == "the runtime failed: CUDA error: out of memory"
    server.chat = [b"<html>"]
    with pytest.raises(NotReady):
        ollama(server).generate("", ())


def test_a_tool_without_parameters_is_an_empty_object_schema() -> None:
    assert parameters_schema(Tool(name="t", description="T.").parameters) == {
        "type": "object",
        "properties": {},
        "required": [],
    }
