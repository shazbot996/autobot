"""The seam between the server and whatever serves its language model (IS-10).

One instruct model serves routing, slot filling and chat, so that only one is
resident. The server reaches it through `Runtime` and nothing else: Ollama is
the implementation today (`autobot_server.ollama`), and llama.cpp's
`llama-server` can replace it without a change to the contract.

`generate` is the one call. It takes the system prompt, the conversation, an
optional JSON schema that the answer is constrained to while it is generated
(IS-9), the tools the model may call (IS-21) and whether to stream. It gives
back an `Answer`, or, when streaming, the `Token`s of the answer as they
come, the last of which carries the whole `Answer`.

`check` says whether the runtime can serve the model at all, without
generating: the runtime is up and has the model. The server never pulls a
model, so a missing one is a `NotReady` whose line `/health` reports (IS-11).

`LanguageModel` is the engine of the `route` and `chat` jobs: it checks the
runtime when it loads and asks for one short answer as its warm-up, which is
also what makes the runtime load the model onto the card (IS-23, IS-24).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from autobot_server.contract import (
    ROLE_USER,
    Message,
    Parameter,
    Tokens,
    Tool,
    ToolCall,
)
from autobot_server.job import NotReady

# The JSON schema type of each parameter type (the NUC's `PARAMETER_TYPES`).
JSON_TYPES = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
}

NO_MODEL_CHOSEN = (
    "no language model is chosen: [server] llm_model is empty "
    "('make server-eval' measures the candidates)"
)
# What the warm-up asks: one word back, so it costs a few tokens.
WARM_UP_PROMPT = "Answer with the one word: ready"
# What an answer used when the runtime did not say.
NO_TOKENS = Tokens(prompt=0, completion=0)


@dataclass(frozen=True)
class Answer:
    """A whole answer of the model: its text, or its tool calls, and the tokens used."""

    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    tokens: Tokens = NO_TOKENS


@dataclass(frozen=True)
class Token:
    """One piece of a streamed answer; the last one carries the whole `Answer`."""

    text: str
    final: Answer | None = None


class Runtime(Protocol):
    """Whatever serves the language model: Ollama, or a test fake."""

    @property
    def model(self) -> str:
        """The model's name, as the runtime names it and `/health` reports it."""
        ...

    def check(self) -> None:
        """`NotReady`, in one line, unless the runtime is up and has the model."""
        ...

    def generate(
        self,
        system: str,
        messages: Sequence[Message],
        schema: dict[str, Any] | None = None,
        tools: Sequence[Tool] = (),
        stream: bool = False,
    ) -> Answer | Iterator[Token]:
        """The model's answer: whole, or as tokens when `stream` is set."""
        ...


def whole(answer: Answer | Iterator[Token]) -> Answer:
    """`answer` as one `Answer`, gathering a stream when that is what it is."""
    if isinstance(answer, Answer):
        return answer
    text: list[str] = []
    for token in answer:
        if token.final is not None:
            return token.final
        text.append(token.text)
    return Answer(content="".join(text))


def parameters_schema(parameters: Sequence[Parameter]) -> dict[str, Any]:
    """The JSON schema of an object with the slots `parameters` (IS-18, IS-21)."""
    properties: dict[str, Any] = {}
    for parameter in parameters:
        slot: dict[str, Any] = {"type": JSON_TYPES.get(parameter.type, "string")}
        if parameter.values:
            slot["enum"] = list(parameter.values)
        properties[parameter.name] = slot
    return {
        "type": "object",
        "properties": properties,
        "required": [parameter.name for parameter in parameters if parameter.required],
    }


def fits(parameter: Parameter, value: object) -> bool:
    """Whether `value` is of `parameter`'s type and among its allowed values."""
    if parameter.type == "boolean":
        typed = isinstance(value, bool)
    elif parameter.type == "integer":
        typed = isinstance(value, int) and not isinstance(value, bool)
    elif parameter.type == "number":
        typed = isinstance(value, int | float) and not isinstance(value, bool)
    else:
        typed = isinstance(value, str)
    return typed and (not parameter.values or value in parameter.values)


class LanguageModel:
    """The engine of the `route` and `chat` jobs: the one model behind the runtime."""

    def __init__(self, runtime: Runtime):
        self._runtime = runtime

    @property
    def model(self) -> str:
        """The model's name; a placeholder while none is chosen."""
        return self._runtime.model or "(none chosen)"

    @property
    def runtime(self) -> Runtime:
        return self._runtime

    def load(self) -> None:
        """Check the runtime has the model; `NotReady` with the reason when not."""
        if not self._runtime.model:
            raise NotReady(NO_MODEL_CHOSEN)
        self._runtime.check()

    def warm_up(self) -> None:
        """One short answer, which also has the runtime load the model."""
        self.load()
        whole(
            self._runtime.generate(
                "", (Message(role=ROLE_USER, content=WARM_UP_PROMPT),)
            )
        )

    def run(self, request: Request) -> Answer | Iterator[Token]:
        """The model's answer to one request of routing or chat."""
        return self._runtime.generate(
            request.system,
            request.messages,
            request.schema,
            request.tools,
            request.stream,
        )


@dataclass(frozen=True)
class Request:
    """One call of the model, as `route` and `chat` build it."""

    system: str
    messages: tuple[Message, ...]
    schema: dict[str, Any] | None = None
    tools: tuple[Tool, ...] = ()
    stream: bool = False
