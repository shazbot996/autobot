"""`autobot.embedding`: the model and its tokenizer from a local path, and nothing else (IS-17).

The model here is the tiny stand-in of `tests/fixtures/embedding/`, with
the inputs and the output of `all-MiniLM-L6-v2`: no test needs the real
model, and none reaches the network.
"""

from __future__ import annotations

import shutil
import socket
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from autobot.config import repo_root
from autobot.embedding import (
    TOKENIZER_FILE,
    EmbeddingError,
    LocalEmbedder,
    similarity,
    unit,
)

FIXTURE = repo_root() / "tests" / "fixtures" / "embedding"
MODEL_FILE = "model_int8.onnx"


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every new socket is refused: a lookup of any kind fails the test."""

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("the embedding model reached for the network")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


@pytest.fixture
def local_model(tmp_path: Path) -> Path:
    """The model and its tokenizer, copied to a temporary local directory."""
    directory = tmp_path / "models" / "embedding" / "tiny"
    directory.mkdir(parents=True)
    for name in (MODEL_FILE, TOKENIZER_FILE):
        shutil.copy(FIXTURE / name, directory / name)
    return directory / MODEL_FILE


def test_the_model_and_tokenizer_load_from_a_local_path_with_the_network_blocked(
    local_model: Path, no_network: None
) -> None:
    embedder = LocalEmbedder(local_model)
    embedder.load()
    vectors = embedder.embed(["what is it like outside", "weather", "the time"])
    assert vectors.shape[0] == 3 and vectors.dtype == np.float32
    # Each row has length 1, so a dot product is a cosine similarity.
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-5)


def test_texts_that_share_words_are_near_and_others_are_not(
    local_model: Path, no_network: None
) -> None:
    embedder = LocalEmbedder(local_model)
    vectors = embedder.embed(
        [
            "What's it like outside?",
            "what is it like outside",
            "weather",
            "show me the front door",
        ]
    )
    scores = similarity(vectors[0], vectors[1:])
    assert scores[0] > 0.6
    assert scores[1] == pytest.approx(0.0, abs=1e-6)
    assert scores[0] > scores[2]
    assert similarity(vectors[1], vectors[1:2])[0] == pytest.approx(1.0, abs=1e-5)


def test_padding_does_not_change_a_short_texts_vector(
    local_model: Path, no_network: None
) -> None:
    embedder = LocalEmbedder(local_model)
    alone = embedder.embed(["weather"])[0]
    fresh = LocalEmbedder(local_model)
    padded = fresh.embed(["will it rain today or tomorrow outside", "weather"])[1]
    assert np.allclose(alone, padded, atol=1e-6)


def test_a_text_asked_for_again_is_not_run_again(
    local_model: Path, no_network: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    embedder = LocalEmbedder(local_model)
    first = embedder.embed(["weather", "the time", "weather"])
    assert np.array_equal(first[0], first[2])
    runs: list[list[str]] = []
    real = embedder._run

    def counted(texts: Any) -> Any:
        runs.append(list(texts))
        return real(texts)

    monkeypatch.setattr(embedder, "_run", counted)
    again = embedder.embed(["  the   time ", "forecast", "weather"])
    assert runs == [["forecast"]]
    assert np.array_equal(again[0], first[1]) and np.array_equal(again[2], first[0])


@pytest.mark.parametrize("missing", [MODEL_FILE, TOKENIZER_FILE])
def test_a_missing_file_is_named_and_nothing_is_downloaded(
    local_model: Path, no_network: None, missing: str
) -> None:
    (local_model.parent / missing).unlink()
    embedder = LocalEmbedder(local_model)
    with pytest.raises(EmbeddingError) as refused:
        embedder.embed(["weather"])
    message = str(refused.value)
    assert message.startswith(
        f"embedding model file is missing: {local_model.parent / missing}"
    )
    assert "'make fetch-models' fetches them" in message


def test_a_file_that_is_not_a_model_is_one_line(
    local_model: Path, no_network: None
) -> None:
    local_model.write_bytes(b"not a model")
    with pytest.raises(EmbeddingError) as refused:
        LocalEmbedder(local_model).load()
    assert str(refused.value).startswith("cannot load the embedding model")
    assert "\n" not in str(refused.value)


def test_unit_leaves_a_zero_row_zero() -> None:
    rows = unit(np.array([[3.0, 4.0], [0.0, 0.0]]))
    assert rows.tolist() == [[pytest.approx(0.6), pytest.approx(0.8)], [0.0, 0.0]]
