"""Routing: `POST /v1/route` (IS-9, IS-17).

The request carries a transcript and the closed list of agents the NUC can
route to. The server holds no agent list of its own (IS-21): everything the
model is told about an agent comes with the request.

The answer is constrained while the model generates it. `route_schema`
builds a JSON schema whose `agent` enum is the list's names plus `none` and
whose `mode` enum is the list's modes plus the empty mode, and the runtime
generates against it, so a name outside the list cannot be produced. The
answer is then checked again before it is sent: an agent outside the list
is never returned, and the request is refused as not ready, so that the NUC
falls to its next step (IS-4). A mode that is not one of the chosen agent's
becomes the empty mode, and a parameter that is not one of the agent's
slots, or does not fit its type and values, is left out. The rules layer on
the NUC admits or refuses what is left (IS-17).

Routing never streams, and keeps nothing (IS-5).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from autobot_server.app import Endpoint, Response, json_response, request_json
from autobot_server.contract import (
    NO_AGENT,
    ROLE_USER,
    Message,
    ParameterValue,
    RouteAgent,
    RouteRequest,
    RouteResponse,
)
from autobot_server.job import Job, NotReady
from autobot_server.runtime import (
    JSON_TYPES,
    Answer,
    Request,
    Token,
    fits,
    whole,
)

ROUTE_PATH = "/v1/route"

ROUTE_SYSTEM = """\
You are the router of autobot, a voice assistant at home. You are given what
the owner said, as transcribed speech, and the agents autobot has. Choose the
one agent whose job is what the owner asked for.

Answer with JSON only:
- "agent": the name of the agent, or "none" when no agent fits, or when the
  owner is only talking and not asking for one of them.
- "mode": one of the modes listed for that agent when what was said asks for
  it, otherwise "".
- "confidence": how sure you are, from 0 to 1.
- "parameters": the parameters listed for that agent that the owner gave,
  by name; leave out any the owner did not say.

The transcript may have small mistakes of hearing. Do not answer what was
asked; only route it.

The agents:
{agents}
"""


def route_schema(agents: tuple[RouteAgent, ...]) -> dict[str, Any]:
    """The JSON schema a route answer is generated against: the closed list (IS-9)."""
    names = [agent.name for agent in agents]
    modes: list[str] = [""]
    slots: dict[str, Any] = {}
    # A slot's name with no enum: some agent takes any value of its type.
    open_slots: set[str] = set()
    for agent in agents:
        modes.extend(mode for mode in agent.modes if mode not in modes)
        for parameter in agent.parameters:
            slot = slots.setdefault(
                parameter.name,
                {"type": JSON_TYPES.get(parameter.type, "string"), "enum": []},
            )
            if not parameter.values:
                open_slots.add(parameter.name)
            # Two agents may share a slot's name: either one's values will do.
            slot["enum"].extend(
                value for value in parameter.values if value not in slot["enum"]
            )
    for name, slot in slots.items():
        if name in open_slots or not slot["enum"]:
            del slot["enum"]
    return {
        "type": "object",
        "properties": {
            "agent": {"type": "string", "enum": [*names, NO_AGENT]},
            "mode": {"type": "string", "enum": modes},
            "confidence": {"type": "number"},
            "parameters": {"type": "object", "properties": slots},
        },
        "required": ["agent", "mode", "confidence", "parameters"],
    }


def describe_agents(agents: tuple[RouteAgent, ...]) -> str:
    """The closed list as the model reads it, one agent to a line."""
    lines: list[str] = []
    for agent in agents:
        parts = [f"- {agent.name}: {agent.description}"]
        if agent.commands:
            parts.append(f"Commands: {', '.join(agent.commands)}.")
        if agent.modes:
            parts.append(f"Modes: {', '.join(agent.modes)}.")
        if agent.parameters:
            slots = []
            for parameter in agent.parameters:
                allowed = ", ".join(str(value) for value in parameter.values)
                slots.append(
                    f"{parameter.name} ({parameter.type}"
                    f"{': ' + allowed if allowed else ''})"
                )
            parts.append(f"Parameters: {'; '.join(slots)}.")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def route_request(request: RouteRequest) -> Request:
    """The call of the model for one route request."""
    return Request(
        system=ROUTE_SYSTEM.format(agents=describe_agents(request.agents)),
        messages=(Message(role=ROLE_USER, content=request.transcript),),
        schema=route_schema(request.agents),
    )


def read_answer(request: RouteRequest, content: str) -> RouteResponse:
    """The model's answer, checked against the closed list again (IS-9).

    `NotReady` when it is not the schema's shape or names an agent outside
    the list: such an answer is never returned.
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        raise NotReady("route is not ready: the model's answer is not JSON") from None
    if not isinstance(data, dict):
        raise NotReady("route is not ready: the model's answer is not a JSON object")
    name = data.get("agent")
    agents = {agent.name: agent for agent in request.agents}
    if not isinstance(name, str) or (name != NO_AGENT and name not in agents):
        raise NotReady("route is not ready: the model named an agent outside the list")
    confidence = data.get("confidence")
    if not isinstance(confidence, int | float) or isinstance(confidence, bool):
        raise NotReady("route is not ready: the model gave no confidence")
    confidence = min(1.0, max(0.0, float(confidence)))
    if name == NO_AGENT:
        return RouteResponse(
            agent=NO_AGENT, mode="", confidence=confidence, parameters={}
        )
    agent = agents[name]
    mode = data.get("mode")
    given = data.get("parameters")
    slots = {parameter.name: parameter for parameter in agent.parameters}
    parameters: dict[str, ParameterValue] = {}
    for key, value in (given if isinstance(given, dict) else {}).items():
        slot = slots.get(key)
        if slot is not None and fits(slot, value):
            parameters[key] = value
    return RouteResponse(
        agent=name,
        mode=mode if isinstance(mode, str) and mode in agent.modes else "",
        confidence=confidence,
        parameters=parameters,
    )


def route_endpoint(job: Job[Request, Answer | Iterator[Token]]) -> Endpoint:
    """`POST /v1/route`: the transcript through `job`, as the IS-9 body."""

    def endpoint(body: bytes) -> Response:
        request = RouteRequest.from_json(request_json(body))
        answer = whole(job.run(route_request(request)))
        return json_response(200, read_answer(request, answer.content).to_json())

    return endpoint
