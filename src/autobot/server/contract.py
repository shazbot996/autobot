"""The inference server's contract, as the NUC reads and writes it (IS-9, IS-11, IS-22).

Every request and response the server and the NUC exchange is one of the
frozen dataclasses below. The examples under `server/contract/` hold one of
each, and both test suites read them, so a field renamed on either side
fails a test (IS-12).

A shape's JSON keys are its field names, in both directions: `to_json`
builds the object from the fields, and `from_json` refuses an unknown key, a
missing required key and a value of the wrong type, with a `ContractError`
whose message is one line. A field marked `OMIT_EMPTY` is left out of the
JSON when it is empty, and may be absent from it.

Chat tokens are streamed as server-sent events: `event: token` for each
piece of the answer, then `event: done` (IS-9). `format_event` writes one
event and `iter_stream` reads them from the lines of a stream.

The contract's version lives once, in `server/contract/contract.json`, and
`contract_version` reads it.
"""

from __future__ import annotations

import base64
import binascii
import json
import types
import typing
from collections.abc import Iterable, Iterator
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Self

from autobot.config import repo_root

# The file that holds the contract's version, the one place it lives.
CONTRACT_FILE = "contract.json"

# A field marked so is left out of the JSON when empty, and may be absent.
OMIT_EMPTY = {"omit_empty": True}

# The roles of a chat message: the owner, the model, and a tool's result.
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_TOOL = "tool"
ROLES = (ROLE_USER, ROLE_ASSISTANT, ROLE_TOOL)

# The agent a route answer names when none on the list fits (IS-9).
NO_AGENT = "none"

# The events of a streamed chat answer (IS-9).
EVENT_TOKEN = "token"
EVENT_DONE = "done"

# An allowed value of a parameter, of the parameter's type (IS-18).
type ParameterValue = str | int | float | bool


class ContractError(Exception):
    """A request or response does not have the contract's shape. The message is one line."""


def contract_dir() -> Path:
    """The directory of the contract's version and examples: `server/contract/`."""
    return repo_root() / "server" / "contract"


@dataclass(frozen=True)
class Shape:
    """The base of every shape: JSON in and out, keyed by the field names."""

    @classmethod
    def from_json(cls, data: object, where: str = "") -> Self:
        """The shape from parsed JSON; `ContractError` when it is not this shape."""
        return _decode_shape(cls, data, where or cls.__name__)

    def to_json(self) -> dict[str, Any]:
        """The shape as a JSON object, ready for `json.dumps`."""
        encoded = _encode(self)
        assert isinstance(encoded, dict)
        return encoded


@dataclass(frozen=True)
class ContractInfo(Shape):
    """`server/contract/contract.json`: the contract's version (IS-22)."""

    # The version, as `"1"`; its first dot-separated part is the major one.
    contract_version: str


@dataclass(frozen=True)
class JobHealth(Shape):
    """Whether one of the server's jobs can be handed work (IS-11)."""

    # Ready only once its model has loaded and answered a warm-up (IS-23).
    ready: bool
    # The model behind the job, as the server names it.
    model: str
    # Why the job is not ready, in one line; empty when it is.
    reason: str = ""


@dataclass(frozen=True)
class Jobs(Shape):
    """The server's jobs, each with its readiness (IS-11)."""

    stt: JobHealth
    route: JobHealth
    chat: JobHealth


@dataclass(frozen=True)
class Budget(Shape):
    """The VRAM budget of IS-10, in MiB."""

    stt_mib: int
    llm_weights_mib: int
    llm_kv_cache_mib: int
    cuda_contexts_mib: int
    total_mib: int


@dataclass(frozen=True)
class GpuMemory(Shape):
    """The GPU's memory as the server read it, in MiB (IS-11)."""

    used_mib: int
    free_mib: int


@dataclass(frozen=True)
class Health(Shape):
    """The answer to `GET /health`: what the server can do (IS-11)."""

    server_version: str
    contract_version: str
    jobs: Jobs
    # Text-to-speech is reserved, so false in the first version (IS-10).
    tts: bool
    budget: Budget
    gpu: GpuMemory


@dataclass(frozen=True)
class SttRequest(Shape):
    """The request of `POST /v1/stt`: one utterance and its hints (IS-8, IS-16)."""

    # The utterance as a WAV file, 16 kHz mono 16-bit PCM, base64-encoded so
    # that the request stays JSON.
    audio_base64: str
    # The words to bias the model toward, capped in length by the NUC.
    hints: tuple[str, ...] = ()

    @classmethod
    def from_wav(cls, audio: bytes, hints: Iterable[str] = ()) -> SttRequest:
        """The request for the WAV file `audio`."""
        return cls(base64.b64encode(audio).decode("ascii"), tuple(hints))

    @property
    def audio(self) -> bytes:
        """The WAV file's bytes; `ContractError` when the field is not base64."""
        try:
            return base64.b64decode(self.audio_base64, validate=True)
        except (binascii.Error, ValueError):
            raise ContractError("SttRequest.audio_base64 is not base64") from None


@dataclass(frozen=True)
class Segment(Shape):
    """One segment of a transcript, with its times in the utterance (IS-8)."""

    start_seconds: float
    end_seconds: float
    text: str


@dataclass(frozen=True)
class SttResponse(Shape):
    """The answer of `POST /v1/stt` (IS-8)."""

    text: str
    language: str
    segments: tuple[Segment, ...]
    # The length of the audio sent.
    duration_seconds: float
    # How long the server took to transcribe it.
    elapsed_seconds: float
    # The model that transcribed it, so the transcript says its source (IS-15).
    model: str


@dataclass(frozen=True)
class Parameter(Shape):
    """One named slot of an agent or a tool (IS-18, IS-21)."""

    name: str
    # One of `autobot.config.PARAMETER_TYPES`.
    type: str = "string"
    # The values it may take; empty when any value of its type will do.
    values: tuple[ParameterValue, ...] = ()
    required: bool = False


@dataclass(frozen=True)
class RouteAgent(Shape):
    """One agent the NUC can route to, as the route request lists it (IS-9)."""

    name: str
    description: str
    # The commands of its `[[routes]]` rules.
    commands: tuple[str, ...]
    # The modes of its rules, without the empty mode, which every agent has.
    modes: tuple[str, ...]
    parameters: tuple[Parameter, ...] = ()


@dataclass(frozen=True)
class RouteRequest(Shape):
    """The request of `POST /v1/route`: a transcript and the closed list (IS-9)."""

    transcript: str
    agents: tuple[RouteAgent, ...]


@dataclass(frozen=True)
class RouteResponse(Shape):
    """The answer of `POST /v1/route`, for the rules layer to admit (IS-9, IS-17)."""

    # One agent of the request's list, or `NO_AGENT`.
    agent: str
    # One of that agent's modes, or empty.
    mode: str
    confidence: float
    # The slots the model read from the transcript, by name.
    parameters: dict[str, ParameterValue]


@dataclass(frozen=True)
class Tool(Shape):
    """One tool the model may call, sent as its schema; the NUC runs it (IS-21)."""

    name: str
    description: str
    parameters: tuple[Parameter, ...] = ()


@dataclass(frozen=True)
class ToolCall(Shape):
    """One call of a tool the model asked for (IS-21)."""

    # Names the call, so the tool message with its result can answer it.
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Message(Shape):
    """One message of a conversation the NUC keeps and sends whole (IS-5, IS-19)."""

    # One of `ROLES`.
    role: str
    # The text; the model's JSON text when the request had a `response_schema`.
    content: str
    # The model's tool calls, on an assistant message.
    tool_calls: tuple[ToolCall, ...] = field(default=(), metadata=OMIT_EMPTY)
    # The call a tool message answers.
    tool_call_id: str = field(default="", metadata=OMIT_EMPTY)

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ContractError(
                f"Message.role {self.role!r} is not one of: {', '.join(ROLES)}"
            )


@dataclass(frozen=True)
class ChatRequest(Shape):
    """The request of `POST /v1/chat` (IS-9)."""

    # The agent's system prompt: its personality (IS-19).
    system: str
    # The conversation so far, oldest first.
    messages: tuple[Message, ...]
    # The JSON schema the answer must have, as AG-4's two parts; none when
    # the answer is free text.
    response_schema: dict[str, Any] | None = field(default=None, metadata=OMIT_EMPTY)
    tools: tuple[Tool, ...] = field(default=(), metadata=OMIT_EMPTY)
    # Whether the answer comes as server-sent events.
    stream: bool = False


@dataclass(frozen=True)
class Tokens(Shape):
    """The tokens a chat used (IS-9)."""

    prompt: int
    completion: int


@dataclass(frozen=True)
class ChatResponse(Shape):
    """The whole answer of `POST /v1/chat` (IS-9)."""

    # The model's message: its answer, or its tool calls.
    message: Message
    model: str
    tokens: Tokens


@dataclass(frozen=True)
class StreamToken(Shape):
    """One `event: token` of a streamed chat: the next piece of the answer."""

    text: str


@dataclass(frozen=True)
class StreamDone(Shape):
    """The `event: done` that ends a streamed chat."""

    model: str
    tokens: Tokens
    tool_calls: tuple[ToolCall, ...] = field(default=(), metadata=OMIT_EMPTY)


type StreamEvent = StreamToken | StreamDone

_EVENT_SHAPES: dict[str, type[StreamToken | StreamDone]] = {
    EVENT_TOKEN: StreamToken,
    EVENT_DONE: StreamDone,
}


def contract_version() -> str:
    """The contract's version, from `server/contract/contract.json`."""
    path = contract_dir() / CONTRACT_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ContractError(f"contract file not found: {path}") from None
    except OSError as error:
        raise ContractError(f"cannot read {path}: {error.strerror}") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ContractError(f"{path} is not valid JSON") from None
    return ContractInfo.from_json(data, CONTRACT_FILE).contract_version


def format_event(event: StreamEvent) -> str:
    """One server-sent event: its name, its data as one JSON line, a blank line."""
    name = EVENT_TOKEN if isinstance(event, StreamToken) else EVENT_DONE
    data = json.dumps(event.to_json(), ensure_ascii=False)
    return f"event: {name}\ndata: {data}\n\n"


def iter_stream(lines: Iterable[str]) -> Iterator[StreamEvent]:
    """The events of a streamed chat, from its lines as they arrive.

    A blank line ends an event; a line starting `:` is a comment; data lines
    are joined with newlines, as server-sent events have it. An event that is
    not `token` or `done`, or whose data is not its shape, is a `ContractError`.
    """
    name = ""
    data: list[str] = []
    for raw in lines:
        line = raw.rstrip("\r\n")
        if not line:
            if data:
                yield _event(name, "\n".join(data))
            name, data = "", []
            continue
        if line.startswith(":"):
            continue
        key, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if key == "event":
            name = value
        elif key == "data":
            data.append(value)


def _event(name: str, data: str) -> StreamEvent:
    shape = _EVENT_SHAPES.get(name)
    if shape is None:
        raise ContractError(
            f"stream event {name or 'message'!r} is not one of: "
            f"{', '.join(_EVENT_SHAPES)}"
        )
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        raise ContractError(f"stream event {name!r} data is not valid JSON") from None
    return shape.from_json(parsed, f"event {name}")


def _encode(value: object) -> Any:
    """`value` as JSON: a shape by its field names, a tuple as a list."""
    if is_dataclass(value) and not isinstance(value, type):
        encoded: dict[str, Any] = {}
        for item in fields(value):
            content = getattr(value, item.name)
            if item.metadata.get("omit_empty") and content in (None, (), ""):
                continue
            encoded[item.name] = _encode(content)
        return encoded
    if isinstance(value, tuple | list):
        return [_encode(item) for item in value]
    if isinstance(value, dict):
        return {key: _encode(item) for key, item in value.items()}
    return value


def _decode_shape[S: Shape](shape: type[S], data: object, where: str) -> S:
    if not isinstance(data, dict):
        raise ContractError(f"{where} is not a JSON object")
    hints = typing.get_type_hints(shape)
    known = fields(shape)
    unknown = sorted(set(data) - {item.name for item in known})
    if unknown:
        raise ContractError(f"{where} has an unknown field: {unknown[0]}")
    values: dict[str, Any] = {}
    for item in known:
        if item.name in data:
            values[item.name] = _decode(
                hints[item.name], data[item.name], f"{where}.{item.name}"
            )
        elif item.default is MISSING and item.default_factory is MISSING:
            raise ContractError(f"{where} is missing the field: {item.name}")
    return shape(**values)


def _decode(hint: Any, value: object, where: str) -> Any:
    """`value` checked against the type `hint` and built into it."""
    if isinstance(hint, typing.TypeAliasType):
        return _decode(hint.__value__, value, where)
    if hint is Any:
        return value
    origin, args = typing.get_origin(hint), typing.get_args(hint)
    if origin is None and isinstance(hint, type) and issubclass(hint, Shape):
        return hint.from_json(value, where)
    if origin is types.UnionType:
        for option in args:
            try:
                return _decode(option, value, where)
            except ContractError:
                continue
        raise ContractError(f"{where} is not one of: {_names(args)}")
    if origin is tuple:
        if not isinstance(value, list):
            raise ContractError(f"{where} is not a list")
        return tuple(
            _decode(args[0], item, f"{where}[{number}]")
            for number, item in enumerate(value)
        )
    if origin is dict:
        if not isinstance(value, dict):
            raise ContractError(f"{where} is not a JSON object")
        return {
            key: _decode(args[1], item, f"{where}.{key}") for key, item in value.items()
        }
    if hint is type(None):
        if value is None:
            return None
    elif hint is bool:
        if isinstance(value, bool):
            return value
    elif hint is int:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    elif hint is float:
        if isinstance(value, int | float) and not isinstance(value, bool):
            return float(value)
    elif hint is str:
        if isinstance(value, str):
            return value
    else:
        raise TypeError(f"the contract has no decoder for {hint!r}")
    raise ContractError(f"{where} is not {_names((hint,))}")


def _names(hints: Iterable[Any]) -> str:
    """The JSON names of the types `hints`, for a message."""
    names = {
        str: "a string",
        int: "an integer",
        float: "a number",
        bool: "a boolean",
        type(None): "null",
    }
    return ", ".join(
        names.get(hint, getattr(hint, "__name__", str(hint))) for hint in hints
    )
