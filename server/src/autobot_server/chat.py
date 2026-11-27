"""Chat: `POST /v1/chat` (IS-9, IS-19, IS-21).

The endpoint has autobot's own shape. A request carries everything the model
needs and the server keeps none of it (IS-5): the agent's system prompt, the
conversation so far, an optional `response_schema` the answer is generated
against (AG-4's two parts, taken apart on the NUC by the agent) and the
schemas of the tools the model may call.

The answer is the model's message: its text, in the shape of
`response_schema` when one was sent, or its tool calls. A tool call is only
reported: the NUC runs it and sends the result in its next request, and the
server executes nothing (IS-21).

With `stream: true` the answer goes out as server-sent events on the same
port: one `event: token` for each piece as the model produces it, then one
`event: done` with the model's name, the tokens used and any tool calls.
The request keeps its place in the job until the stream ends (IS-24). A
stream that fails part-way simply ends without its `done`, which the NUC
reads as the server having gone away (IS-4).
"""

from __future__ import annotations

from collections.abc import Generator, Iterator

from autobot_server.app import (
    EVENT_STREAM_TYPE,
    Endpoint,
    Response,
    json_response,
    request_json,
)
from autobot_server.contract import (
    ROLE_ASSISTANT,
    ChatRequest,
    ChatResponse,
    Message,
    StreamDone,
    StreamToken,
    format_event,
)
from autobot_server.job import Job
from autobot_server.runtime import Answer, Request, Token, whole

CHAT_PATH = "/v1/chat"


def chat_request(request: ChatRequest) -> Request:
    """The call of the model for one chat request."""
    return Request(
        system=request.system,
        messages=request.messages,
        schema=request.response_schema,
        tools=request.tools,
        stream=request.stream,
    )


def chat_response(answer: Answer, model: str) -> ChatResponse:
    """The whole answer as the IS-9 body."""
    return ChatResponse(
        message=Message(
            role=ROLE_ASSISTANT, content=answer.content, tool_calls=answer.tool_calls
        ),
        model=model,
        tokens=answer.tokens,
    )


class EventStream:
    """A streamed answer as server-sent events, a chunk of bytes to an event."""

    def __init__(self, tokens: Iterator[Token], model: str):
        self._tokens = tokens
        self._events = _events(tokens, model)

    def __iter__(self) -> Iterator[bytes]:
        return self

    def __next__(self) -> bytes:
        return next(self._events)

    def close(self) -> None:
        """End the stream and give the job's place back."""
        self._events.close()
        _close(self._tokens)


def _events(tokens: Iterator[Token], model: str) -> Generator[bytes]:
    try:
        for token in tokens:
            if token.text:
                yield format_event(StreamToken(text=token.text)).encode("utf-8")
            if token.final is not None:
                done = StreamDone(
                    model=model,
                    tokens=token.final.tokens,
                    tool_calls=token.final.tool_calls,
                )
                yield format_event(done).encode("utf-8")
                return
    except Exception:  # noqa: BLE001
        # The headers are sent: the stream ends here, without its `done`.
        return
    finally:
        _close(tokens)


def _close(tokens: Iterator[Token]) -> None:
    closer = getattr(tokens, "close", None)
    if callable(closer):
        closer()


def chat_endpoint(job: Job[Request, Answer | Iterator[Token]], model: str) -> Endpoint:
    """`POST /v1/chat`: the conversation through `job`, whole or streamed."""

    def endpoint(body: bytes) -> Response:
        request = ChatRequest.from_json(request_json(body))
        call = chat_request(request)
        if request.stream:
            return Response(
                200,
                (("Content-Type", EVENT_STREAM_TYPE),),
                EventStream(job.stream(call), model),
            )
        answer = whole(job.run(call))
        return json_response(200, chat_response(answer, model).to_json())

    return endpoint
