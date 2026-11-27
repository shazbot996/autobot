"""`make server-eval`: measure a language model, on `GAMEON1`, against the real runtime (IS-26).

    python -m autobot_server.eval [--model NAME]

The tests of the server run on fakes and can say nothing of a real model's
accuracy or speed. This command can: it is how the language model of IS-10
is chosen. It runs on `GAMEON1` only, against the runtime at `[server]
runtime_url`, for the model named by `--model` (`MODEL=` of the make
target), or `[server] llm_model` when none is named. The runtime must
already have the model: nothing is pulled.

It measures:

- **Route accuracy**, on the committed, labelled set of typed utterances in
  `server/eval/utterances.jsonl`. Each is one thing the owner might say,
  typed, with the agent, the mode and the parameters it should route to, or
  `none`. They are text only: no audio and nothing of the owner's voice
  (TS-5). Each goes through `autobot_server.route` as a request does, with
  the closed list of agents of the contract's example.
- **JSON validity**: whether each answer is the schema's shape and names an
  agent on the list.
- **Latency**: of each route answer, and, for the contract's chat example
  streamed through `autobot_server.chat`, to the first token and to the
  full answer.

and says whether each stays within the latency budget of IS-4: a routed
command within 1.5 s, a streamed chat reply starting within 2.0 s.

The report is written to `run/server-eval-<model>.json` and summarised on
the terminal. The candidate with the best route accuracy and JSON validity
that stays within the budget is the one to write to `[server] llm_model`,
which the owner does himself.
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import statistics
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from autobot_server.chat import chat_request
from autobot_server.config import (
    ServerConfigError,
    load_settings,
    resolve_checkout_path,
)
from autobot_server.contract import (
    NO_AGENT,
    ROLE_USER,
    ChatRequest,
    ContractError,
    Message,
    ParameterValue,
    RouteAgent,
    RouteRequest,
    contract_dir,
)
from autobot_server.hostcheck import WrongHostError, require_server_host
from autobot_server.job import NotReady
from autobot_server.ollama import Ollama
from autobot_server.route import read_answer, route_request
from autobot_server.runtime import WARM_UP_PROMPT, Answer, Runtime, whole

UTTERANCES = Path("server/eval/utterances.jsonl")
REPORT_DIR = Path("run")
REPORT_NAME = "server-eval-{model}.json"

# The latency budget of IS-4, in seconds, measured here at the server.
ROUTE_BUDGET_SECONDS = 1.5
CHAT_FIRST_TOKEN_BUDGET_SECONDS = 2.0
# How many times the chat example is asked.
CHAT_RUNS = 5


class EvalError(Exception):
    """The evaluation cannot be run. The message is one line."""


@dataclass(frozen=True)
class Utterance:
    """One labelled, typed utterance: what was said, and where it should go."""

    text: str
    agent: str
    mode: str = ""
    parameters: dict[str, ParameterValue] = field(default_factory=dict)


@dataclass(frozen=True)
class RouteResult:
    """One utterance's answer, scored."""

    text: str
    expected: str
    # What the model answered; empty when its answer was not valid.
    agent: str
    mode: str
    parameters: dict[str, ParameterValue]
    valid: bool
    agent_right: bool
    mode_right: bool
    parameters_right: bool
    seconds: float
    # Why the answer was not valid; empty when it was.
    error: str = ""

    @property
    def right(self) -> bool:
        """Whether the agent, the mode and the parameters are all as labelled."""
        return self.agent_right and self.mode_right and self.parameters_right


@dataclass(frozen=True)
class ChatResult:
    """One streamed chat answer, timed."""

    first_token_seconds: float
    total_seconds: float
    completion_tokens: int
    valid: bool


def load_utterances(path: Path, agents: Sequence[RouteAgent]) -> list[Utterance]:
    """The labelled set; `EvalError` when a line is not a label for one of `agents`."""
    names = {agent.name for agent in agents} | {NO_AGENT}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise EvalError(f"cannot read {path}: {error.strerror}") from None
    utterances: list[Utterance] = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        where = f"{path.name} line {number}"
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            raise EvalError(f"{where} is not valid JSON") from None
        if not isinstance(data, dict) or not isinstance(data.get("text"), str):
            raise EvalError(f"{where} has no text")
        agent = data.get("agent")
        if agent not in names:
            raise EvalError(f"{where} names {agent!r}, which is not on the list")
        parameters = data.get("parameters", {})
        if not isinstance(parameters, dict):
            raise EvalError(f"{where}: parameters is not an object")
        utterances.append(
            Utterance(data["text"], str(agent), str(data.get("mode", "")), parameters)
        )
    if not utterances:
        raise EvalError(f"{path} holds no utterances")
    return utterances


def example_agents() -> tuple[RouteAgent, ...]:
    """The closed list the set is labelled against: the contract example's agents."""
    path = contract_dir() / "route-request.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return RouteRequest.from_json(data).agents
    except (OSError, json.JSONDecodeError, ContractError):
        raise EvalError(f"cannot read the contract example {path}") from None


def score_route(
    runtime: Runtime,
    utterance: Utterance,
    agents: Sequence[RouteAgent],
    clock: Callable[[], float] = time.monotonic,
) -> RouteResult:
    """Ask the model to route one utterance, as a request would, and score the answer."""
    request = RouteRequest(transcript=utterance.text, agents=tuple(agents))
    call = route_request(request)
    started = clock()
    seconds: float | None = None
    try:
        answer = whole(
            runtime.generate(call.system, call.messages, call.schema, call.tools)
        )
        seconds = clock() - started
        routed = read_answer(request, answer.content)
    except NotReady as error:
        # Timed once: when the answer came, or when the runtime failed.
        taken = seconds if seconds is not None else clock() - started
        return RouteResult(
            text=utterance.text,
            expected=utterance.agent,
            agent="",
            mode="",
            parameters={},
            valid=False,
            agent_right=False,
            mode_right=False,
            parameters_right=False,
            seconds=taken,
            error=str(error),
        )
    agent_right = routed.agent == utterance.agent
    return RouteResult(
        text=utterance.text,
        expected=utterance.agent,
        agent=routed.agent,
        mode=routed.mode,
        parameters=dict(routed.parameters),
        valid=True,
        agent_right=agent_right,
        mode_right=agent_right and routed.mode == utterance.mode,
        parameters_right=agent_right and routed.parameters == utterance.parameters,
        seconds=seconds,
    )


def score_chat(
    runtime: Runtime, request: ChatRequest, clock: Callable[[], float] = time.monotonic
) -> ChatResult:
    """Ask the chat example as a stream; time its first token and its end."""
    call = chat_request(request)
    started = clock()
    first: float | None = None
    final: Answer | None = None
    answer = runtime.generate(
        call.system, call.messages, call.schema, call.tools, stream=True
    )
    if isinstance(answer, Answer):
        first, final = clock() - started, answer
    else:
        for token in answer:
            if first is None and (token.text or token.final is not None):
                first = clock() - started
            if token.final is not None:
                final = token.final
    total = clock() - started
    valid = final is not None and bool(final.content.strip() or final.tool_calls)
    if valid and final is not None and request.response_schema and not final.tool_calls:
        try:
            valid = isinstance(json.loads(final.content), dict)
        except json.JSONDecodeError:
            valid = False
    return ChatResult(
        first_token_seconds=first if first is not None else total,
        total_seconds=total,
        completion_tokens=final.tokens.completion if final is not None else 0,
        valid=valid,
    )


def _share(count: int, of: int) -> float:
    return round(count / of, 4) if of else 0.0


def _spread(seconds: Sequence[float]) -> dict[str, float]:
    """The median, the 95th percentile and the longest of some times, in seconds."""
    if not seconds:
        return {"median": 0.0, "p95": 0.0, "max": 0.0}
    ordered = sorted(seconds)
    p95 = ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]
    return {
        "median": round(statistics.median(ordered), 3),
        "p95": round(p95, 3),
        "max": round(ordered[-1], 3),
    }


def report(
    model: str,
    routes: Sequence[RouteResult],
    chats: Sequence[ChatResult],
    at: str,
) -> dict[str, Any]:
    """The report file's contents: the figures first, then every answer."""
    count = len(routes)
    route_seconds = _spread([result.seconds for result in routes])
    first_token = _spread([chat.first_token_seconds for chat in chats])
    by_agent: dict[str, dict[str, int]] = {}
    for result in routes:
        tally = by_agent.setdefault(result.expected, {"utterances": 0, "right": 0})
        tally["utterances"] += 1
        tally["right"] += result.agent_right
    return {
        "model": model,
        "at": at,
        "route": {
            "utterances": count,
            # The agent alone, which is what routing depends on.
            "accuracy": _share(sum(result.agent_right for result in routes), count),
            # The agent, its mode and its parameters, all as labelled.
            "exact": _share(sum(result.right for result in routes), count),
            "json_validity": _share(sum(result.valid for result in routes), count),
            "seconds": route_seconds,
            "budget_seconds": ROUTE_BUDGET_SECONDS,
            "within_budget": bool(routes)
            and route_seconds["p95"] <= ROUTE_BUDGET_SECONDS,
            "by_agent": by_agent,
        },
        "chat": {
            "runs": len(chats),
            "json_validity": _share(sum(chat.valid for chat in chats), len(chats)),
            "first_token_seconds": first_token,
            "total_seconds": _spread([chat.total_seconds for chat in chats]),
            "budget_seconds": CHAT_FIRST_TOKEN_BUDGET_SECONDS,
            "within_budget": bool(chats)
            and first_token["p95"] <= CHAT_FIRST_TOKEN_BUDGET_SECONDS,
        },
        "wrong": [
            {
                "text": result.text,
                "expected": result.expected,
                "agent": result.agent,
                "mode": result.mode,
                "parameters": result.parameters,
                "error": result.error,
            }
            for result in routes
            if not result.right
        ],
        "answers": [asdict(result) for result in routes],
    }


def report_path(model: str) -> Path:
    """`run/server-eval-<model>.json`, the model's name made safe for a file name."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", model).strip("-") or "model"
    return resolve_checkout_path(REPORT_DIR / REPORT_NAME.format(model=safe))


def summary_lines(data: dict[str, Any]) -> list[str]:
    """What the terminal says of a report."""
    route, chat = data["route"], data["chat"]

    def within(part: dict[str, Any]) -> str:
        word = "within" if part["within_budget"] else "OVER"
        return f"{word} the {part['budget_seconds']:g} s budget"

    routed, first = route["seconds"], chat["first_token_seconds"]
    accuracy = (
        f"route accuracy: {route['accuracy']:.1%} of {route['utterances']} "
        f"(agent, mode and parameters: {route['exact']:.1%})"
    )
    validity = (
        f"JSON validity: {route['json_validity']:.1%} (route), "
        f"{chat['json_validity']:.1%} (chat)"
    )
    route_latency = (
        f"route latency: median {routed['median']:g} s, "
        f"p95 {routed['p95']:g} s: {within(route)}"
    )
    chat_latency = (
        f"chat first token: median {first['median']:g} s, "
        f"p95 {first['p95']:g} s: {within(chat)}"
    )
    return [
        f"model: {data['model']}",
        accuracy,
        validity,
        route_latency,
        chat_latency,
        f"chat full answer: median {chat['total_seconds']['median']:g} s",
    ]


def evaluate(
    runtime: Runtime,
    utterances: Sequence[Utterance],
    agents: Sequence[RouteAgent],
    chat: ChatRequest,
    *,
    chat_runs: int = CHAT_RUNS,
    clock: Callable[[], float] = time.monotonic,
    at: str = "",
) -> dict[str, Any]:
    """Run the whole evaluation of the runtime's model and build its report."""
    # One answer that is not timed, so the model is on the card for the rest.
    whole(runtime.generate("", (Message(role=ROLE_USER, content=WARM_UP_PROMPT),)))
    routes = [
        score_route(runtime, utterance, agents, clock) for utterance in utterances
    ]
    chats = [score_chat(runtime, chat, clock) for _ in range(chat_runs)]
    return report(runtime.model, routes, chats, at)


def chat_example() -> ChatRequest:
    path = contract_dir() / "chat-request.json"
    try:
        return ChatRequest.from_json(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ContractError):
        raise EvalError(f"cannot read the contract example {path}") from None


def main(
    argv: list[str] | None = None,
    *,
    hostname: Callable[[], str] = socket.gethostname,
) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m autobot_server.eval",
        description="Measure a language model against the real runtime (IS-26).",
    )
    parser.add_argument(
        "--model", default="", help="the model to measure (default: [server] llm_model)"
    )
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
        require_server_host(hostname(), settings.hostname)
        model = args.model.strip() or settings.llm_model
        if not model:
            raise EvalError(
                "no model to measure: name one with MODEL=, as "
                "'make server-eval MODEL=qwen3:8b'"
            )
        agents = example_agents()
        utterances = load_utterances(resolve_checkout_path(UTTERANCES), agents)
        chat = chat_example()
        runtime = Ollama(settings.runtime_url, model, settings.llm_context)
        runtime.check()
        print(f"measuring {model} on {len(utterances)} utterances...", flush=True)
        data = evaluate(
            runtime,
            utterances,
            agents,
            chat,
            at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        path = report_path(model)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    except (EvalError, NotReady, ServerConfigError, WrongHostError) as error:
        print(error, file=sys.stderr)
        return 1
    except OSError as error:
        print(f"cannot write the report: {error.strerror}", file=sys.stderr)
        return 1
    for line in summary_lines(data):
        print(line)
    print(f"report: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
