"""`autobot.fetch_models`: what is fetched, and resuming. No test uses the network."""

from __future__ import annotations

import hashlib
import io
import tarfile
import urllib.error
import zipfile
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from autobot import fetch_models
from autobot.agents import weather_api
from autobot.config import ServerConfig
from autobot.fetch_models import Asset, FetchError
from autobot.listener.engines.sherpa_kws import SHERPA_KWS_FILES, SHERPA_KWS_MODEL


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


# --- the sherpa_kws model: one archive, checked, then five files of it --------


def sherpa_archive(path: Path, names: tuple[str, ...]) -> str:
    """Write a release-shaped archive holding `names`; return its sha256."""
    folder = SHERPA_KWS_MODEL
    with tarfile.open(path, "w:bz2") as bundle:
        for name in (*names, "README.md", "test_wavs/0.wav"):
            body = name.encode()
            member = tarfile.TarInfo(f"{folder}/{name}")
            member.size = len(body)
            bundle.addfile(member, io.BytesIO(body))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def serve_archive(
    monkeypatch: pytest.MonkeyPatch, source: Path, sha256: str
) -> list[str]:
    """Make `fetch` copy `source` instead of downloading; return the URLs asked for."""
    asked: list[str] = []

    def fetch(url: str, destination: Path, limit: int | None = None) -> bool:
        asked.append(url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        return True

    monkeypatch.setattr(fetch_models, "fetch", fetch)
    monkeypatch.setattr(fetch_models, "SHERPA_KWS_SHA256", sha256)
    return asked


def test_the_sherpa_kws_model_is_unpacked_to_the_files_the_engine_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "release.tar.bz2"
    sha256 = sherpa_archive(source, SHERPA_KWS_FILES)
    asked = serve_archive(monkeypatch, source, sha256)
    models = tmp_path / "models"
    assert fetch_models.fetch_sherpa_kws(models)
    assert asked == [fetch_models.SHERPA_KWS_URL]
    assert asked[0].startswith("https://github.com/k2-fsa/sherpa-onnx/releases/")
    # Only the engine's files are kept: no archive, no README, no test clips.
    kept = {path.name for path in (models / "sherpa-kws").iterdir()}
    assert kept == set(SHERPA_KWS_FILES)
    assert (models / "sherpa-kws" / "tokens.txt").read_bytes() == b"tokens.txt"
    # Already there: nothing is asked for again.
    assert not fetch_models.fetch_sherpa_kws(models)
    assert len(asked) == 1


def test_a_sherpa_kws_archive_that_fails_its_checksum_is_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "release.tar.bz2"
    sherpa_archive(source, SHERPA_KWS_FILES)
    serve_archive(monkeypatch, source, "0" * 64)
    models = tmp_path / "models"
    with pytest.raises(FetchError, match="does not match its published checksum"):
        fetch_models.fetch_sherpa_kws(models)
    assert list((models / "sherpa-kws").iterdir()) == []


def test_a_sherpa_kws_archive_without_a_model_file_is_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "release.tar.bz2"
    sha256 = sherpa_archive(source, SHERPA_KWS_FILES[:-1])
    serve_archive(monkeypatch, source, sha256)
    with pytest.raises(FetchError, match="does not hold joiner-"):
        fetch_models.fetch_sherpa_kws(tmp_path / "models")


# --- the zip-code centroids: one archive, three columns of its table ------------

# The table as the Bureau publishes it now, and as it did in earlier years.
GAZETTEER = (
    "GEOID|GEOIDFQ|ALAND|AWATER|ALAND_SQMI|AWATER_SQMI|INTPTLAT|INTPTLONG\n"
    "00601|860Z200US00601|166744424|795116|64.38|0.307|18.180621|-66.749931\n"
    "10001|860Z200US10001|1616221|0|0.624|0.|40.750649|-73.997298\n"
)
OLD_GAZETTEER = (
    "GEOID\tALAND\tAWATER\tALAND_SQMI\tAWATER_SQMI\tINTPTLAT\tINTPTLONG       \n"
    "10001\t1616221\t0\t0.624\t0.\t40.750649\t-73.997298      \n"
)


def serve_gazetteer(
    monkeypatch: pytest.MonkeyPatch, body: bytes, min_rows: int = 1
) -> list[str]:
    """Make `fetch` write `body` instead of downloading; return the URLs asked for."""
    asked: list[str] = []

    def fetch(url: str, destination: Path, limit: int | None = None) -> bool:
        asked.append(url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(body)
        return True

    monkeypatch.setattr(fetch_models, "fetch", fetch)
    monkeypatch.setattr(fetch_models, "GEO_MIN_ROWS", min_rows)
    return asked


def zipped(text: str, name: str = "2026_Gaz_zcta_national.txt") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(name, text)
    return buffer.getvalue()


def test_the_gazetteer_is_kept_as_the_table_the_weather_agent_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = serve_gazetteer(monkeypatch, zipped(GAZETTEER))
    models = tmp_path / "models"
    assert fetch_models.fetch_geo(models)
    assert asked == [fetch_models.GEO_URL]
    assert asked[0].startswith("https://www2.census.gov/geo/docs/maps-data/")
    # Only the table is kept: no archive, nothing half written.
    assert [path.name for path in (models / "geo").iterdir()] == ["zcta.tsv"]
    assert (models / "geo" / "zcta.tsv").read_text() == (
        "GEOID\tINTPTLAT\tINTPTLONG\n"
        "00601\t18.180621\t-66.749931\n"
        "10001\t40.750649\t-73.997298\n"
    )
    # It is where, and what, the weather agent's `locate` reads.
    assert weather_api.locate("10001", tmp_path) == (40.750649, -73.997298)
    # Already there: nothing is asked for again.
    assert not fetch_models.fetch_geo(models)
    assert len(asked) == 1


def test_the_gazetteer_is_read_by_its_column_names() -> None:
    assert fetch_models.geo_rows(OLD_GAZETTEER) == [
        ("10001", "40.750649", "-73.997298")
    ]
    assert fetch_models.geo_rows(GAZETTEER)[0] == ("00601", "18.180621", "-66.749931")
    with pytest.raises(ValueError):
        fetch_models.geo_rows("ZIP|LAT|LONG\n10001|40.7|-73.9\n")


def test_a_gazetteer_that_is_not_the_table_is_one_line_and_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    models = tmp_path / "models"
    for body in (b"<html>not an archive</html>", zipped("a page\nof words\n")):
        serve_gazetteer(monkeypatch, body)
        with pytest.raises(FetchError, match="cannot read the zip code table") as error:
            fetch_models.fetch_geo(models)
        assert "\n" not in str(error.value)
        assert list((models / "geo").iterdir()) == []
    serve_gazetteer(monkeypatch, zipped(GAZETTEER, "readme.md"))
    with pytest.raises(FetchError, match="does not hold a table"):
        fetch_models.fetch_geo(models)
    # A table of a few rows is not the national one.
    serve_gazetteer(monkeypatch, zipped(GAZETTEER), min_rows=30_000)
    with pytest.raises(FetchError, match="holds 2 zip codes, not the national table"):
        fetch_models.fetch_geo(models)
    assert not (models / "geo" / "zcta.tsv").exists()


def test_the_command_names_the_listeners_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Nothing is downloaded: every file is "already there" but the Joe voice.
    wanted: list[Path] = []

    def fake_fetch(url: str, destination: Path, limit: int | None = None) -> bool:
        wanted.append(destination)
        return destination.name == "en_US-joe-medium.onnx"

    monkeypatch.setattr(fetch_models, "fetch", fake_fetch)
    monkeypatch.setattr(fetch_models, "fetch_sherpa_kws", lambda models: False)
    monkeypatch.setattr(fetch_models, "fetch_geo", lambda models: True)
    monkeypatch.setattr(fetch_models, "ack_voice", lambda: "en_US-joe-medium")
    monkeypatch.setattr(fetch_models, "resolve_repo_path", lambda path: tmp_path)
    assert fetch_models.main([]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "  fetched  models/piper/en_US-joe-medium.onnx" in lines
    assert "  have     models/piper/en_US-joe-medium.onnx.json" in lines
    assert "  have     models/silero/silero_vad.onnx" in lines
    assert "  have     models/sherpa-kws/ (5 files: the sherpa_kws baseline)" in lines
    assert "  have     models/embedding/all-MiniLM-L6-v2/model_int8.onnx" in lines
    assert lines[-1].endswith("(1 fetched now, 10 already there)")
    assert (
        "  fetched  models/geo/zcta.tsv (the zip-code centroids: the weather agent)"
        in lines
    )
    assert len(lines) == len(wanted) + 3


def test_the_embedding_model_is_fetched_beside_its_tokenizer() -> None:
    assets = fetch_models.embedding_assets()
    assert [str(asset.path) for asset in assets] == [
        "embedding/all-MiniLM-L6-v2/model_int8.onnx",
        "embedding/all-MiniLM-L6-v2/tokenizer.json",
    ]
    # Where `[server] embedding_model` looks for it, by default.
    assert ServerConfig().embedding_model == f"models/{assets[0].path}"
    assert all(asset.url.startswith("https://huggingface.co/") for asset in assets)
    assert all(asset.limit is None for asset in assets)
