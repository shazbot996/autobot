"""`autobot.agents.gemini`: the service account first, text only, one-line failures.

No test here reaches Google Cloud or runs the ADK: `connect` is a fake and the
one function that touches the ADK is replaced.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from agents.agent_fakes import FakeModel
from autobot.agents import gemini
from autobot.agents.gemini import (
    MESSAGE_LIMIT,
    NO_ANSWER,
    NO_MODEL,
    AdkModel,
    Model,
    ModelError,
)
from autobot.config import GEMINI_DEVELOPER_API, Config, parse_config
from autobot.gcp.client import GcpError

MODEL = "gemini-2.5-flash"
PROMPT = "Answer in one sentence."
QUERY = "how far is the moon"
ANSWER = "About 384,400 km."
ENV_VARS = (
    gemini.USE_VERTEX_AI_ENV_VAR,
    gemini.PROJECT_ENV_VAR,
    gemini.LOCATION_ENV_VAR,
)


def config(**gcp: Any) -> Config:
    return parse_config(
        {
            "audio": {"input_device": "pipewire", "output_device": "pipewire"},
            "wake_phrases": [
                {"phrase": "autobot", "kind": "text_match", "target": "ack"}
            ],
            "gcp": gcp,
        }
    )


class FakeConnect:
    """Stands in for `gcp.client.connect`: records each call, raises `error`."""

    def __init__(self, error: GcpError | None = None) -> None:
        self.error = error
        self.calls: list[tuple[Config, Path]] = []

    def __call__(self, config: Config, repo: Path) -> object:
        self.calls.append((config, repo))
        if self.error is not None:
            raise self.error
        return object()


class FakeAdk:
    """Stands in for the function that touches the ADK."""

    def __init__(self, answer: str = ANSWER, error: Exception | None = None) -> None:
        self.answer = answer
        self.error = error
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, model: str, prompt: str, query: str) -> str:
        self.calls.append((model, prompt, query))
        if self.error is not None:
            raise self.error
        return self.answer


@pytest.fixture
def adk(monkeypatch: pytest.MonkeyPatch) -> FakeAdk:
    """The ADK replaced, and the environment as it was before `[gcp]` is set."""
    fake = FakeAdk()
    monkeypatch.setattr(gemini, "_ask_adk", fake)
    for name in ENV_VARS:
        # Set and removed through monkeypatch, so what `ask` sets is undone.
        monkeypatch.setenv(name, "before")
        monkeypatch.delenv(name)
    return fake


def takes_model(model: Model) -> Model:
    return model


def test_the_fake_model_and_the_adk_model_are_models(tmp_path: Path) -> None:
    # Checked by mypy: both satisfy the protocol the agents are written against.
    assert takes_model(FakeModel()).ask(PROMPT, QUERY)
    assert takes_model(AdkModel(config(), tmp_path, FakeConnect()))


def test_importing_the_module_loads_no_adk_and_no_speech_library() -> None:
    absent = (
        "google.adk",
        "google.genai",
        "onnxruntime",
        "faster_whisper",
        "piper",
        "sherpa_onnx",
    )
    code = (
        "import sys\n"
        "import autobot.agents.gemini\n"
        f"print(','.join(name for name in {absent!r} if name in sys.modules))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def test_no_chosen_model_is_refused_before_anything_else(
    tmp_path: Path, adk: FakeAdk
) -> None:
    connect = FakeConnect()
    with pytest.raises(ModelError) as raised:
        AdkModel(config(), tmp_path, connect).ask(PROMPT, QUERY)
    assert str(raised.value) == NO_MODEL
    assert connect.calls == []
    assert adk.calls == []


def test_the_developer_api_is_refused_before_connecting(
    tmp_path: Path, adk: FakeAdk
) -> None:
    connect = FakeConnect()
    chosen = config(gemini_model=MODEL, gemini_api=GEMINI_DEVELOPER_API)
    with pytest.raises(ModelError, match="only through aiplatform.googleapis.com"):
        AdkModel(chosen, tmp_path, connect).ask(PROMPT, QUERY)
    assert connect.calls == []
    assert adk.calls == []


def test_a_refused_connection_is_one_line_and_the_adk_is_never_reached(
    tmp_path: Path, adk: FakeAdk
) -> None:
    connect = FakeConnect(GcpError("refused: the key file\n is missing"))
    with pytest.raises(ModelError) as raised:
        AdkModel(config(gemini_model=MODEL), tmp_path, connect).ask(PROMPT, QUERY)
    assert str(raised.value) == "refused: the key file is missing"
    assert len(connect.calls) == 1
    assert adk.calls == []
    # A refused call does not point the ADK anywhere either.
    assert all(name not in os.environ for name in ENV_VARS)


def test_an_answer_comes_back_and_the_environment_is_set_from_gcp(
    tmp_path: Path, adk: FakeAdk
) -> None:
    connect = FakeConnect()
    chosen = config(gemini_model=MODEL, project="some-project", region="europe-west4")
    assert AdkModel(chosen, tmp_path, connect).ask(PROMPT, QUERY) == ANSWER
    assert connect.calls == [(chosen, tmp_path)]
    # Only the model's id and the two texts go to the ADK.
    assert adk.calls == [(MODEL, PROMPT, QUERY)]
    assert os.environ[gemini.USE_VERTEX_AI_ENV_VAR] == "TRUE"
    assert os.environ[gemini.PROJECT_ENV_VAR] == "some-project"
    assert os.environ[gemini.LOCATION_ENV_VAR] == "europe-west4"


def test_a_failure_of_the_adk_is_one_short_line(tmp_path: Path, adk: FakeAdk) -> None:
    adk.error = RuntimeError(
        "429 RESOURCE_EXHAUSTED\n\n  quota exceeded\n" + "x " * 300
    )
    with pytest.raises(ModelError) as raised:
        AdkModel(config(gemini_model=MODEL), tmp_path, FakeConnect()).ask(PROMPT, QUERY)
    message = str(raised.value)
    assert "\n" not in message
    assert message.startswith(
        "Gemini failed: RuntimeError: 429 RESOURCE_EXHAUSTED quota exceeded x"
    )
    assert len(message) <= MESSAGE_LIMIT


def test_a_missing_adk_is_a_model_error(tmp_path: Path, adk: FakeAdk) -> None:
    adk.error = ModuleNotFoundError("No module named 'google.adk'")
    with pytest.raises(ModelError, match="No module named 'google.adk'"):
        AdkModel(config(gemini_model=MODEL), tmp_path, FakeConnect()).ask(PROMPT, QUERY)


def test_a_timeout_is_a_model_error(tmp_path: Path, adk: FakeAdk) -> None:
    adk.error = TimeoutError()
    with pytest.raises(ModelError, match="did not answer in 60 seconds"):
        AdkModel(config(gemini_model=MODEL), tmp_path, FakeConnect()).ask(PROMPT, QUERY)


def test_an_empty_answer_is_a_model_error(tmp_path: Path, adk: FakeAdk) -> None:
    adk.answer = " \n"
    with pytest.raises(ModelError) as raised:
        AdkModel(config(gemini_model=MODEL), tmp_path, FakeConnect()).ask(PROMPT, QUERY)
    assert str(raised.value) == NO_ANSWER
