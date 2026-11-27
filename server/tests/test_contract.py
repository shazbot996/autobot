"""`autobot_server.contract`: the server reads every example under `server/contract/` (IS-12).

The NUC's suite reads the same files through `autobot.server.contract`; a
field renamed in an example, or in either side's shape, fails one of them.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from autobot_server.contract import (
    ChatRequest,
    ChatResponse,
    ContractError,
    ContractInfo,
    Health,
    RouteRequest,
    RouteResponse,
    Shape,
    SttRequest,
    SttResponse,
    contract_dir,
    contract_version,
    format_event,
    iter_stream,
)

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


def test_every_example_has_a_shape() -> None:
    assert {path.name for path in contract_dir().iterdir()} == {*EXAMPLES, STREAM}


@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_an_example_reads_back_as_it_stands(name: str) -> None:
    data = example(name)
    assert normalised(EXAMPLES[name].from_json(data).to_json()) == normalised(data)


@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_a_renamed_field_is_refused(name: str) -> None:
    data = example(name)
    first = next(iter(data))
    data[f"{first}_renamed"] = data.pop(first)
    with pytest.raises(ContractError, match=f"unknown field: {first}_renamed"):
        EXAMPLES[name].from_json(data)


def test_the_stream_example_reads_back_as_it_stands() -> None:
    text = (contract_dir() / STREAM).read_text(encoding="utf-8")
    events = list(iter_stream(text.splitlines(keepends=True)))
    assert "".join(format_event(event) for event in events) == text


def test_the_version_is_read_from_contract_json() -> None:
    assert contract_version() == "1"
