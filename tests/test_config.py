"""`autobot.config`: loading, validation, and path resolution."""

from pathlib import Path
from typing import Any

import pytest

from autobot.config import (
    CONFIG_ENV_VAR,
    Config,
    ConfigError,
    config_path,
    load_config,
    parse_config,
    repo_root,
    resolve_repo_path,
)

VALID_TOML = """
[host]
nuc_hostname = "nuc"

[audio]
input_device = "pipewire"
output_device = 3
sample_rate = 16000
frame_ms = 80

[[wake_phrases]]
phrase = "autobot"
kind = "text_match"
model = ""
threshold = 0.5
target = "ack"

[ack]
text = "Yes?"
voice = "en_US-lessac-medium"
"""


def valid_data() -> dict[str, Any]:
    return {
        "host": {"nuc_hostname": "nuc"},
        "audio": {"input_device": "pipewire", "output_device": "pipewire"},
        "wake_phrases": [{"phrase": "autobot", "kind": "text_match", "target": "ack"}],
        "ack": {"text": "Yes?", "voice": "en_US-lessac-medium"},
    }


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "autobot.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_repo_root_holds_the_project_definition() -> None:
    assert (repo_root() / "pyproject.toml").is_file()
    assert (repo_root() / "src" / "autobot").is_dir()


def test_resolve_repo_path(tmp_path: Path) -> None:
    assert (
        resolve_repo_path("run/listener.sock") == repo_root() / "run" / "listener.sock"
    )
    assert resolve_repo_path(tmp_path) == tmp_path


def test_config_path_default_and_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)
    assert config_path() == repo_root() / "config" / "autobot.toml"
    monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "other.toml"))
    assert config_path() == tmp_path / "other.toml"
    monkeypatch.setenv(CONFIG_ENV_VAR, "config/other.toml")
    assert config_path() == repo_root() / "config" / "other.toml"


def test_the_committed_config_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)
    config = load_config()
    assert config.path == repo_root() / "config" / "autobot.toml"
    assert config.audio.sample_rate == 16000
    assert config.audio.frame_ms == 80
    assert [phrase.phrase for phrase in config.wake_phrases] == ["autobot"]
    assert config.wake_phrases[0].target == "ack"
    assert config.ack.text == "Yes?"


def test_load_config_reads_every_field(tmp_path: Path) -> None:
    config = load_config(write(tmp_path, VALID_TOML))
    assert config.host.nuc_hostname == "nuc"
    assert config.audio.input_device == "pipewire"
    assert config.audio.output_device == 3
    assert config.audio.frame_samples == 1280
    phrase = config.wake_phrases[0]
    assert (
        phrase.phrase,
        phrase.kind,
        phrase.model,
        phrase.threshold,
        phrase.target,
    ) == (
        "autobot",
        "text_match",
        "",
        0.5,
        "ack",
    )
    assert phrase.model_path is None
    assert config.ack.voice == "en_US-lessac-medium"


def test_load_config_honours_the_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = write(tmp_path, VALID_TOML.replace('"nuc"', '"elsewhere"'))
    monkeypatch.setenv(CONFIG_ENV_VAR, str(path))
    assert load_config().host.nuc_hostname == "elsewhere"


def test_missing_file_is_a_one_line_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="config file not found") as raised:
        load_config(tmp_path / "absent.toml")
    assert "\n" not in str(raised.value)


def test_malformed_toml_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="is not valid TOML"):
        load_config(write(tmp_path, "[audio\ninput_device = "))


def test_validation_errors_name_the_file(tmp_path: Path) -> None:
    path = write(tmp_path, VALID_TOML.replace("sample_rate = 16000", "sample_rate = 0"))
    with pytest.raises(ConfigError, match="sample_rate") as raised:
        load_config(path)
    assert str(path) in str(raised.value)


def test_defaults_apply_to_optional_keys() -> None:
    data = valid_data()
    del data["host"]
    del data["ack"]
    config = parse_config(data)
    assert config.host.nuc_hostname == ""
    assert config.audio.sample_rate == 16000
    assert config.audio.frame_ms == 80
    assert config.wake_phrases[0].threshold == 0.5
    assert config.ack.text == "Yes?"
    assert config.path is None


def test_config_is_frozen() -> None:
    config = parse_config(valid_data())
    assert isinstance(config, Config)
    with pytest.raises(AttributeError):
        setattr(config.audio, "sample_rate", 8000)  # noqa: B010


def test_openwakeword_model_path_resolves_against_the_repo_root() -> None:
    data = valid_data()
    data["wake_phrases"][0].update(
        kind="openwakeword", model="models/wakeword/autobot.onnx"
    )
    phrase = parse_config(data).wake_phrases[0]
    assert phrase.model_path == repo_root() / "models" / "wakeword" / "autobot.onnx"


def set_key(table: str, key: str, value: object) -> dict[str, Any]:
    data = valid_data()
    target = data["wake_phrases"][0] if table == "wake_phrases" else data[table]
    target[key] = value
    return data


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({}, r"missing table \[audio\]"),
        ({**valid_data(), "wake_phrases": []}, "at least one"),
        ({**valid_data(), "extra": {}}, "unknown key: extra"),
        (set_key("audio", "input_device", ""), "input_device must not be empty"),
        (set_key("audio", "input_device", 1.5), "input_device must be a device name"),
        (
            set_key("audio", "output_device", True),
            "output_device must be a device name",
        ),
        (set_key("audio", "output_device", -1), "negative index"),
        (
            set_key("audio", "sample_rate", "16000"),
            "sample_rate must be a positive whole",
        ),
        (set_key("audio", "frame_ms", 0), "frame_ms must be a positive whole"),
        (set_key("audio", "sample_rate", 1001), "not a whole number of samples"),
        (set_key("audio", "channels", 2), r"\[audio\] unknown key: channels"),
        (set_key("host", "nuc_hostname", 7), "nuc_hostname must be a string"),
        (set_key("wake_phrases", "phrase", " "), "phrase must not be empty"),
        (
            set_key("wake_phrases", "kind", "magic"),
            "is not one of: text_match, openwakeword",
        ),
        (set_key("wake_phrases", "kind", "openwakeword"), "needs a model file path"),
        (set_key("wake_phrases", "threshold", 0), "threshold = 0 must be above 0"),
        (set_key("wake_phrases", "threshold", 1.5), "threshold = 1.5 must be above 0"),
        (set_key("wake_phrases", "threshold", "high"), "threshold must be a number"),
        (set_key("wake_phrases", "target", ""), "target must not be empty"),
        (set_key("ack", "text", ""), "text must not be empty"),
        (set_key("ack", "voice", 3), "voice must be a string"),
    ],
)
def test_invalid_config_is_rejected(data: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError, match=message) as raised:
        parse_config(data)
    assert "\n" not in str(raised.value)


def test_missing_required_wake_phrase_key() -> None:
    data = valid_data()
    del data["wake_phrases"][0]["target"]
    with pytest.raises(ConfigError, match="missing key: target"):
        parse_config(data)


def test_duplicate_wake_phrases_are_rejected() -> None:
    data = valid_data()
    data["wake_phrases"].append(
        {"phrase": "Autobot", "kind": "text_match", "target": "ack"}
    )
    with pytest.raises(ConfigError, match="configured more than once"):
        parse_config(data)
