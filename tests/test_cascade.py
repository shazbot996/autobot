"""`autobot.cascade`: three tiers, and the one rule that admits their answers (IS-17)."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from agents.agent_fakes import FakeEmbedder
from autobot.cascade import (
    TIER_KEYWORD,
    TIER_SEMANTIC,
    TIER_SERVER,
    Decision,
    Notes,
    ServerRouter,
    admit,
    agent_modes,
    decide,
    decide_fast,
    decide_slow,
    refusal,
    route_request,
    tier2,
)
from autobot.config import Config, parse_config
from autobot.embedding import EmbeddingError
from autobot.server.contract import RouteRequest
from autobot.server.watch import DEGRADED, DOWN
from server.server_fakes import (
    CONFIG_TEXT,
    FakeTransport,
    FakeWatch,
    client_on,
    example,
)

OUTSIDE = "what's it like outside"
NEAR_WEATHER = {OUTSIDE: ("weather", 0.81)}

# The weather agent with a slot, and a rule that carries example phrases.
SLOTS = """
[[routes]]
command = "weather"
agent = "weather"
examples = ["do I need a coat", "is it going to rain"]

[[routes]]
command = "forecast"
agent = "weather"
mode = "forecast"

[[routes]]
command = "time"
agent = "time"

[[agents]]
name = "weather"
description = "Says the weather."

[[agents.parameters]]
name = "day"
type = "string"
values = ["today", "tomorrow"]

[[agents]]
name = "time"
description = "Says the time."

[[agents.parameters]]
name = "zone"
type = "string"
required = true
"""


def config(extra: str = "") -> Config:
    return parse_config(tomllib.loads(CONFIG_TEXT + extra))


def server(tmp_path: Path, answer: Any = None, watch: FakeWatch | None = None) -> Any:
    """Tier 3 on a fake transport that answers `/v1/route` with `answer`."""
    transport = FakeTransport()
    if answer is not None:
        transport.answers["/v1/route"] = (200, answer)
    router = ServerRouter(client_on(tmp_path, transport), watch or FakeWatch())
    return router, transport


def route_answer(agent: str, confidence: float = 0.9, **more: Any) -> dict[str, Any]:
    return {
        "agent": agent,
        "mode": more.get("mode", ""),
        "confidence": confidence,
        "parameters": more.get("parameters", {}),
    }


# --- tier 1 ---------------------------------------------------------------------


def test_a_keyword_decides_at_once_and_asks_nobody(tmp_path: Path) -> None:
    embedder = FakeEmbedder()
    router, transport = server(tmp_path)
    decision = decide("what's the forecast", config(), embedder, router)
    assert decision is not None
    assert (decision.agent, decision.mode, decision.tier) == (
        "weather",
        "forecast",
        TIER_KEYWORD,
    )
    assert decision.confidence == 1.0 and decision.number == 3
    assert decision.describe() == (
        'tier 1: rule 3: "forecast" -> weather agent (forecast)'
    )
    assert embedder.calls == [] and transport.sent == []
    assert decide_fast("nothing like a command", config()) is None


# --- tier 2 ---------------------------------------------------------------------


def test_a_sentence_near_a_command_is_routed_by_tier_2(tmp_path: Path) -> None:
    embedder = FakeEmbedder(NEAR_WEATHER)
    router, transport = server(tmp_path)
    decision = decide(OUTSIDE, config(), embedder, router)
    assert decision is not None
    assert (decision.agent, decision.tier) == ("weather", TIER_SEMANTIC)
    assert decision.confidence == pytest.approx(0.81)
    assert decision.describe() == "tier 2: 0.81 -> weather agent"
    # Admitted on the NUC: the server was not asked.
    assert transport.sent == []


def test_tier_2_compares_with_every_command_and_example(tmp_path: Path) -> None:
    embedder = FakeEmbedder({"should I take a coat": ("do I need a coat", 0.9)})
    decision = tier2("should I  take a coat", config(SLOTS), embedder)
    assert decision is not None and decision.agent == "weather"
    assert (decision.number, decision.mode) == (1, "")
    assert embedder.calls == [
        [
            "should I take a coat",
            "weather",
            "do I need a coat",
            "is it going to rain",
            "forecast",
            "time",
        ]
    ]
    # The rule's own mode comes with a match on that rule.
    nearer = FakeEmbedder({"the days ahead": ("forecast", 0.7)})
    ahead = tier2("the days ahead", config(SLOTS), nearer)
    assert ahead is not None and (ahead.mode, ahead.number) == ("forecast", 2)


def test_tier_2_under_its_threshold_is_refused_with_a_note(tmp_path: Path) -> None:
    embedder = FakeEmbedder({OUTSIDE: ("weather", 0.41)})
    notes = Notes()
    assert decide_slow(OUTSIDE, config(), embedder, None, notes) is None
    assert notes.lines == ["tier 2: weather refused: 0.41 is under the threshold 0.6"]


def test_tier_2_works_while_the_server_is_away(tmp_path: Path) -> None:
    router, transport = server(tmp_path, watch=FakeWatch(DOWN))
    decision = decide(OUTSIDE, config(), FakeEmbedder(NEAR_WEATHER), router)
    assert decision is not None and decision.tier == TIER_SEMANTIC
    assert transport.sent == []


def test_a_missing_embedding_model_is_a_note_and_tier_3_still_runs(
    tmp_path: Path,
) -> None:
    embedder = FakeEmbedder()
    embedder.error = EmbeddingError("embedding model file is missing: model_int8.onnx")
    router, _transport = server(tmp_path, route_answer("weather"))
    notes = Notes()
    decision = decide(OUTSIDE, config(), embedder, router, notes)
    assert decision is not None and decision.tier == TIER_SERVER
    assert notes.lines == ["tier 2: embedding model file is missing: model_int8.onnx"]


# --- tier 3 ---------------------------------------------------------------------


def test_tier_3_asks_the_server_with_the_closed_list(tmp_path: Path) -> None:
    router, transport = server(
        tmp_path, route_answer("weather", 0.86, parameters={"day": "today"})
    )
    decision = decide("will I need an umbrella", config(SLOTS), FakeEmbedder(), router)
    assert decision is not None
    assert (decision.agent, decision.tier, decision.parameters) == (
        "weather",
        TIER_SERVER,
        {"day": "today"},
    )
    assert decision.describe() == "tier 3: 0.86 -> weather agent [day=today]"
    (sent,) = transport.sent
    assert (sent.path, sent.timeout) == ("/v1/route", 1.5)
    request = RouteRequest.from_json(sent.body)
    assert request.transcript == "will I need an umbrella"
    names = [agent.name for agent in request.agents]
    # Every agent but the core agent, which is the default and not a route.
    assert "weather" in names and "time" in names and "core" not in names
    weather = next(agent for agent in request.agents if agent.name == "weather")
    assert weather.commands == ("weather", "forecast")
    assert weather.modes == ("forecast",)
    assert [(slot.name, slot.values) for slot in weather.parameters] == [
        ("day", ("today", "tomorrow"))
    ]


def test_the_request_of_the_shipped_shape_matches_the_contract_example() -> None:
    request = route_request("will I need an umbrella this afternoon", config())
    expected = RouteRequest.from_json(example("route-request.json"))
    by_name = {agent.name: agent for agent in request.agents}
    for agent in expected.agents:
        assert by_name[agent.name].commands == agent.commands
        assert by_name[agent.name].modes == agent.modes


@pytest.mark.parametrize(
    ("answer", "why"),
    [
        (route_answer("lights"), "lights refused: lights is not an agent"),
        (
            route_answer("core"),
            "core refused: the core agent is the default, not a route",
        ),
        (
            route_answer("time", mode="forecast", parameters={"zone": "utc"}),
            "time refused: forecast is not a mode of the time agent",
        ),
        (
            route_answer("weather", parameters={"zip": "12345"}),
            "weather refused: zip is not a parameter of the weather agent",
        ),
        (
            route_answer("weather", parameters={"day": "someday"}),
            "weather refused: day='someday' does not fit the weather agent's day",
        ),
        (route_answer("time"), "time refused: the time agent needs its zone"),
        (
            route_answer("weather", 0.69),
            "weather refused: 0.69 is under the threshold 0.7",
        ),
    ],
)
def test_the_rules_layer_refuses_what_it_cannot_admit(
    tmp_path: Path, answer: dict[str, Any], why: str
) -> None:
    router, _transport = server(tmp_path, answer)
    notes = Notes()
    assert decide("do something", config(SLOTS), None, router, notes) is None
    assert notes.lines == [f"tier 3: {why}"]


def test_none_from_the_server_is_no_decision(tmp_path: Path) -> None:
    router, _transport = server(tmp_path, route_answer("none", 0.9))
    notes = Notes()
    assert decide("how are you", config(), None, router, notes) is None
    assert notes.lines == ["tier 3: no agent fits"]


def test_the_server_is_not_asked_while_its_route_job_is_not_ready(
    tmp_path: Path,
) -> None:
    for watch in (FakeWatch(DOWN), FakeWatch(DEGRADED, route="loading")):
        router, transport = server(tmp_path, route_answer("weather"), watch)
        assert decide("do something", config(), FakeEmbedder(), router) is None
        assert transport.sent == []


def test_a_server_that_times_out_is_a_note_and_no_decision(tmp_path: Path) -> None:
    router, transport = server(tmp_path)
    transport.error = TimeoutError("timed out")
    notes = Notes()
    assert decide("do something", config(), None, router, notes) is None
    assert notes.lines == ["tier 3: the server is away: timed out"]
    # The request was made once and not tried again.
    assert len(transport.sent) == 1


# --- admission ------------------------------------------------------------------


def test_a_keyword_cannot_fill_a_required_slot_so_the_next_tier_is_asked(
    tmp_path: Path,
) -> None:
    router, _transport = server(
        tmp_path, route_answer("time", parameters={"zone": "utc"})
    )
    notes = Notes()
    decision = decide("what time is it", config(SLOTS), None, router, notes)
    assert decision is not None
    assert (decision.tier, decision.parameters) == (TIER_SERVER, {"zone": "utc"})
    assert notes.lines == ["tier 1: time refused: the time agent needs its zone"]


def test_admit_is_the_one_check_of_every_tier() -> None:
    shipped = config(SLOTS)
    good = Decision("weather", "forecast", {"day": "tomorrow"}, TIER_SERVER, 0.7, "x")
    assert admit(good, shipped) and refusal(good, shipped) == ""
    assert agent_modes(shipped, "weather") == ("forecast",)
    assert agent_modes(shipped, "time") == ()
    wrong_type = Decision("weather", "", {"day": 3}, TIER_SERVER, 0.9, "x")
    assert not admit(wrong_type, shipped)
    # A bool is not an integer, and an integer is a number.
    numbers = config(
        """
[[agents]]
name = "echo"

[[agents.parameters]]
name = "times"
type = "integer"

[[agents.parameters]]
name = "volume"
type = "number"

[[agents.parameters]]
name = "loud"
type = "boolean"
"""
    )
    fits = Decision(
        "echo", "", {"times": 2, "volume": 3, "loud": True}, TIER_SERVER, 0.9, "x"
    )
    assert admit(fits, numbers)
    assert not admit(
        Decision("echo", "", {"times": True}, TIER_SERVER, 0.9, "x"), numbers
    )
    assert not admit(Decision("echo", "", {"loud": 1}, TIER_SERVER, 0.9, "x"), numbers)
