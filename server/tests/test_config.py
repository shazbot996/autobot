"""`autobot_server.config`: the server reads its settings from the `[server]` table (IS-3)."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from autobot_server.config import (
    CONFIG_ENV_VAR,
    KNOWN_KEYS,
    ServerConfigError,
    ServerSettings,
    checkout_root,
    load_settings,
    parse_settings,
)

SHIPPED = Path("config/autobot.toml")


def test_the_checkout_root_holds_the_server_and_the_config() -> None:
    root = checkout_root()
    assert (root / "server" / "pyproject.toml").is_file()
    assert (root / SHIPPED).is_file()


def test_the_shipped_file_gives_the_servers_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)
    settings = load_settings()
    assert settings.hostname == "GAMEON1"
    assert settings.url == "http://192.168.86.53:8300"
    assert settings.port == 8300
    assert settings.stt_model == "models/faster-whisper/large-v3-turbo"
    assert settings.stt_model_name == "large-v3-turbo"
    assert settings.stt_model_path == (
        checkout_root() / "models" / "faster-whisper" / "large-v3-turbo"
    )
    assert settings.stt_compute_type == "int8_float16"
    assert settings.llm_context == 8192
    assert (settings.runtime, settings.runtime_url) == (
        "ollama",
        "http://127.0.0.1:11434",
    )


def test_every_key_of_the_shipped_table_is_known() -> None:
    with (checkout_root() / SHIPPED).open("rb") as handle:
        table = tomllib.load(handle)["server"]
    assert set(table) == KNOWN_KEYS


def test_a_missing_table_gives_the_defaults() -> None:
    assert parse_settings({}) == ServerSettings()


def test_another_file_is_named_by_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "other.toml"
    path.write_text('[server]\nhostname = "elsewhere"\n', encoding="utf-8")
    monkeypatch.setenv(CONFIG_ENV_VAR, str(path))
    assert load_settings().hostname == "elsewhere"


def test_an_unknown_key_is_refused() -> None:
    with pytest.raises(ServerConfigError, match="unknown key: hostnme"):
        parse_settings({"server": {"hostnme": "GAMEON1"}})


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("url", "gameon1.lan:8300", "url = 'gameon1.lan:8300' is not valid"),
        ("runtime_url", "http://127.0.0.1", "runtime_url = 'http://127.0.0.1'"),
        ("stt_compute_type", "bfloat16", "stt_compute_type = 'bfloat16' is not one of"),
        ("runtime", "vllm", "runtime = 'vllm' is not one of"),
        ("hostname", "", "hostname must be a non-empty string"),
        ("llm_context", 0, "llm_context must be a whole number above 0"),
        ("llm_context", True, "llm_context must be a whole number above 0"),
    ],
)
def test_a_bad_value_is_refused_by_name(key: str, value: object, message: str) -> None:
    with pytest.raises(ServerConfigError, match=re.escape(message)):
        parse_settings({"server": {key: value}})


def test_an_empty_language_model_is_allowed_until_one_is_chosen() -> None:
    assert parse_settings({"server": {"llm_model": ""}}).llm_model == ""


def test_a_missing_file_is_one_line(tmp_path: Path) -> None:
    with pytest.raises(ServerConfigError, match="config file not found"):
        load_settings(tmp_path / "absent.toml")


def test_the_server_never_imports_autobot() -> None:
    package = checkout_root() / "server" / "src" / "autobot_server"
    importing = re.compile(r"^\s*(from|import)\s+autobot(\.|\s|$)", re.MULTILINE)
    for module in package.rglob("*.py"):
        assert not importing.search(module.read_text(encoding="utf-8")), module.name
