"""The core agent: autobot himself, on the local language model (IS-19, IS-20, IS-21). No drawing.

`core` is the default of the routing cascade: what no tier is admitted for
comes here when the inference server's chat is ready (IS-17). Its
personality is its system prompt, in its definition.

**A conversation, kept here.** The agent holds the session's messages, on
the NUC and in memory only (AG-11): nothing is written to disk and nothing
outlives the session. The server keeps no state (IS-5), so every request
carries the conversation whole, with the oldest turns dropped until it fits
the model's context, `[server] llm_context` less a margin for the reply.

**One turn.**

1. The request goes to the first model of the agent's chain that can be
   asked now (`autobot.agents.models`): its own, `local`, then its
   fallback. It asks for the two parts of AG-4 as one JSON object, with an
   optional `handoff`, and carries the definition's tools as schemas.
2. The answer is streamed. Its summary is cut into sentences as it comes,
   at `.`, `?` or `!` followed by a space or the end, and each sentence is
   spoken by a `Speaker` of its own in the local voice, so autobot starts
   speaking at the first full sentence and not at the last token. The
   detail panel shows the time to the first token. A model that fails
   before its first token is passed over for the next of the chain; when
   the chain ends in its spoken line, the agent says it and the session
   ends.
3. **Tools.** When the model answers with tool calls, the NUC runs each
   from `Seams.tools` and sends the results back as tool messages, for at
   most `[server] tool_rounds` rounds in a turn; then it stops with a
   trouble line. The server runs no tool (IS-21).
4. **Handoff.** When the answer names a `handoff`, an agent from the closed
   list of those whose model is `gemini` and the query it should get, a
   "thinking" cue is shown at once, the agent's before-a-request question
   is asked if its definition turns it on (AG-13), and the query goes to
   that agent's own model chain from the NUC, as the service account. The
   server is never asked to call a cloud service (IS-7, IS-20).
5. **The next turn.** After a reply the agent listens for `[server]
   core_listen_seconds`. What the listener transcribes meanwhile is gathered
   until the owner has paused, and is the next turn. When that time passes
   with nothing said, the session ends. `cancel` ends it at once (AG-14).

What the agent reads is the listener's own transcript, and what it speaks is
the local voice (`CN-1`, `CN-2`): only text goes to the inference server on
the house LAN, and to Gemini only by a handoff or the fallback.
"""

from __future__ import annotations

import json
import queue
import re
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from autobot.agents.base import Launch
from autobot.agents.confirm import Confirmation
from autobot.agents.gemini import ChatAnswer, ModelError, Token
from autobot.agents.models import (
    KIND_LINE,
    KIND_NOTHING,
    Answered,
    Choice,
    choices,
    first_answer,
)
from autobot.agents.response import Response, layout, parse_model_response
from autobot.agents.speech import Reply, Speaker
from autobot.config import AGENT_CORE, MODEL_GEMINI, AgentConfig, Config
from autobot.server.contract import (
    ROLE_ASSISTANT,
    ROLE_TOOL,
    ROLE_USER,
    ChatRequest,
    Message,
    Parameter,
    Tool,
    ToolCall,
)
from autobot.tui.face import FaceContext
from autobot.tui.face_reactions import ReactionSet
from autobot.tui.runner import FunctionTask

type Stage = Literal[
    "thinking", "confirming", "handoff", "tools", "listening", "closing", "done"
]

THINKING = "Thinking..."
LISTENING = "Go on: I'm listening."
HEARD = 'you: "{text}"'
FIRST_TOKEN = "first token after {seconds:.2f} s"
ANSWERED_BY = "answered by the {kind} model{name}"
HANDOFF_CUE = "Asking the {agent} agent..."
NOT_CONFIRMED = "Okay: I did not ask the {agent} agent."
TOO_MANY_ROUNDS = "the model asked for tools more than {rounds} times in one turn"
NO_TOOL = "error: there is no tool named {name}"
TOOL_FAILED = "error: {reason}"
TOOL_RAN = "tool {name}: {result}"
ENDED = "Talk to you later."

# How long the owner must have paused before what was heard is taken as the
# turn, in seconds: the listener transcribes speech a piece at a time.
TURN_QUIET_SECONDS = 1.2
# The tokens kept free of the context for the model's reply.
REPLY_MARGIN_TOKENS = 1024
# A token is about four characters of English; each message costs a few more.
CHARS_PER_TOKEN = 4
MESSAGE_TOKENS = 4

# How the answer's shape is put to the model, after the personality.
FORMAT = """\
Reply with one JSON object and nothing else:
{"summary": "...", "detail": ["...", "..."]}
The summary is read aloud: one to three short sentences in plain words.
The detail is a few short lines for a small text screen, or an empty list.
"""
HANDOFF_FORMAT = """\
To hand the owner's request to one of the agents below, add
"handoff": {{"agent": "<name>", "query": "<the question that agent should get>"}}
and make the summary one short line saying that you are asking it.
The agents you can hand to:
{agents}
"""

# The end of a sentence: closing punctuation, then a space (plan choice 8).
_SENTENCE_END = re.compile(r"[.?!]+[\"')\]]*(?=\s)")
_SUMMARY_KEY = re.compile(r'"summary"\s*:\s*"')
_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}

# A tool the NUC runs for a model: its arguments in, its result as text out.
type ToolRunner = Callable[[Mapping[str, Any]], str]


def summary_so_far(raw: str) -> str:
    """The text of the `summary` string in a JSON answer that is still arriving.

    Plain text that is not a JSON object is its own summary. A JSON object
    whose summary has not started gives nothing yet.
    """
    stripped = raw.lstrip()
    if not stripped.startswith("{"):
        return "" if stripped.startswith("```") else raw
    found = _SUMMARY_KEY.search(raw)
    if found is None:
        return ""
    text: list[str] = []
    index = found.end()
    while index < len(raw):
        character = raw[index]
        if character == '"':
            break
        if character != "\\":
            text.append(character)
            index += 1
            continue
        if index + 1 >= len(raw):
            break
        escape = raw[index + 1]
        if escape == "u":
            digits = raw[index + 2 : index + 6]
            if len(digits) < 4:
                break
            try:
                text.append(chr(int(digits, 16)))
            except ValueError:
                text.append("?")
            index += 6
            continue
        text.append(_ESCAPES.get(escape, escape))
        index += 2
    return "".join(text)


class SummaryStream:
    """Cuts the summary of an answer that is still arriving into its sentences."""

    def __init__(self) -> None:
        self._raw = ""
        # How much of the summary has been given out as sentences.
        self._given = 0

    @property
    def raw(self) -> str:
        """Everything the model has sent so far."""
        return self._raw

    def feed(self, text: str) -> list[str]:
        """Take the next piece; the sentences it completed, in order."""
        self._raw += text
        return self._cut(summary_so_far(self._raw), final=False)

    def finish(self, fallback: str = "") -> list[str]:
        """The stream has ended: what is left of the summary, as its last sentence.

        `fallback` is the summary as the whole answer was parsed, said when
        nothing of it could be read while it arrived (a fenced answer).
        """
        text = summary_so_far(self._raw)
        if not text.strip() and self._given == 0:
            text = fallback
        return self._cut(text, final=True)

    def _cut(self, text: str, *, final: bool) -> list[str]:
        sentences: list[str] = []
        while True:
            found = _SENTENCE_END.search(text, self._given)
            if found is None:
                break
            sentence = " ".join(text[self._given : found.end()].split())
            self._given = found.end()
            if sentence:
                sentences.append(sentence)
        if final:
            rest = " ".join(text[self._given :].split())
            self._given = len(text)
            if rest:
                sentences.append(rest)
        return sentences


@dataclass(frozen=True)
class _End:
    """The end of one answer, as the worker hands it to the frame."""

    choice: Choice
    final: ChatAnswer | None = None
    # Why the answer stopped part-way; empty when it did not.
    error: str = ""
    # Why the models before this one were passed over or failed.
    troubles: tuple[str, ...] = ()


def response_schema(handoff_agents: Sequence[str]) -> dict[str, Any]:
    """The JSON schema of the core agent's answer: AG-4's two parts and a handoff (IS-20)."""
    properties: dict[str, Any] = {
        "summary": {"type": "string"},
        "detail": {"type": "array", "items": {"type": "string"}},
    }
    if handoff_agents:
        properties["handoff"] = {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "enum": list(handoff_agents)},
                "query": {"type": "string"},
            },
            "required": ["agent", "query"],
        }
    return {
        "type": "object",
        "properties": properties,
        "required": ["summary", "detail"],
    }


def handoff_agents(config: Config) -> tuple[AgentConfig, ...]:
    """The closed list of the handoff: every agent whose model is `gemini` (IS-20)."""
    return tuple(
        agent
        for agent in config.agents
        if agent.model == MODEL_GEMINI and agent.name != AGENT_CORE
    )


def system_prompt(definition: AgentConfig, agents: Sequence[AgentConfig]) -> str:
    """The personality, then the answer's shape and the agents it can hand to."""
    parts = [definition.prompt.rstrip(), "", FORMAT.rstrip()]
    if agents:
        listed = "\n".join(f"- {agent.name}: {agent.description}" for agent in agents)
        parts.append(HANDOFF_FORMAT.format(agents=listed).rstrip())
    return "\n".join(parts) + "\n"


def tools_of(definition: AgentConfig) -> tuple[Tool, ...]:
    """The definition's tools as the schemas the chat request carries (IS-21)."""
    return tuple(
        Tool(
            name=tool.name,
            description=tool.description,
            parameters=tuple(
                Parameter(
                    name=slot.name,
                    type=slot.type,
                    values=slot.values,
                    required=slot.required,
                )
                for slot in tool.parameters
            ),
        )
        for tool in definition.tools
    )


def estimate_tokens(text: str) -> int:
    """About how many tokens `text` is: its characters over four, rounded up."""
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def message_tokens(message: Message) -> int:
    calls = sum(
        estimate_tokens(call.name + json.dumps(call.arguments))
        for call in message.tool_calls
    )
    return MESSAGE_TOKENS + estimate_tokens(message.content) + calls


def trim(messages: Sequence[Message], budget: int) -> tuple[Message, ...]:
    """The newest of `messages` that fit `budget` tokens, starting at an owner's turn.

    The oldest turns are dropped first (IS-19). The last message is always
    kept, whatever its size: a turn is never answered with nothing.
    """
    kept = list(messages)
    while len(kept) > 1 and (
        sum(message_tokens(message) for message in kept) > budget
        or kept[0].role != ROLE_USER
    ):
        kept.pop(0)
    return tuple(kept)


def read_handoff(content: str, allowed: Sequence[str]) -> tuple[str, str] | None:
    """The agent and the query of an answer's `handoff`, when it names one of `allowed`."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None
    handoff = data.get("handoff") if isinstance(data, dict) else None
    if not isinstance(handoff, dict):
        return None
    agent, query = handoff.get("agent"), handoff.get("query")
    if not isinstance(agent, str) or agent not in allowed:
        return None
    if not isinstance(query, str) or not query.strip():
        return None
    return agent, " ".join(query.split())


class Core:
    """One session with autobot himself: turns, each answered as it streams."""

    def __init__(self, launch: Launch) -> None:
        self._definition = launch.definition
        self._first = launch.transcript
        # The conversation so far, oldest first: kept here and nowhere else.
        self.messages: list[Message] = []
        self.stage: Stage = "thinking"
        # Seconds from the last request to its first token; None before one came.
        self.first_token_seconds: float | None = None
        self._done = False
        self._pieces: queue.SimpleQueue[Token | _End] = queue.SimpleQueue()
        self._task: FunctionTask | None = None
        self._stream = SummaryStream()
        self._end: _End | None = None
        self._asked_at = 0.0
        self._waiting: deque[Speaker] = deque()
        self._speaking: Speaker | None = None
        self._rounds = 0
        self._confirmation: Confirmation | None = None
        self._handoff: tuple[AgentConfig, str] | None = None
        self._reply: Reply | None = None
        self._turn: list[str] = []
        self._turn_at = 0.0
        self._listen_until = 0.0

    @property
    def reactions(self) -> ReactionSet | None:
        return None

    @property
    def done(self) -> bool:
        return self._done

    def start(self, face: FaceContext) -> None:
        self._user(face, self._first)

    def tick(self, face: FaceContext) -> None:
        if self._done:
            return
        if self.stage == "thinking":
            self._answering(face)
        elif self.stage == "confirming":
            self._confirming(face)
        elif self.stage == "handoff":
            self._handing_off(face)
        elif self.stage == "tools":
            self._tools_ran(face)
        elif self.stage == "listening":
            self._listening(face)
        elif self.stage == "closing":
            self._closing(face)

    def cancel(self) -> None:
        """The owner said `cancel`: the session ends, and an answer on its way is dropped."""
        self._task = None
        self._waiting.clear()
        self._speaking = None
        self._done = True

    # --- a turn ---------------------------------------------------------------------

    def _user(self, face: FaceContext, text: str) -> None:
        said = " ".join(text.split())
        self.messages.append(Message(role=ROLE_USER, content=said))
        self._rounds = 0
        face.detail.append(HEARD.format(text=said))
        self._ask(face)

    def request(self, config: Config) -> ChatRequest:
        """The next request: the conversation, trimmed to the context, as a stream."""
        agents = handoff_agents(config)
        system = system_prompt(self._definition, agents)
        schema = response_schema([agent.name for agent in agents])
        tools = tools_of(self._definition)
        fixed = (
            estimate_tokens(system)
            + estimate_tokens(json.dumps(schema))
            + sum(
                estimate_tokens(json.dumps(tool.to_json(), ensure_ascii=False))
                for tool in tools
            )
        )
        budget = config.server.llm_context - REPLY_MARGIN_TOKENS - fixed
        return ChatRequest(
            system=system,
            messages=trim(self.messages, budget),
            response_schema=schema,
            tools=tools,
            stream=True,
        )

    def _ask(self, face: FaceContext) -> None:
        definition, seams = self._definition, face.seams
        config, repo = face.config, face.repo
        request = self.request(config)
        pieces: queue.SimpleQueue[Token | _End] = queue.SimpleQueue()

        def run() -> None:
            """Ask each model of the chain in turn; hand its tokens to the frame."""
            troubles: list[str] = []
            for choice in choices(definition, seams, config, repo):
                model = choice.model
                if model is None:
                    pieces.put(_End(choice, troubles=(*choice.passed, *troubles)))
                    return
                text: list[str] = []
                try:
                    answer = model.chat(request)
                    if isinstance(answer, ChatAnswer):
                        if answer.content:
                            pieces.put(Token(answer.content))
                        pieces.put(_End(choice, answer))
                        return
                    for token in answer:
                        if token.final is not None:
                            pieces.put(_End(choice, token.final))
                            return
                        if token.text:
                            text.append(token.text)
                            pieces.put(token)
                    pieces.put(_End(choice, ChatAnswer("".join(text))))
                    return
                except ModelError as error:
                    if text:
                        # Part of the answer was spoken: it is not asked again.
                        pieces.put(_End(choice, error=str(error)))
                        return
                    # Nothing came: the next model of the chain is asked (IS-4).
                    troubles.append(str(error))

        self.stage = "thinking"
        self._pieces = pieces
        self._stream = SummaryStream()
        self._end = None
        self.first_token_seconds = None
        self._asked_at = face.clock()
        face.summary.add(THINKING)
        face.react("working")
        self._task = face.spawn(run)
        self._answering(face)

    def _answering(self, face: FaceContext) -> None:
        """Take what the model has sent since the last frame, and speak what is whole."""
        while self._end is None:
            try:
                piece = self._pieces.get_nowait()
            except queue.Empty:
                break
            if isinstance(piece, _End):
                self._end = piece
                self._ended(face, piece)
                break
            if self.first_token_seconds is None:
                self.first_token_seconds = face.clock() - self._asked_at
                face.detail.append(FIRST_TOKEN.format(seconds=self.first_token_seconds))
            self._queue(self._stream.feed(piece.text))
        if self._end is None:
            task = self._task
            result = task.poll() if task is not None else None
            if result is not None and not result.ok and self._pieces.empty():
                self._trouble(face, result.summary())
                return
        if not self._speak(face):
            return
        if self._end is not None:
            self._finished(face, self._end)

    def _queue(self, sentences: Sequence[str]) -> None:
        self._waiting.extend(Speaker(sentence) for sentence in sentences)

    def _speak(self, face: FaceContext) -> bool:
        """Say the sentences that wait, one at a time; whether all have been said."""
        while True:
            if self._speaking is None:
                if not self._waiting:
                    return True
                self._speaking = self._waiting.popleft()
                self._speaking.start(face)
            self._speaking.tick(face)
            if not self._speaking.done:
                return False
            if self._speaking.error:
                # Shown though not said: the render or the playback failed.
                face.summary.add(self._speaking.text)
                face.detail.append(f"voice: {self._speaking.error}")
            self._speaking = None

    def _ended(self, face: FaceContext, end: _End) -> None:
        """The stream is over: queue what is left of the summary, or the chain's line."""
        if end.final is not None and not end.final.tool_calls:
            parsed = parse_model_response(end.final.content)
            self._queue(self._stream.finish(parsed.summary))
        elif end.choice.kind == KIND_LINE:
            self._queue([end.choice.line])

    def _finished(self, face: FaceContext, end: _End) -> None:
        """Everything of the answer has been said: what the turn does next."""
        self._task = None
        if end.error:
            self._trouble(face, end.error)
            return
        if end.choice.kind == KIND_LINE:
            # Neither model could answer: the line was said, and the session ends.
            for trouble in end.troubles:
                face.detail.append(f"x {trouble}")
            self._close(face)
            return
        final = end.final
        if end.choice.kind == KIND_NOTHING or final is None:
            reason = end.troubles[-1] if end.troubles else "no model can answer"
            self._trouble(face, reason)
            return
        if final.tool_calls:
            self.messages.append(
                Message(
                    role=ROLE_ASSISTANT,
                    content=final.content,
                    tool_calls=final.tool_calls,
                )
            )
            self._run_tools(face, final.tool_calls)
            return
        if not final.content.strip():
            self._trouble(face, "the language model gave no answer")
            return
        self.messages.append(Message(role=ROLE_ASSISTANT, content=final.content))
        self._show(face, parse_model_response(final.content), end.choice, final)
        allowed = handoff_agents(face.config)
        handoff = read_handoff(final.content, [agent.name for agent in allowed])
        if handoff is not None:
            name, query = handoff
            target = next(agent for agent in allowed if agent.name == name)
            self._begin_handoff(face, target, query)
            return
        self._listen(face)

    def _show(
        self, face: FaceContext, response: Response, choice: Choice, final: ChatAnswer
    ) -> None:
        """Lay the answer's detail out, with which model gave it and how fast."""
        face.detail.clear()
        for line in layout(response, face.detail.page, face.detail.cols):
            face.detail.append(line)
        name = f" ({final.model})" if final.model else ""
        face.detail.append(ANSWERED_BY.format(kind=choice.kind, name=name))
        if self.first_token_seconds is not None:
            face.detail.append(FIRST_TOKEN.format(seconds=self.first_token_seconds))

    # --- tools (IS-21) ---------------------------------------------------------------

    def _run_tools(self, face: FaceContext, calls: Sequence[ToolCall]) -> None:
        self._rounds += 1
        limit = face.config.server.tool_rounds
        if self._rounds > limit:
            face.summary.trouble(TOO_MANY_ROUNDS.format(rounds=limit))
            face.react("trouble")
            self._listen(face)
            return
        tools = face.seams.tools

        def run() -> list[Message]:
            results: list[Message] = []
            for call in calls:
                tool = tools.get(call.name)
                if tool is None:
                    result = NO_TOOL.format(name=call.name)
                else:
                    try:
                        result = str(tool(call.arguments))
                    except Exception as error:  # noqa: BLE001
                        line = " ".join(str(error).split()) or type(error).__name__
                        result = TOOL_FAILED.format(reason=line)
                results.append(
                    Message(role=ROLE_TOOL, content=result, tool_call_id=call.id)
                )
            return results

        self.stage = "tools"
        face.react("working")
        self._task = face.spawn(run)
        self._tools_ran(face)

    def _tools_ran(self, face: FaceContext) -> None:
        task = self._task
        if task is None:
            return
        result = task.poll()
        if result is None:
            return
        self._task = None
        if not result.ok or not isinstance(task.value, list):
            self._trouble(face, result.summary() or "the tools could not be run")
            return
        calls = {call.id: call.name for call in self.messages[-1].tool_calls}
        for message in task.value:
            name = calls.get(message.tool_call_id, "?")
            face.detail.append(TOOL_RAN.format(name=name, result=message.content))
            self.messages.append(message)
        # The results go back in the next request of the same turn.
        self._ask(face)

    # --- the handoff (IS-20) ----------------------------------------------------------

    def _begin_handoff(
        self, face: FaceContext, target: AgentConfig, query: str
    ) -> None:
        # The cue comes first: the owner sees at once that it is being asked.
        face.summary.add(HANDOFF_CUE.format(agent=target.name))
        face.react("working")
        self._handoff = (target, query)
        if target.confirm_request:
            self.stage = "confirming"
            self._confirmation = face.confirm(target.confirm_request_question)
            self._confirming(face)
            return
        self._send_handoff(face)

    def _confirming(self, face: FaceContext) -> None:
        confirmation = self._confirmation
        assert confirmation is not None and self._handoff is not None
        confirmation.tick(face)
        if not confirmation.done:
            return
        self._confirmation = None
        if confirmation.outcome == "yes":
            face.react("working")
            self._send_handoff(face)
            return
        target, _query = self._handoff
        self._handoff = None
        face.summary.add(NOT_CONFIRMED.format(agent=target.name))
        self._listen(face)

    def _send_handoff(self, face: FaceContext) -> None:
        assert self._handoff is not None
        target, query = self._handoff
        seams, config, repo = face.seams, face.config, face.repo

        def ask() -> Answered:
            # That agent's own chain, asked from the NUC (IS-20).
            return first_answer(
                target,
                seams,
                config,
                repo,
                lambda model: model.ask(target.prompt, query),
            )

        self.stage = "handoff"
        self._reply = None
        self._task = face.spawn(ask)
        self._handing_off(face)

    def _handing_off(self, face: FaceContext) -> None:
        if self._reply is None:
            task = self._task
            if task is None:
                return
            result = task.poll()
            if result is None:
                return
            self._task = None
            answered = task.value if isinstance(task.value, Answered) else None
            target = self._handoff[0] if self._handoff is not None else None
            self._handoff = None
            if answered is not None and answered.text.strip():
                self.messages.append(
                    Message(role=ROLE_ASSISTANT, content=answered.text)
                )
                self._reply = Reply(face, parse_model_response(answered.text))
            elif answered is not None and answered.choice.kind == KIND_LINE:
                self._reply = Reply(face, Response(answered.choice.line))
            else:
                reason = (answered.trouble if answered else "") or result.summary()
                name = target.name if target is not None else "other"
                face.summary.trouble(
                    f"the {name} agent could not answer: {reason or 'no model'}"
                )
                face.react("trouble")
                self._listen(face)
                return
        self._reply.tick(face)
        if self._reply.done:
            self._reply = None
            self._listen(face)

    # --- the next turn (IS-19) ----------------------------------------------------------

    def _listen(self, face: FaceContext) -> None:
        """Wait for the owner's next turn, `core_listen_seconds` from now."""
        self.stage = "listening"
        # What was heard while autobot thought and spoke is not a turn.
        face.heard.clear()
        self._turn = []
        self._listen_until = face.clock() + face.config.server.core_listen_seconds
        face.summary.add(LISTENING)
        face.react("listening")

    def _listening(self, face: FaceContext) -> None:
        now = face.clock()
        while face.heard:
            text = " ".join(face.heard.popleft().split())
            if text:
                self._turn.append(text)
                self._turn_at = now
        if self._turn:
            # The listener hears a sentence a piece at a time: wait for the pause.
            if now - self._turn_at >= TURN_QUIET_SECONDS:
                said, self._turn = " ".join(self._turn), []
                self._user(face, said)
            return
        if now >= self._listen_until:
            face.summary.add(ENDED)
            face.react("done")
            self.stage = "done"
            self._done = True

    # --- the end ---------------------------------------------------------------------------

    def _close(self, face: FaceContext) -> None:
        self.stage = "closing"
        self._closing(face)

    def _closing(self, face: FaceContext) -> None:
        if not self._speak(face):
            return
        face.react("done")
        self.stage = "done"
        self._done = True

    def _trouble(self, face: FaceContext, reason: str) -> None:
        self._task = None
        self._waiting.clear()
        self._speaking = None
        self.stage = "done"
        face.summary.trouble(reason)
        face.detail.append(f"x {reason}")
        face.react("trouble")
        face.release()
        self._done = True
