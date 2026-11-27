"""`autobot_server.route`: `POST /v1/route` on a fake runtime (IS-9, IS-17)."""

from __future__ import annotations

import json
from typing import Any

from autobot_server.app import Response
from autobot_server.contract import (
    NO_AGENT,
    Parameter,
    RouteAgent,
    RouteRequest,
    contract_dir,
)
from autobot_server.job import READY
from autobot_server.route import (
    ROUTE_PATH,
    describe_agents,
    route_endpoint,
    route_schema,
)
from autobot_server.runtime import Answer
from tests.server_fakes import BEARER, FakeRuntime, llm_job, make_app


def example(name: str) -> Any:
    return json.loads((contract_dir() / name).read_text(encoding="utf-8"))


def body_of(response: Response) -> dict[str, Any]:
    assert isinstance(response.body, bytes)
    data = json.loads(response.body)
    assert isinstance(data, dict)
    return data


def answer(**fields: object) -> Answer:
    return Answer(content=json.dumps(fields))


def post(runtime: FakeRuntime, request: object | None = None) -> Response:
    job = llm_job("route", runtime)
    app = make_app(endpoints={ROUTE_PATH: route_endpoint(job)})
    body = json.dumps(request or example("route-request.json")).encode("utf-8")
    response = app.handle("POST", ROUTE_PATH, BEARER, body)
    # A refused answer is not the GPU going away: the job stays ready.
    assert job.state == READY
    return response


def test_the_contract_example_yields_exactly_the_contract_response() -> None:
    runtime = FakeRuntime(Answer(content=json.dumps(example("route-response.json"))))
    response = post(runtime)
    assert response.status == 200
    assert body_of(response) == example("route-response.json")


def test_the_model_is_asked_with_the_closed_list_as_its_schema() -> None:
    runtime = FakeRuntime(Answer(content=json.dumps(example("route-response.json"))))
    post(runtime)
    (call,) = runtime.calls
    assert call.stream is False
    assert [message.content for message in call.messages] == [
        "will I need an umbrella this afternoon"
    ]
    assert call.schema is not None
    properties = call.schema["properties"]
    assert properties["agent"]["enum"] == [
        "question",
        "weather",
        "time",
        "camera",
        "introduce",
        "echo",
        NO_AGENT,
    ]
    assert properties["mode"]["enum"] == ["", "forecast"]
    assert properties["parameters"]["properties"] == {
        "day": {"type": "string", "enum": ["today", "tomorrow"]}
    }
    assert "- weather: Says today's weather" in call.system
    assert "Modes: forecast." in call.system
    assert "day (string: today, tomorrow)" in call.system


def test_an_agent_outside_the_list_is_refused_and_never_returned() -> None:
    runtime = FakeRuntime(
        answer(agent="lights", mode="", confidence=0.99, parameters={})
    )
    response = post(runtime)
    assert response.status == 503
    assert body_of(response) == {
        "error": "route is not ready: the model named an agent outside the list"
    }
    assert "lights" not in json.dumps(body_of(response))


def test_an_answer_that_is_not_the_schema_is_refused() -> None:
    for content in ("not json", "[1]", '{"agent": "time", "mode": ""}'):
        response = post(FakeRuntime(Answer(content=content)))
        assert response.status == 503


def test_none_is_an_answer_with_no_mode_and_no_parameters() -> None:
    runtime = FakeRuntime(
        answer(agent=NO_AGENT, mode="forecast", confidence=0.4, parameters={"day": "x"})
    )
    assert body_of(post(runtime)) == {
        "agent": NO_AGENT,
        "mode": "",
        "confidence": 0.4,
        "parameters": {},
    }


def test_a_mode_or_parameter_that_is_not_the_agents_is_left_out() -> None:
    runtime = FakeRuntime(
        answer(
            agent="time",
            mode="forecast",
            confidence=1.7,
            parameters={"day": "today"},
        )
    )
    assert body_of(post(runtime)) == {
        "agent": "time",
        "mode": "",
        "confidence": 1.0,
        "parameters": {},
    }
    runtime = FakeRuntime(
        answer(
            agent="weather",
            mode="forecast",
            confidence=0.8,
            parameters={"day": "someday", "zip": "12345"},
        )
    )
    assert body_of(post(runtime)) == {
        "agent": "weather",
        "mode": "forecast",
        "confidence": 0.8,
        "parameters": {},
    }


def test_a_request_that_is_not_the_contract_is_400() -> None:
    response = post(FakeRuntime(), {"transcript": "hi"})
    assert response.status == 400
    assert body_of(response)["error"] == "RouteRequest is missing the field: agents"


def test_a_slot_two_agents_share_takes_either_ones_values() -> None:
    agents = (
        RouteAgent(
            name="a",
            description="A.",
            commands=("a",),
            modes=(),
            parameters=(Parameter(name="n", type="integer", values=(1, 2)),),
        ),
        RouteAgent(
            name="b",
            description="B.",
            commands=("b",),
            modes=("slow",),
            parameters=(
                Parameter(name="n", type="integer", values=(2, 3)),
                Parameter(name="free", type="string"),
            ),
        ),
    )
    schema = route_schema(agents)
    assert schema["properties"]["parameters"]["properties"] == {
        "n": {"type": "integer", "enum": [1, 2, 3]},
        "free": {"type": "string"},
    }
    assert describe_agents(agents).splitlines()[1] == (
        "- b: B. Commands: b. Modes: slow. Parameters: n (integer: 2, 3); free (string)."
    )
    assert RouteRequest(transcript="x", agents=agents).to_json()["transcript"] == "x"
