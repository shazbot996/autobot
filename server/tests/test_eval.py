"""`autobot_server.eval`: the scoring, on a fake runtime (IS-26).

The evaluation itself runs on `GAMEON1` against a real model. Here only its
arithmetic is tested: what counts as right, valid and within the budget, and
what the report file holds. No model is asked.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autobot_server import eval as evaluation
from autobot_server.config import checkout_root
from autobot_server.contract import NO_AGENT, Tokens
from autobot_server.eval import (
    CHAT_FIRST_TOKEN_BUDGET_SECONDS,
    ROUTE_BUDGET_SECONDS,
    UTTERANCES,
    EvalError,
    Utterance,
    chat_example,
    evaluate,
    example_agents,
    load_utterances,
    report_path,
    score_chat,
    score_route,
    summary_lines,
)
from autobot_server.job import NotReady
from autobot_server.runtime import Answer
from tests.server_fakes import FakeClock, FakeRuntime

AGENTS = example_agents()


def routed(agent: str, mode: str = "", **parameters: object) -> Answer:
    return Answer(
        content=json.dumps(
            {"agent": agent, "mode": mode, "confidence": 0.9, "parameters": parameters}
        )
    )


class Ticking(FakeClock):
    """A clock that moves `step` seconds every time it is read."""

    def __init__(self, step: float) -> None:
        super().__init__()
        self.step = step

    def __call__(self) -> float:
        now = self.now
        self.now += self.step
        return now


# --- the committed set --------------------------------------------------------------


def test_the_committed_set_covers_every_agent_and_none() -> None:
    utterances = load_utterances(checkout_root() / UTTERANCES, AGENTS)
    labels = {utterance.agent for utterance in utterances}
    assert labels == {agent.name for agent in AGENTS} | {NO_AGENT}
    assert len(utterances) >= 50
    # Each agent, and none, has enough utterances to mean something.
    for label in labels:
        assert sum(utterance.agent == label for utterance in utterances) >= 5, label
    # A mode and a parameter are labelled too.
    assert any(utterance.mode == "forecast" for utterance in utterances)
    assert any(utterance.parameters == {"day": "tomorrow"} for utterance in utterances)
    # Typed text only: no audio, and no path to any (TS-5).
    assert all(
        set(vars(utterance)) == {"text", "agent", "mode", "parameters"}
        for utterance in utterances
    )
    assert len({utterance.text for utterance in utterances}) == len(utterances)


def test_every_label_is_one_the_list_allows() -> None:
    by_name = {agent.name: agent for agent in AGENTS}
    for utterance in load_utterances(checkout_root() / UTTERANCES, AGENTS):
        if utterance.agent == NO_AGENT:
            assert (utterance.mode, utterance.parameters) == ("", {})
            continue
        agent = by_name[utterance.agent]
        assert utterance.mode in ("", *agent.modes), utterance.text
        slots = {slot.name: slot for slot in agent.parameters}
        for name, value in utterance.parameters.items():
            assert value in slots[name].values, utterance.text


@pytest.mark.parametrize(
    ("line", "why"),
    [
        ("not json", "is not valid JSON"),
        ('{"agent": "time"}', "has no text"),
        (
            '{"text": "x", "agent": "lights"}',
            "names 'lights', which is not on the list",
        ),
        (
            '{"text": "x", "agent": "time", "parameters": []}',
            "parameters is not an object",
        ),
    ],
)
def test_a_line_that_is_not_a_label_is_refused(
    tmp_path: Path, line: str, why: str
) -> None:
    path = tmp_path / "utterances.jsonl"
    path.write_text(f"{line}\n", encoding="utf-8")
    with pytest.raises(EvalError) as refused:
        load_utterances(path, AGENTS)
    assert str(refused.value) == f"utterances.jsonl line 1 {why}" or why in str(
        refused.value
    )
    path.write_text("\n", encoding="utf-8")
    with pytest.raises(EvalError, match="holds no utterances"):
        load_utterances(path, AGENTS)
    with pytest.raises(EvalError, match="cannot read"):
        load_utterances(tmp_path / "missing.jsonl", AGENTS)


# --- scoring one answer -------------------------------------------------------------


def test_an_answer_is_right_when_agent_mode_and_parameters_are_as_labelled() -> None:
    utterance = Utterance(
        "what's the weather tomorrow", "weather", "", {"day": "tomorrow"}
    )
    clock = Ticking(0.4)
    result = score_route(
        FakeRuntime(routed("weather", day="tomorrow")), utterance, AGENTS, clock
    )
    assert (result.valid, result.agent_right, result.right) == (True, True, True)
    assert result.seconds == pytest.approx(0.4)
    # The right agent with the wrong mode or parameters is right for routing only.
    result = score_route(FakeRuntime(routed("weather", "forecast")), utterance, AGENTS)
    assert (result.agent_right, result.mode_right, result.parameters_right) == (
        True,
        False,
        False,
    )
    assert not result.right
    result = score_route(FakeRuntime(routed("time")), utterance, AGENTS)
    assert (result.valid, result.agent_right, result.right) == (True, False, False)


def test_none_is_right_only_when_none_was_labelled() -> None:
    chat = Utterance("tell me a joke", NO_AGENT)
    assert score_route(FakeRuntime(routed(NO_AGENT)), chat, AGENTS).right
    assert not score_route(FakeRuntime(routed("question")), chat, AGENTS).right


def test_an_answer_that_is_not_the_schema_is_not_valid_and_not_right() -> None:
    utterance = Utterance("what time is it", "time")
    for answer in (Answer(content="the time agent"), routed("clock")):
        result = score_route(FakeRuntime(answer), utterance, AGENTS)
        assert (result.valid, result.agent_right, result.right) == (False, False, False)
        assert result.error.startswith("route is not ready: ")
    runtime = FakeRuntime()
    runtime.error = NotReady("the runtime at http://127.0.0.1:11434 is not answering")
    result = score_route(runtime, utterance, AGENTS)
    assert not result.valid and "is not answering" in result.error


def test_a_chat_is_timed_to_its_first_token_and_to_its_end() -> None:
    content = '{"summary": "Your nine minute timer is running.", "detail": []}'
    runtime = FakeRuntime(
        Answer(content=content, tokens=Tokens(prompt=400, completion=20))
    )
    clock = Ticking(0.25)
    result = score_chat(runtime, chat_example(), clock)
    # The clock is read at the start, at the first token and at the end.
    assert result.first_token_seconds == pytest.approx(0.25)
    assert result.total_seconds == pytest.approx(0.5)
    assert (result.valid, result.completion_tokens) == (True, 20)
    assert runtime.calls[0].stream is True
    assert not score_chat(FakeRuntime(Answer(content="not json")), chat_example()).valid
    assert not score_chat(FakeRuntime(Answer(content="")), chat_example()).valid


# --- the report ---------------------------------------------------------------------


def run(
    runtime: FakeRuntime, clock: FakeClock, utterances: list[Utterance]
) -> dict[str, object]:
    return evaluate(
        runtime,
        utterances,
        AGENTS,
        chat_example(),
        chat_runs=2,
        clock=clock,
        at="2026-11-27T22:30:00-05:00",
    )


def test_the_report_holds_accuracy_validity_latency_and_the_budget() -> None:
    utterances = [
        Utterance("what time is it", "time"),
        Utterance("give me the forecast", "weather", "forecast"),
        Utterance("tell me a joke", NO_AGENT),
        Utterance("who are you", "introduce"),
    ]
    chat = Answer(content='{"summary": "Done.", "detail": []}')
    runtime = FakeRuntime(
        Answer(content="ready"),
        routed("time"),
        routed("weather"),
        Answer(content="chatting instead of routing"),
        routed("introduce"),
        chat,
        model="qwen3:8b",
    )
    data = run(runtime, Ticking(0.3), utterances)
    assert data["model"] == "qwen3:8b" and data["at"] == "2026-11-27T22:30:00-05:00"
    route = data["route"]
    assert isinstance(route, dict)
    assert route["utterances"] == 4
    # Three agents right of four; the forecast's mode was missed; one was not JSON.
    assert (route["accuracy"], route["exact"], route["json_validity"]) == (
        0.75,
        0.5,
        0.75,
    )
    assert route["seconds"] == {"median": 0.3, "p95": 0.3, "max": 0.3}
    assert (route["budget_seconds"], route["within_budget"]) == (
        ROUTE_BUDGET_SECONDS,
        True,
    )
    assert route["by_agent"] == {
        "time": {"utterances": 1, "right": 1},
        "weather": {"utterances": 1, "right": 1},
        NO_AGENT: {"utterances": 1, "right": 0},
        "introduce": {"utterances": 1, "right": 1},
    }
    chat_part = data["chat"]
    assert isinstance(chat_part, dict)
    assert (chat_part["runs"], chat_part["json_validity"]) == (2, 1.0)
    assert chat_part["first_token_seconds"]["median"] == 0.3
    assert chat_part["within_budget"] is True
    assert chat_part["budget_seconds"] == CHAT_FIRST_TOKEN_BUDGET_SECONDS
    wrong = data["wrong"]
    assert isinstance(wrong, list)
    assert [entry["text"] for entry in wrong] == [
        "give me the forecast",
        "tell me a joke",
    ]
    answers = data["answers"]
    assert isinstance(answers, list) and len(answers) == 4
    # The warm-up answer is not timed and not scored.
    assert len(runtime.calls) == 1 + 4 + 2
    lines = summary_lines(data)
    assert lines[0] == "model: qwen3:8b"
    assert lines[1] == (
        "route accuracy: 75.0% of 4 (agent, mode and parameters: 50.0%)"
    )
    assert "within the 1.5 s budget" in lines[3]
    assert "within the 2 s budget" in lines[4]


def test_a_model_that_is_too_slow_is_over_the_budget() -> None:
    runtime = FakeRuntime(Answer(content="ready"), routed("time"))
    data = run(runtime, Ticking(2.5), [Utterance("what time is it", "time")])
    route, chat = data["route"], data["chat"]
    assert isinstance(route, dict) and isinstance(chat, dict)
    assert (route["within_budget"], chat["within_budget"]) == (False, False)
    lines = summary_lines(data)
    assert "OVER the 1.5 s budget" in lines[3] and "OVER the 2 s budget" in lines[4]


def test_the_report_is_named_for_the_model() -> None:
    assert report_path("qwen3:8b").name == "server-eval-qwen3-8b.json"
    assert report_path("hf.co/org/Model-GGUF:Q4_K_M").name == (
        "server-eval-hf.co-org-Model-GGUF-Q4_K_M.json"
    )
    assert report_path("qwen3:8b").parent == checkout_root() / "run"
    assert report_path(":").name == "server-eval-model.json"


def test_the_evaluation_refuses_off_the_server_host(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert evaluation.main(["--model", "qwen3:8b"], hostname=lambda: "branch") == 1
    assert capsys.readouterr().err.strip() == (
        "the inference server runs on GAMEON1 only; this host is branch"
    )
