"""`autobot.fetch_models`: what is fetched, and resuming. No test uses the network."""

from __future__ import annotations

import io
import urllib.error
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from autobot import fetch_models
from autobot.fetch_models import Asset, FetchError


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, status: int):
        super().__init__(body)
        self.status = status
        self.headers = {"Content-Length": str(len(body))}


class FakeServer:
    """Stands in for `_open`: serves one file, honouring Range, and records calls."""

    def __init__(self, content: bytes, honour_range: bool = True):
        self.content = content
        self.honour_range = honour_range
        self.calls: list[tuple[int, int | None]] = []

    def __call__(self, url: str, first_byte: int, limit: int | None) -> Any:
        self.calls.append((first_byte, limit))
        if not self.honour_range:
            return FakeResponse(self.content, 200)
        if first_byte >= len(self.content):
            raise urllib.error.HTTPError(url, 416, "no such range", Message(), None)
        partial = bool(first_byte) or limit is not None
        return FakeResponse(self.content[first_byte:limit], 206 if partial else 200)


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> FakeServer:
    fake = FakeServer(bytes(range(256)) * 40)
    monkeypatch.setattr(fetch_models, "_open", fake)
    return fake


def test_a_piper_voice_url_follows_the_voice_name() -> None:
    assert fetch_models.piper_voice_url("en_GB-alan-medium") == (
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
        "en/en_GB/alan/medium/en_GB-alan-medium.onnx"
    )
    with pytest.raises(FetchError, match="<language>-<name>-<quality>"):
        fetch_models.piper_voice_url("lessac")


def test_the_runtime_assets_are_what_the_listener_loads() -> None:
    paths = {str(asset.path) for asset in fetch_models.runtime_assets("en_US-amy-low")}
    assert paths == {
        "openwakeword/melspectrogram.onnx",
        "openwakeword/embedding_model.onnx",
        "silero/silero_vad.onnx",
        "faster-whisper/tiny.en/config.json",
        "faster-whisper/tiny.en/model.bin",
        "faster-whisper/tiny.en/tokenizer.json",
        "faster-whisper/tiny.en/vocabulary.txt",
        "piper/en_US-amy-low.onnx",
        "piper/en_US-amy-low.onnx.json",
    }


def test_the_trained_model_is_never_fetched() -> None:
    assets = [*fetch_models.runtime_assets("x-y-z"), *fetch_models.training_assets(3)]
    assert not [asset for asset in assets if "wakeword/autobot" in str(asset.path)]


def test_the_training_assets_hold_every_voice_and_the_feature_files() -> None:
    assets = fetch_models.training_assets(3.0)
    voices = [a for a in assets if str(a.path).startswith("piper/")]
    assert len(voices) == 2 * 27
    limited = [a for a in assets if a.limit is not None]
    assert [str(a.path) for a in limited] == [
        "wakeword/data/features/acav100m_head.npy.raw"
    ]
    assert limited[0].limit == fetch_models.acav_bytes(3.0)


def test_the_negative_features_are_cut_at_a_whole_row() -> None:
    size = fetch_models.acav_bytes(3.0)
    assert size == 2_999_998_592
    assert (size - 128) % (16 * 96 * 2) == 0


def test_fetch_writes_the_file_and_leaves_no_part(
    tmp_path: Path, server: FakeServer
) -> None:
    destination = tmp_path / "sub" / "model.onnx"
    assert fetch_models.fetch_once("https://example.invalid/m", destination)
    assert destination.read_bytes() == server.content
    assert not destination.with_name("model.onnx.part").exists()


def test_a_file_that_is_there_is_not_fetched_again(
    tmp_path: Path, server: FakeServer
) -> None:
    destination = tmp_path / "model.onnx"
    destination.write_bytes(b"already here")
    assert not fetch_models.fetch_once("https://example.invalid/m", destination)
    assert server.calls == []
    assert destination.read_bytes() == b"already here"


def test_a_partial_file_is_resumed_from_where_it_stopped(
    tmp_path: Path, server: FakeServer
) -> None:
    destination = tmp_path / "model.onnx"
    destination.with_name("model.onnx.part").write_bytes(server.content[:1000])
    assert fetch_models.fetch_once("https://example.invalid/m", destination)
    assert server.calls == [(1000, None)]
    assert destination.read_bytes() == server.content


def test_a_server_that_ignores_the_range_starts_the_file_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeServer(b"whole file", honour_range=False)
    monkeypatch.setattr(fetch_models, "_open", fake)
    destination = tmp_path / "model.onnx"
    destination.with_name("model.onnx.part").write_bytes(b"stale")
    assert fetch_models.fetch_once("https://example.invalid/m", destination)
    assert destination.read_bytes() == b"whole file"


def test_a_complete_partial_file_is_just_renamed(
    tmp_path: Path, server: FakeServer
) -> None:
    destination = tmp_path / "model.onnx"
    destination.with_name("model.onnx.part").write_bytes(server.content)
    assert fetch_models.fetch_once("https://example.invalid/m", destination)
    assert destination.read_bytes() == server.content


def test_a_limit_fetches_only_the_head_of_the_file(
    tmp_path: Path, server: FakeServer
) -> None:
    destination = tmp_path / "head.raw"
    assert fetch_models.fetch_once("https://example.invalid/big", destination, 3000)
    assert destination.read_bytes() == server.content[:3000]
    assert server.calls == [(0, 3000)]


def test_a_larger_limit_extends_the_file_that_is_there(
    tmp_path: Path, server: FakeServer
) -> None:
    destination = tmp_path / "head.raw"
    destination.write_bytes(server.content[:3000])
    assert fetch_models.fetch_once("https://example.invalid/big", destination, 5000)
    assert server.calls == [(3000, 5000)]
    assert destination.read_bytes() == server.content[:5000]
    # A smaller limit than what is there fetches nothing and keeps the file.
    assert not fetch_models.fetch_once("https://example.invalid/big", destination, 100)
    assert len(destination.read_bytes()) == 5000


def test_a_missing_file_upstream_is_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def gone(url: str, first_byte: int, limit: int | None) -> Any:
        raise urllib.error.HTTPError(url, 404, "not found", Message(), None)

    monkeypatch.setattr(fetch_models, "_open", gone)
    with pytest.raises(FetchError) as error:
        fetch_models.fetch("https://example.invalid/m", tmp_path / "m")
    assert str(error.value) == "cannot fetch https://example.invalid/m: HTTP 404"


def test_an_asset_is_a_url_and_a_path_under_models() -> None:
    asset = Asset("https://example.invalid/a", Path("silero") / "a.onnx")
    assert asset.limit is None
    assert not asset.path.is_absolute()
