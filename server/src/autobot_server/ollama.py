"""Ollama as the server's language model runtime (IS-10).

Ollama runs on `GAMEON1` bound to `127.0.0.1` only, behind this server, which
is the only thing the NUC talks to. It is reached at `[server] runtime_url`
with the standard library's `urllib`, on its own HTTP API:

- `GET /api/tags` lists the models it has. The server never pulls one: a
  model Ollama does not have is a not-ready reason that names the
  `ollama pull` the owner runs (IS-10).
- `POST /api/chat` generates. `format` is the JSON schema the answer is
  constrained to (IS-9), `options.num_ctx` the context of `[server]
  llm_context`, and `keep_alive` is `-1`, so the model stays resident
  (IS-24). Thinking is turned off: a routing answer has 1.5 s (IS-4).

One request at a time reaches the model: each job admits one running request
(IS-24), and Ollama itself is to run with `OLLAMA_NUM_PARALLEL=1`, which is
a setting of its service and not of a request (`INFRASTRUCTURE.md`).

This is loopback traffic to the owner's own runtime, not a cloud service
(IS-7).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import Any, Protocol

from autobot_server.contract import (
    ROLE_ASSISTANT,
    ROLE_TOOL,
    Message,
    Tokens,
    Tool,
    ToolCall,
)
from autobot_server.job import NotReady
from autobot_server.runtime import Answer, Token, parameters_schema

TAGS_PATH = "/api/tags"
CHAT_PATH = "/api/chat"
# The model stays loaded for good (IS-24).
KEEP_ALIVE = -1
# How long the runtime may take to say which models it has, in seconds.
CHECK_TIMEOUT = 5.0
# How long one read of a generation may take, in seconds: the first answer
# after a start waits for the model to load onto the card.
GENERATE_TIMEOUT = 180.0
# The tag Ollama gives a model pulled without one.
DEFAULT_TAG = ":latest"


class Reply(Protocol):
    """An open HTTP response: its lines, and a close."""

    def __iter__(self) -> Iterator[bytes]: ...

    def close(self) -> None: ...


# Opens a request to the runtime: URL, JSON body or none, timeout.
type Opener = Callable[[str, bytes | None, float], Reply]


def open_url(url: str, body: bytes | None, timeout: float) -> Reply:
    """`urllib`'s opener: a `GET` without a body, a JSON `POST` with one."""
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"} if body is not None else {},
        method="POST" if body is not None else "GET",
    )
    reply: Reply = urllib.request.urlopen(request, timeout=timeout)
    return reply


class Ollama:
    """The language model `model`, served by Ollama at `url`."""

    def __init__(
        self,
        url: str,
        model: str,
        context: int,
        *,
        opener: Opener = open_url,
    ):
        self._url = url.rstrip("/")
        self._model = model
        self._context = context
        self._open = opener

    @property
    def model(self) -> str:
        return self._model

    def check(self) -> None:
        """`NotReady` unless Ollama answers and has the model."""
        data = self._json(TAGS_PATH, None, CHECK_TIMEOUT)
        models = data.get("models")
        names: set[str] = set()
        if isinstance(models, list):
            for entry in models:
                if isinstance(entry, dict):
                    names.update(
                        str(entry[key]) for key in ("name", "model") if key in entry
                    )
        wanted = {self._model, f"{self._model}{DEFAULT_TAG}"}
        if not wanted & names:
            raise NotReady(
                f"the runtime does not have the model {self._model}: "
                f"'ollama pull {self._model}' fetches it (the server never pulls)"
            )

    def generate(
        self,
        system: str,
        messages: Sequence[Message],
        schema: dict[str, Any] | None = None,
        tools: Sequence[Tool] = (),
        stream: bool = False,
    ) -> Answer | Iterator[Token]:
        body = self.request_body(system, messages, schema, tools, stream)
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        reply = self._reply(CHAT_PATH, encoded, GENERATE_TIMEOUT)
        if stream:
            return self._tokens(reply)
        try:
            chunks = [_chunk(line) for line in reply if line.strip()]
        except OSError as error:
            raise NotReady(self._away(error)) from None
        finally:
            reply.close()
        return _answer(chunks)

    def request_body(
        self,
        system: str,
        messages: Sequence[Message],
        schema: dict[str, Any] | None,
        tools: Sequence[Tool],
        stream: bool,
    ) -> dict[str, Any]:
        """The body of `POST /api/chat` for one generation."""
        body: dict[str, Any] = {
            "model": self._model,
            "messages": ollama_messages(system, messages),
            "stream": stream,
            "think": False,
            "keep_alive": KEEP_ALIVE,
            "options": {"num_ctx": self._context},
        }
        if schema is not None:
            body["format"] = schema
        if tools:
            body["tools"] = [ollama_tool(tool) for tool in tools]
        return body

    def _tokens(self, reply: Reply) -> Iterator[Token]:
        chunks: list[dict[str, Any]] = []
        try:
            for line in reply:
                if not line.strip():
                    continue
                chunk = _chunk(line)
                chunks.append(chunk)
                text = _content(chunk)
                if text:
                    yield Token(text)
        except OSError as error:
            raise NotReady(self._away(error)) from None
        finally:
            reply.close()
        yield Token("", final=_answer(chunks))

    def _reply(self, path: str, body: bytes | None, timeout: float) -> Reply:
        try:
            return self._open(f"{self._url}{path}", body, timeout)
        except urllib.error.HTTPError as error:
            raise NotReady(
                f"the runtime refused {path}: {error.code} {_reason(error)}"
            ) from None
        except OSError as error:
            raise NotReady(self._away(error)) from None

    def _json(self, path: str, body: bytes | None, timeout: float) -> dict[str, Any]:
        reply = self._reply(path, body, timeout)
        try:
            raw = b"".join(reply)
        except OSError as error:
            raise NotReady(self._away(error)) from None
        finally:
            reply.close()
        return _chunk(raw)

    def _away(self, error: OSError) -> str:
        reason = (
            getattr(error, "reason", None) or error.strerror or type(error).__name__
        )
        return f"the runtime at {self._url} is not answering: {reason}"


def ollama_messages(system: str, messages: Iterable[Message]) -> list[dict[str, Any]]:
    """The conversation as Ollama takes it, the system prompt first."""
    sent: list[dict[str, Any]] = []
    if system:
        sent.append({"role": "system", "content": system})
    # The name of each tool call, by its id, for the tool message answering it.
    called: dict[str, str] = {}
    for message in messages:
        entry: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.role == ROLE_ASSISTANT and message.tool_calls:
            entry["tool_calls"] = [
                {"function": {"name": call.name, "arguments": call.arguments}}
                for call in message.tool_calls
            ]
            called.update({call.id: call.name for call in message.tool_calls})
        if message.role == ROLE_TOOL and message.tool_call_id in called:
            entry["tool_name"] = called[message.tool_call_id]
        sent.append(entry)
    return sent


def ollama_tool(tool: Tool) -> dict[str, Any]:
    """One tool as the function schema Ollama takes (IS-21)."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": parameters_schema(tool.parameters),
        },
    }


def _chunk(line: bytes) -> dict[str, Any]:
    try:
        data = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise NotReady("the runtime answered something that is not JSON") from None
    if not isinstance(data, dict):
        raise NotReady("the runtime answered something that is not a JSON object")
    error = data.get("error")
    if error:
        raise NotReady(f"the runtime failed: {str(error).splitlines()[0]}")
    return data


def _content(chunk: dict[str, Any]) -> str:
    message = chunk.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    return content if isinstance(content, str) else ""


def _answer(chunks: Sequence[dict[str, Any]]) -> Answer:
    """The whole answer from every chunk Ollama sent: one, or a stream's."""
    calls: list[ToolCall] = []
    for chunk in chunks:
        message = chunk.get("message")
        found = message.get("tool_calls") if isinstance(message, dict) else None
        for call in found if isinstance(found, list) else ():
            function = call.get("function") if isinstance(call, dict) else None
            if not isinstance(function, dict):
                continue
            arguments = function.get("arguments")
            calls.append(
                ToolCall(
                    id=f"call-{len(calls) + 1}",
                    name=str(function.get("name", "")),
                    arguments=dict(arguments) if isinstance(arguments, dict) else {},
                )
            )
    last = chunks[-1] if chunks else {}
    return Answer(
        content="".join(_content(chunk) for chunk in chunks),
        tool_calls=tuple(calls),
        tokens=Tokens(
            prompt=_count(last.get("prompt_eval_count")),
            completion=_count(last.get("eval_count")),
        ),
    )


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _reason(error: urllib.error.HTTPError) -> str:
    """The runtime's own one-line reason for an HTTP error, when it gave one."""
    try:
        data = json.loads(error.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return str(error.reason)
    reason = data.get("error") if isinstance(data, dict) else None
    return str(reason).splitlines()[0] if reason else str(error.reason)
