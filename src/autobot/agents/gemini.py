"""The language model behind an agent that reasons: Gemini, through the ADK.

`Model` is the seam: `ask(prompt, query)` sends an agent's prompt and one
query, both text, and returns the answer's text. It blocks, so an agent calls
it on a worker thread. `AdkModel` implements it over Gemini on Vertex AI with
Google's Agent Development Kit (ADR 0002); the tests give a fake.

The seam has a second call, `chat(request)`, for an agent that converses
(IS-18, IS-19): a system prompt, the messages so far, an optional
`response_schema` and optional `tools`, in the shape of the inference
server's contract (`autobot.server.contract.ChatRequest`). It answers a
`ChatAnswer`, or, when the request streams and the model can, the `Token`s
of the answer as they come, the last of which carries the whole answer. The
local model (`autobot.agents.local_model`) is the seam's second
implementation and streams; `AdkModel.chat` runs the conversation as one
ADK turn, with the history as context, and answers whole. It is given no
tools: a tool call is the local model's to make.

`AdkModel.ask` connects through `autobot.gcp.client.connect` first, so the
identity is autobot's service account or the call is refused in one line
(GC-4): it never falls back to the owner's own login. Then it points the ADK
at Vertex AI in the configured project and `[gcp] gemini_location`
(`global` unless set: the newer models are not served in a region), builds an `LlmAgent` on
`[gcp] gemini_model` with the prompt as its instruction, and runs one turn in
a fresh in-memory session. No state is kept (AG-11).

Only text crosses to Google Cloud: no audio, no tool, no speech setting
(`CN-1`, `CN-2`). `google.adk` and `google.genai` are imported inside the
call, so importing this module loads neither. Every failure is a `ModelError`
whose message is one line. Nothing here draws.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import autobot.gcp.client
from autobot.config import GEMINI_VERTEX_AI, Config
from autobot.gcp.client import GcpError
from autobot.server.contract import (
    ROLE_ASSISTANT,
    ROLE_TOOL,
    ChatRequest,
    ToolCall,
)

NO_MODEL = "no Gemini model is chosen: pick one on the Google Cloud screen"
NO_ANSWER = "Gemini gave no answer"

# The settings the ADK's Gemini client reads from the environment.
USE_VERTEX_AI_ENV_VAR = "GOOGLE_GENAI_USE_VERTEXAI"
PROJECT_ENV_VAR = "GOOGLE_CLOUD_PROJECT"
LOCATION_ENV_VAR = "GOOGLE_CLOUD_LOCATION"

# How long one answer may take, in seconds.
TIMEOUT = 60.0
# The longest a `ModelError` message is, in characters.
MESSAGE_LIMIT = 200

# The names the ADK wants for the one-turn session; nothing is stored under them.
_APP_NAME = "autobot"
_AGENT_NAME = "autobot_agent"
_USER_ID = "autobot"


def _one_line(message: object) -> str:
    """`message` on one line, cut to `MESSAGE_LIMIT` characters."""
    line = " ".join(str(message).split())
    if len(line) > MESSAGE_LIMIT:
        line = line[: MESSAGE_LIMIT - 3].rstrip() + "..."
    return line


class ModelError(Exception):
    """The model could not be asked or did not answer. The message is one line."""

    def __init__(self, message: object) -> None:
        super().__init__(_one_line(message))


@dataclass(frozen=True)
class ChatAnswer:
    """A model's whole answer in a conversation: its text, or its tool calls."""

    content: str
    # The tools the model asked for; the NUC runs them (IS-21).
    tool_calls: tuple[ToolCall, ...] = ()
    # The model that answered, as it names itself; empty when it does not say.
    model: str = ""


@dataclass(frozen=True)
class Token:
    """One piece of a streamed answer; the last one carries the whole `ChatAnswer`."""

    text: str
    final: ChatAnswer | None = None


class Model(Protocol):
    """A language model an agent asks one question at a time, or converses with."""

    def ask(self, prompt: str, query: str) -> str:
        """The answer to `query` under the instruction `prompt`, or `ModelError`.

        It blocks until the answer is whole.
        """
        ...

    def chat(self, request: ChatRequest) -> ChatAnswer | Iterator[Token]:
        """The answer to a conversation, or `ModelError`.

        Whole, or as tokens when `request.stream` is set and the model
        streams. It blocks, as does each step of the tokens.
        """
        ...


def whole(answer: ChatAnswer | Iterator[Token]) -> ChatAnswer:
    """`answer` as one `ChatAnswer`, gathering a stream when that is what it is."""
    if isinstance(answer, ChatAnswer):
        return answer
    text: list[str] = []
    for token in answer:
        if token.final is not None:
            return token.final
        text.append(token.text)
    return ChatAnswer("".join(text))


# What a conversation's speakers are called when it is put to a one-turn model.
_OWNER = "Owner"
_AUTOBOT = "autobot"
_TOOL = "Tool result"
SCHEMA_INSTRUCTION = (
    "Reply with one JSON object and nothing else, matching this JSON schema:"
)
LAST_MESSAGE = "Answer the owner's last message."


def one_turn(request: ChatRequest) -> tuple[str, str]:
    """A conversation as one prompt and one query, for a model asked a turn at a time.

    The prompt is the system prompt, with the response schema after it when
    there is one. The query is the last message alone when it is the only
    one, and otherwise the conversation, each message under its speaker.
    """
    prompt = request.system
    if request.response_schema is not None:
        schema = json.dumps(request.response_schema, ensure_ascii=False)
        prompt = f"{prompt.rstrip()}\n{SCHEMA_INSTRUCTION}\n{schema}\n"
    messages = [message for message in request.messages if message.content.strip()]
    if len(messages) <= 1:
        return prompt, messages[0].content if messages else ""
    speakers = {ROLE_ASSISTANT: _AUTOBOT, ROLE_TOOL: _TOOL}
    lines = [
        f"{speakers.get(message.role, _OWNER)}: {message.content.strip()}"
        for message in messages
    ]
    return prompt, "\n".join([*lines, "", LAST_MESSAGE])


async def _one_turn(model: str, prompt: str, query: str) -> str:
    """One turn of an ADK agent on `model`: the text of its final response."""
    from google.adk.agents import LlmAgent
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    agent = LlmAgent(
        name=_AGENT_NAME,
        model=model,
        # A function, not the string: the ADK reads `{name}` in an instruction
        # string as a session value to fill in, and a prompt may hold braces.
        instruction=lambda _context: prompt,
    )
    sessions = InMemorySessionService()
    runner = Runner(app_name=_APP_NAME, agent=agent, session_service=sessions)
    try:
        session = await sessions.create_session(app_name=_APP_NAME, user_id=_USER_ID)
        message = types.Content(role="user", parts=[types.Part(text=query)])
        answer: list[str] = []
        async for event in runner.run_async(
            user_id=_USER_ID, session_id=session.id, new_message=message
        ):
            if not event.is_final_response():
                continue
            if event.error_code or event.error_message:
                raise ModelError(
                    f"Gemini refused: {event.error_message or event.error_code}"
                )
            if event.content is None or not event.content.parts:
                continue
            answer.extend(
                part.text
                for part in event.content.parts
                if part.text and not part.thought
            )
        return "".join(answer)
    finally:
        await runner.close()


def _ask_adk(model: str, prompt: str, query: str) -> str:
    """Ask `model` through the ADK and wait for the answer.

    The only function that touches the ADK. The caller is a worker thread
    with no event loop, so the asynchronous API is run to its end here.
    """
    return asyncio.run(asyncio.wait_for(_one_turn(model, prompt, query), TIMEOUT))


def _not_found(error: BaseException) -> bool:
    """Whether `error` is Vertex AI's 404: the model is not served where it was asked for."""
    text = str(error)
    return getattr(error, "code", None) == 404 or "NOT_FOUND" in text


class AdkModel:
    """Gemini on Vertex AI, asked as autobot's service account."""

    def __init__(
        self,
        config: Config,
        repo: Path,
        connect: Callable[[Config, Path], object] = autobot.gcp.client.connect,
    ) -> None:
        self._config = config
        self._repo = repo
        self._connect = connect

    def ask(self, prompt: str, query: str) -> str:
        gcp = self._config.gcp
        if not gcp.gemini_model:
            raise ModelError(NO_MODEL)
        if gcp.gemini_api != GEMINI_VERTEX_AI:
            # The other API takes an API key, not the service account.
            raise ModelError(
                f"[gcp] gemini_api = {gcp.gemini_api}: "
                f"the agents ask Gemini only through {GEMINI_VERTEX_AI}"
            )
        try:
            # Refuses unless the credentials are the service account's (GC-4).
            self._connect(self._config, self._repo)
        except GcpError as error:
            raise ModelError(error) from None
        os.environ[USE_VERTEX_AI_ENV_VAR] = "TRUE"
        os.environ[PROJECT_ENV_VAR] = gcp.project
        os.environ[LOCATION_ENV_VAR] = gcp.gemini_location
        not_found = ModelError(
            f"Gemini has no model {gcp.gemini_model} at location "
            f"{gcp.gemini_location}: pick another on the Google Cloud screen, "
            "or set [gcp] gemini_location"
        )
        try:
            answer = _ask_adk(gcp.gemini_model, prompt, query)
        except ModelError as error:
            if _not_found(error):
                raise not_found from None
            raise
        except TimeoutError:
            raise ModelError(
                f"Gemini did not answer in {TIMEOUT:.0f} seconds"
            ) from None
        except Exception as error:  # noqa: BLE001
            if _not_found(error):
                raise not_found from None
            # A library that is not installed, a refused token, an HTTP error,
            # a quota: one line, whatever the ADK raised.
            raise ModelError(
                f"Gemini failed: {type(error).__name__}: {error}"
            ) from None
        if not answer.strip():
            raise ModelError(NO_ANSWER)
        return answer

    def chat(self, request: ChatRequest) -> ChatAnswer:
        """The conversation as one ADK turn, the history as context; a whole answer."""
        prompt, query = one_turn(request)
        return ChatAnswer(self.ask(prompt, query), model=self._config.gcp.gemini_model)
