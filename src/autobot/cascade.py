"""The routing cascade: three tiers, and one rule that admits their answers (IS-17). No drawing.

The wake phrase only wakes autobot. What is said after it is tried against
three tiers in turn, cheapest first, after the global action keywords
(AG-14):

- **Tier 1.** The `[[routes]]` keyword rules, as `autobot.routing` matches
  them (FS-15). Instant, and its confidence always counts as 1.
- **Tier 2.** A semantic match on the NUC's CPU: the transcript and each
  rule's command and example phrases are embedded (`autobot.embedding`), and
  the rule whose phrase is nearest wins, with the cosine similarity as its
  confidence. The model is local, so this tier works with the server away.
- **Tier 3.** The inference server's language model, asked with the closed
  list of agents and their modes and parameters, when the watcher says its
  `route` job is ready (IS-9). It also fills the agent's parameters.

A tier only proposes. `admit` is the rules layer's one check of every
proposal, whichever tier made it, so that no query reaches an agent without
a rule deciding it may (RR-1):

- the agent exists, and is not the core agent, which is the default and
  never a route;
- the mode is one of the agent's modes, or empty;
- the parameters validate against the agent's definition: each is one of
  its slots, of the slot's type and among its values, and every required
  slot is filled;
- the confidence reaches that tier's threshold in `[server]`.

The first proposal admitted is the `Decision`, which carries the tier that
made it. When none is, the caller hands the request to the core agent if
the server is up, or says it did not catch that.

Tier 1 never blocks. Tiers 2 and 3 do (a model run, a request with a
1.5 s timeout), so the face runs `decide_slow` on a worker thread. Only a
transcript, and the agents' names, descriptions, commands, modes and
parameters, go to the server: the owner's own machine on the house LAN.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from autobot.config import AGENT_CORE, AgentConfig, Config, ParameterValue, RouteConfig
from autobot.embedding import Embedder, EmbeddingError, similarity
from autobot.routing import match_route
from autobot.server.client import ServerClient, ServerUnavailable
from autobot.server.contract import (
    NO_AGENT,
    Parameter,
    RouteAgent,
    RouteRequest,
    RouteResponse,
)
from autobot.server.watch import ServerWatch

TIER_KEYWORD = 1
TIER_SEMANTIC = 2
TIER_SERVER = 3
TIERS = (TIER_KEYWORD, TIER_SEMANTIC, TIER_SERVER)

# Tier 1's confidence: a keyword either stands in the transcript or does not.
KEYWORD_CONFIDENCE = 1.0


@dataclass(frozen=True)
class Decision:
    """What a tier proposes, and, once admitted, where the request goes."""

    agent: str
    # One of the agent's modes (AG-7); empty when there is none.
    mode: str
    # The slots the tier filled, by name (IS-18).
    parameters: Mapping[str, ParameterValue]
    # The tier that proposed it: one of `TIERS`.
    tier: int
    confidence: float
    # The words it was decided from.
    transcript: str
    # The rule tier 1 or 2 matched, and its place in the file, from 1.
    route: RouteConfig | None = None
    number: int = 0

    def describe(self) -> str:
        """One line for the detail panel, naming the tier (IS-17)."""
        if self.tier == TIER_KEYWORD and self.route is not None:
            how = f'rule {self.number}: "{self.route.command}"'
        else:
            how = f"{self.confidence:.2f}"
        text = f"tier {self.tier}: {how} -> {self.agent} agent"
        if self.mode:
            text += f" ({self.mode})"
        if self.parameters:
            slots = ", ".join(
                f"{name}={value}" for name, value in self.parameters.items()
            )
            text += f" [{slots}]"
        return text


class ServerRoute(Protocol):
    """Tier 3's way to the server: whether it can be asked, and the asking."""

    def ready(self) -> bool: ...

    def route(self, request: RouteRequest) -> RouteResponse: ...


@dataclass(frozen=True)
class ServerRouter:
    """Tier 3 over the client, asked only while the watcher says `route` is ready."""

    client: ServerClient
    watch: ServerWatch

    def ready(self) -> bool:
        return self.watch.ready("route")

    def route(self, request: RouteRequest) -> RouteResponse:
        return self.client.route(request)


@dataclass
class Notes:
    """Why tiers were passed over, a line each, for the detail panel."""

    lines: list[str] = field(default_factory=list)

    def add(self, tier: int, text: str) -> None:
        self.lines.append(f"tier {tier}: {text}")


# --- the agents' modes and the closed list ---------------------------------------


def agent_modes(config: Config, agent: str) -> tuple[str, ...]:
    """The modes of `agent`: those of its routing rules, without the empty mode."""
    modes: list[str] = []
    for route in config.routes:
        if route.agent == agent and route.mode and route.mode not in modes:
            modes.append(route.mode)
    return tuple(modes)


def routable_agents(config: Config) -> tuple[AgentConfig, ...]:
    """The agents a tier may route to: every one defined but the core agent."""
    return tuple(agent for agent in config.agents if agent.name != AGENT_CORE)


def route_request(transcript: str, config: Config) -> RouteRequest:
    """Tier 3's request: the transcript and the closed list (IS-9)."""
    agents = tuple(
        RouteAgent(
            name=agent.name,
            description=agent.description,
            commands=tuple(
                route.command for route in config.routes if route.agent == agent.name
            ),
            modes=agent_modes(config, agent.name),
            parameters=tuple(
                Parameter(
                    name=slot.name,
                    type=slot.type,
                    values=slot.values,
                    required=slot.required,
                )
                for slot in agent.parameters
            ),
        )
        for agent in routable_agents(config)
    )
    return RouteRequest(transcript=" ".join(transcript.split()), agents=agents)


# --- admission ---------------------------------------------------------------------


def threshold(config: Config, tier: int) -> float:
    """The confidence `tier` must reach (IS-17); tier 1 always reaches it."""
    if tier == TIER_SEMANTIC:
        return config.server.tier2_threshold
    if tier == TIER_SERVER:
        return config.server.tier3_threshold
    return KEYWORD_CONFIDENCE


def _fits(kind: str, values: tuple[ParameterValue, ...], value: object) -> bool:
    if kind == "boolean":
        typed = isinstance(value, bool)
    elif kind == "integer":
        typed = isinstance(value, int) and not isinstance(value, bool)
    elif kind == "number":
        typed = isinstance(value, int | float) and not isinstance(value, bool)
    else:
        typed = isinstance(value, str)
    return typed and (not values or value in values)


def refusal(decision: Decision, config: Config) -> str:
    """Why the rules layer does not admit `decision`, in a few words; empty when it does."""
    if decision.agent == AGENT_CORE:
        return "the core agent is the default, not a route"
    try:
        definition = config.agent(decision.agent)
    except KeyError:
        return f"{decision.agent} is not an agent"
    if decision.mode and decision.mode not in agent_modes(config, decision.agent):
        return f"{decision.mode} is not a mode of the {decision.agent} agent"
    slots = {slot.name: slot for slot in definition.parameters}
    for name, value in decision.parameters.items():
        slot = slots.get(name)
        if slot is None:
            return f"{name} is not a parameter of the {decision.agent} agent"
        if not _fits(slot.type, slot.values, value):
            return f"{name}={value!r} does not fit the {decision.agent} agent's {name}"
    for slot in definition.parameters:
        if slot.required and slot.name not in decision.parameters:
            return f"the {decision.agent} agent needs its {slot.name}"
    needed = threshold(config, decision.tier)
    if decision.confidence < needed:
        return f"{decision.confidence:.2f} is under the threshold {needed:g}"
    return ""


def admit(decision: Decision, config: Config) -> bool:
    """Whether the rules layer admits `decision` (IS-17, RR-1)."""
    return not refusal(decision, config)


# --- the tiers ---------------------------------------------------------------------


def tier1(transcript: str, config: Config) -> Decision | None:
    """The first keyword rule whose command stands in the transcript."""
    match = match_route(transcript, config.routes)
    if match is None:
        return None
    return Decision(
        agent=match.route.agent,
        mode=match.route.mode,
        parameters={},
        tier=TIER_KEYWORD,
        confidence=KEYWORD_CONFIDENCE,
        transcript=match.transcript,
        route=match.route,
        number=match.number,
    )


def tier2(transcript: str, config: Config, embedder: Embedder) -> Decision | None:
    """The rule whose command or example phrase is nearest in meaning.

    `EmbeddingError` when the model cannot be used.
    """
    phrases: list[str] = []
    owners: list[int] = []
    for index, route in enumerate(config.routes):
        for phrase in (route.command, *route.examples):
            if phrase.strip():
                phrases.append(phrase)
                owners.append(index)
    said = " ".join(transcript.split())
    if not phrases or not said:
        return None
    vectors = embedder.embed([said, *phrases])
    scores = similarity(vectors[0], vectors[1:])
    best = int(scores.argmax())
    route = config.routes[owners[best]]
    return Decision(
        agent=route.agent,
        mode=route.mode,
        parameters={},
        tier=TIER_SEMANTIC,
        confidence=float(scores[best]),
        transcript=said,
        route=route,
        number=owners[best] + 1,
    )


def tier3(transcript: str, config: Config, server: ServerRoute) -> Decision | None:
    """The server's model's choice from the closed list; None when it says `none`.

    `ServerUnavailable` when the server cannot answer in time.
    """
    request = route_request(transcript, config)
    answer = server.route(request)
    if answer.agent == NO_AGENT:
        return None
    return Decision(
        agent=answer.agent,
        mode=answer.mode,
        parameters=dict(answer.parameters),
        tier=TIER_SERVER,
        confidence=answer.confidence,
        transcript=request.transcript,
    )


# --- the cascade ---------------------------------------------------------------------


def decide_fast(
    transcript: str, config: Config, notes: Notes | None = None
) -> Decision | None:
    """Tier 1 alone: instant, and safe to call in a frame."""
    proposal = tier1(transcript, config)
    return _admitted(proposal, config, TIER_KEYWORD, notes)


def decide_slow(
    transcript: str,
    config: Config,
    embedder: Embedder | None,
    server: ServerRoute | None,
    notes: Notes | None = None,
) -> Decision | None:
    """Tiers 2 and 3, in turn. It blocks, so the face calls it on a worker thread."""
    if embedder is not None:
        try:
            proposal = tier2(transcript, config, embedder)
        except EmbeddingError as error:
            proposal = None
            if notes is not None:
                notes.add(TIER_SEMANTIC, str(error))
        decision = _admitted(proposal, config, TIER_SEMANTIC, notes)
        if decision is not None:
            return decision
    if server is not None and server.ready():
        try:
            proposal = tier3(transcript, config, server)
        except ServerUnavailable as error:
            proposal = None
            if notes is not None:
                notes.add(TIER_SERVER, str(error))
        else:
            if proposal is None and notes is not None:
                notes.add(TIER_SERVER, "no agent fits")
        return _admitted(proposal, config, TIER_SERVER, notes)
    return None


def decide(
    transcript: str,
    config: Config,
    embedder: Embedder | None,
    server: ServerRoute | None,
    notes: Notes | None = None,
) -> Decision | None:
    """The whole cascade: the first proposal the rules layer admits, or None."""
    decision = decide_fast(transcript, config, notes)
    if decision is not None:
        return decision
    return decide_slow(transcript, config, embedder, server, notes)


def _admitted(
    proposal: Decision | None, config: Config, tier: int, notes: Notes | None
) -> Decision | None:
    if proposal is None:
        return None
    why = refusal(proposal, config)
    if not why:
        return proposal
    if notes is not None:
        notes.add(tier, f"{proposal.agent} refused: {why}")
    return None
