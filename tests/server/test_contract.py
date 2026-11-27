"""`autobot.server.contract`: every example under `server/contract/` reads back as it stands.

The examples are the contract both hosts check against (IS-12). Each one is
read through its shape and written back, and must equal the file after both
are normalised, so a field renamed in an example or in a shape fails here.
"""

from __future__ import annotations

import io
import json
import wave
from pathlib import Path
from typing import Any

import pytest

from autobot.server.contract import (
    NO_AGENT,
    ChatRequest,
    ChatResponse,
    ContractError,
    ContractInfo,
    Health,
    Message,
    RouteRequest,
    RouteResponse,
    Shape,
    StreamDone,
    StreamToken,
    SttRequest,
    SttResponse,
    Tokens,
    contract_dir,
    contract_version,
    format_event,
    iter_stream,
)

# Each JSON example and the shape that reads it.
EXAMPLES: dict[str, type[Shape]] = {
    "contract.json": ContractInfo,
    "health.json": Health,
    "stt-request.json": SttRequest,
    "stt-response.json": SttResponse,
    "route-request.json": RouteRequest,
    "route-response.json": RouteResponse,
    "chat-request.json": ChatRequest,
    "chat-response.json": ChatResponse,
}
STREAM = "chat-stream.txt"


def example(name: str) -> Any:
    return json.loads((contract_dir() / name).read_text(encoding="utf-8"))


def normalised(data: Any) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False)


def test_the_contract_is_under_server_contract() -> None:
    assert contract_dir().parts[-2:] == ("server", "contract")
    assert contract_dir().is_dir()


def test_every_example_has_a_shape() -> None:
    names = {path.name for path in contract_dir().iterdir()}
    assert names == {*EXAMPLES, STREAM}


@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_an_example_reads_back_as_it_stands(name: str) -> None:
    data = example(name)
    shape = EXAMPLES[name].from_json(data)
    assert normalised(shape.to_json()) == normalised(data)


@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_a_renamed_field_in_an_example_is_refused(name: str) -> None:
    data = example(name)
    first = next(iter(data))
    data[f"{first}_renamed"] = data.pop(first)
    with pytest.raises(ContractError, match=f"unknown field: {first}_renamed"):
        EXAMPLES[name].from_json(data)


def test_a_missing_required_field_is_named() -> None:
    data = example("stt-response.json")
    del data["model"]
    with pytest.raises(ContractError, match="SttResponse is missing the field: model"):
        SttResponse.from_json(data)


def test_a_nested_value_of_the_wrong_type_is_named() -> None:
    data = example("health.json")
    data["jobs"]["chat"]["ready"] = "yes"
    with pytest.raises(
        ContractError, match=r"Health\.jobs\.chat\.ready is not a boolean"
    ):
        Health.from_json(data)


def test_a_boolean_is_not_an_integer() -> None:
    data = example("health.json")
    data["gpu"]["free_mib"] = True
    with pytest.raises(ContractError, match="free_mib is not an integer"):
        Health.from_json(data)


def test_the_version_lives_in_contract_json_and_health_reports_it() -> None:
    assert contract_version() == "1"
    assert Health.from_json(example("health.json")).contract_version == "1"


def test_health_says_tts_is_not_ready() -> None:
    health = Health.from_json(example("health.json"))
    assert health.tts is False
    budget = health.budget
    assert budget.total_mib == (
        budget.stt_mib
        + budget.llm_weights_mib
        + budget.llm_kv_cache_mib
        + budget.cuda_contexts_mib
    )


def test_the_stt_request_carries_a_16_khz_mono_16_bit_wav() -> None:
    request = SttRequest.from_json(example("stt-request.json"))
    with wave.open(io.BytesIO(request.audio), "rb") as clip:
        assert (clip.getframerate(), clip.getnchannels(), clip.getsampwidth()) == (
            16000,
            1,
            2,
        )
    assert SttRequest.from_wav(request.audio, request.hints) == request


def test_audio_that_is_not_base64_is_refused() -> None:
    with pytest.raises(ContractError, match="not base64"):
        assert SttRequest(audio_base64="not base64!").audio


def test_the_route_answer_names_an_agent_and_mode_of_the_request() -> None:
    request = RouteRequest.from_json(example("route-request.json"))
    answer = RouteResponse.from_json(example("route-response.json"))
    agents = {agent.name: agent for agent in request.agents}
    assert answer.agent in agents and answer.agent != NO_AGENT
    assert answer.mode in ("", *agents[answer.agent].modes)
    slots = {slot.name: slot for slot in agents[answer.agent].parameters}
    for name, value in answer.parameters.items():
        assert value in slots[name].values


def test_the_chat_request_carries_a_tool_round() -> None:
    request = ChatRequest.from_json(example("chat-request.json"))
    call = request.messages[3].tool_calls[0]
    assert call.name == request.tools[0].name
    assert request.messages[4].tool_call_id == call.id
    assert request.response_schema is not None


def test_empty_optional_fields_are_left_out() -> None:
    assert Message(role="user", content="hello").to_json() == {
        "role": "user",
        "content": "hello",
    }
    request = ChatRequest(system="", messages=())
    assert request.to_json() == {"system": "", "messages": [], "stream": False}


def test_a_message_of_an_unknown_role_is_refused() -> None:
    with pytest.raises(ContractError, match="Message.role 'system' is not one of"):
        Message.from_json({"role": "system", "content": "hi"})


def test_the_stream_example_reads_back_as_it_stands() -> None:
    text = (contract_dir() / STREAM).read_text(encoding="utf-8")
    events = list(iter_stream(text.splitlines(keepends=True)))
    assert "".join(format_event(event) for event in events) == text
    *tokens, done = events
    assert all(isinstance(token, StreamToken) for token in tokens)
    assert isinstance(done, StreamDone)
    assert "".join(
        token.text for token in tokens if isinstance(token, StreamToken)
    ) == ("It's a quarter past three. Anything else?")


def test_a_stream_skips_comments_and_joins_data_lines() -> None:
    lines = [
        ": keep-alive",
        "",
        "event: token",
        'data: {"text":',
        'data: "hi"}',
        "",
        "event: done",
        'data: {"model": "m", "tokens": {"prompt": 1, "completion": 1}}',
        "",
    ]
    assert list(iter_stream(lines)) == [
        StreamToken("hi"),
        StreamDone("m", Tokens(1, 1)),
    ]


def test_an_unknown_stream_event_is_refused() -> None:
    with pytest.raises(ContractError, match="stream event 'error' is not one of"):
        list(iter_stream(["event: error", 'data: {"text": "x"}', ""]))


def test_a_missing_contract_file_is_one_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("autobot.server.contract.contract_dir", lambda: tmp_path)
    with pytest.raises(ContractError, match="contract file not found"):
        contract_version()
