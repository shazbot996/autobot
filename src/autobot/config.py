"""Load and validate `config/autobot.toml`.

The file is the only place configuration lives. `load_config` turns it into
frozen dataclasses and raises `ConfigError` with a one-line message for
anything wrong with it; command-line entry points catch `ConfigError` and
print that line instead of a traceback.

`repo_root` and `resolve_repo_path` are how every module finds paths inside
the repository (`run/`, `models/`, the config file itself).
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path

CONFIG_ENV_VAR = "AUTOBOT_CONFIG"
DEFAULT_CONFIG_PATH = Path("config/autobot.toml")

# A wake phrase kind is the detector that implements the phrase.
KIND_TEXT_MATCH = "text_match"
KIND_OPENWAKEWORD = "openwakeword"
# The no-training baseline: sherpa-onnx open-vocabulary keyword spotting. It
# needs no `model`: its model is fetched into `models/sherpa-kws/`.
KIND_SHERPA_KWS = "sherpa_kws"
WAKE_PHRASE_KINDS = (KIND_TEXT_MATCH, KIND_OPENWAKEWORD, KIND_SHERPA_KWS)

# The colour themes of the terminal user interface, in the order `T` cycles them.
TUI_THEMES = ("light", "dark", "sun")

# An audio device: a substring of a device name, or a numeric index.
type DeviceSpec = str | int


class ConfigError(Exception):
    """The configuration is missing or invalid. The message is one line."""


@dataclass(frozen=True)
class HostConfig:
    # Hostname of the NUC; empty when unset.
    nuc_hostname: str = ""


@dataclass(frozen=True)
class AudioConfig:
    input_device: DeviceSpec
    output_device: DeviceSpec
    sample_rate: int = 16000
    frame_ms: int = 80

    @property
    def frame_samples(self) -> int:
        """Samples in one frame (1280 at 16 kHz and 80 ms)."""
        return self.sample_rate * self.frame_ms // 1000


@dataclass(frozen=True)
class WakePhraseConfig:
    phrase: str
    kind: str
    target: str
    # Model file path, relative to the repository root; `openwakeword` needs
    # it, `text_match` and `sherpa_kws` do not use it.
    model: str = ""
    threshold: float = 0.5

    @property
    def model_path(self) -> Path | None:
        """The absolute model path, or None when no model is configured."""
        return resolve_repo_path(self.model) if self.model else None


@dataclass(frozen=True)
class AckConfig:
    text: str = "Yes?"
    voice: str = "en_US-joe-medium"


@dataclass(frozen=True)
class TuiConfig:
    # One of `TUI_THEMES`.
    theme: str = "light"


@dataclass(frozen=True)
class Config:
    host: HostConfig
    audio: AudioConfig
    wake_phrases: tuple[WakePhraseConfig, ...]
    ack: AckConfig
    tui: TuiConfig = TuiConfig()
    # The file the configuration was loaded from; None when built from a mapping.
    path: Path | None = None


@cache
def repo_root() -> Path:
    """The repository root: the nearest ancestor of this file with a `pyproject.toml`."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    raise ConfigError(
        "cannot find the autobot repository root: run from a checkout set up with 'make setup'"
    )


def resolve_repo_path(path: str | Path) -> Path:
    """Resolve a path against the repository root; absolute paths pass through."""
    candidate = Path(path).expanduser()
    return candidate if candidate.is_absolute() else repo_root() / candidate


def config_path() -> Path:
    """The config file to load: `$AUTOBOT_CONFIG` when set, else `config/autobot.toml`.

    A relative path, from either source, is resolved against the repository root.
    """
    return resolve_repo_path(os.environ.get(CONFIG_ENV_VAR) or DEFAULT_CONFIG_PATH)


def load_config(path: str | Path | None = None) -> Config:
    """Load and validate the configuration from `path`, or from `config_path()`."""
    source = config_path() if path is None else resolve_repo_path(path)
    try:
        with source.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {source}") from None
    except OSError as error:
        raise ConfigError(
            f"cannot read config file {source}: {error.strerror}"
        ) from None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise ConfigError(f"config file {source} is not valid TOML: {error}") from None
    try:
        return parse_config(data, path=source)
    except ConfigError as error:
        raise ConfigError(f"config file {source}: {error}") from None


def parse_config(data: Mapping[str, object], path: Path | None = None) -> Config:
    """Validate parsed TOML and build the `Config`. Messages name the offending key."""
    _reject_unknown(data, "", ("host", "audio", "wake_phrases", "ack", "tui"))

    host_table = _table(data, "host", required=False)
    _reject_unknown(host_table, "host", ("nuc_hostname",))
    host = HostConfig(nuc_hostname=_str(host_table, "host", "nuc_hostname", default=""))

    audio_table = _table(data, "audio", required=True)
    _reject_unknown(
        audio_table,
        "audio",
        ("input_device", "output_device", "sample_rate", "frame_ms"),
    )
    audio = AudioConfig(
        input_device=_device(audio_table, "input_device"),
        output_device=_device(audio_table, "output_device"),
        sample_rate=_positive_int(audio_table, "audio", "sample_rate", default=16000),
        frame_ms=_positive_int(audio_table, "audio", "frame_ms", default=80),
    )
    if audio.sample_rate * audio.frame_ms % 1000:
        raise ConfigError(
            f"[audio] frame_ms = {audio.frame_ms} is not a whole number of samples "
            f"at sample_rate = {audio.sample_rate}"
        )

    wake_phrases = _wake_phrases(data)

    ack_table = _table(data, "ack", required=False)
    _reject_unknown(ack_table, "ack", ("text", "voice"))
    ack = AckConfig(
        text=_str(ack_table, "ack", "text", default=AckConfig.text, non_empty=True),
        voice=_str(ack_table, "ack", "voice", default=AckConfig.voice, non_empty=True),
    )

    tui_table = _table(data, "tui", required=False)
    _reject_unknown(tui_table, "tui", ("theme",))
    theme = _str(tui_table, "tui", "theme", default=TuiConfig.theme)
    if theme not in TUI_THEMES:
        raise ConfigError(
            f"[tui] theme = {theme!r} is not one of: {', '.join(TUI_THEMES)}"
        )

    return Config(
        host=host,
        audio=audio,
        wake_phrases=wake_phrases,
        ack=ack,
        tui=TuiConfig(theme=theme),
        path=path,
    )


def _wake_phrases(data: Mapping[str, object]) -> tuple[WakePhraseConfig, ...]:
    raw = data.get("wake_phrases")
    if not isinstance(raw, list) or not raw:
        raise ConfigError("at least one [[wake_phrases]] entry is required")
    entries: list[WakePhraseConfig] = []
    for number, item in enumerate(raw, start=1):
        where = f"wake_phrases #{number}"
        if not isinstance(item, dict):
            raise ConfigError(f"[[wake_phrases]] entry #{number} must be a table")
        _reject_unknown(item, where, ("phrase", "kind", "model", "threshold", "target"))
        kind = _str(item, where, "kind", non_empty=True)
        if kind not in WAKE_PHRASE_KINDS:
            raise ConfigError(
                f"[{where}] kind = {kind!r} is not one of: {', '.join(WAKE_PHRASE_KINDS)}"
            )
        model = _str(item, where, "model", default="")
        if kind == KIND_OPENWAKEWORD and not model:
            raise ConfigError(
                f"[{where}] kind = {kind!r} needs a model file path in 'model'"
            )
        threshold = item.get("threshold", 0.5)
        if isinstance(threshold, bool) or not isinstance(threshold, int | float):
            raise ConfigError(f"[{where}] threshold must be a number")
        if not 0.0 < threshold <= 1.0:
            raise ConfigError(
                f"[{where}] threshold = {threshold} must be above 0 and at most 1"
            )
        entries.append(
            WakePhraseConfig(
                phrase=_str(item, where, "phrase", non_empty=True),
                kind=kind,
                target=_str(item, where, "target", non_empty=True),
                model=model,
                threshold=float(threshold),
            )
        )
    phrases = [entry.phrase.lower() for entry in entries]
    for phrase in phrases:
        if phrases.count(phrase) > 1:
            raise ConfigError(
                f"[[wake_phrases]] phrase {phrase!r} is configured more than once"
            )
    return tuple(entries)


def _reject_unknown(
    table: Mapping[str, object], where: str, known: tuple[str, ...]
) -> None:
    unknown = sorted(set(table) - set(known))
    if unknown:
        prefix = f"[{where}] " if where else ""
        raise ConfigError(f"{prefix}unknown key: {unknown[0]}")


def _table(
    data: Mapping[str, object], name: str, *, required: bool
) -> Mapping[str, object]:
    value = data.get(name)
    if value is None:
        if required:
            raise ConfigError(f"missing table [{name}]")
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a table")
    return value


def _str(
    table: Mapping[str, object],
    where: str,
    key: str,
    *,
    default: str | None = None,
    non_empty: bool = False,
) -> str:
    value = table.get(key, default)
    if value is None:
        raise ConfigError(f"[{where}] missing key: {key}")
    if not isinstance(value, str):
        raise ConfigError(f"[{where}] {key} must be a string")
    if non_empty and not value.strip():
        raise ConfigError(f"[{where}] {key} must not be empty")
    return value


def _positive_int(
    table: Mapping[str, object], where: str, key: str, *, default: int
) -> int:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"[{where}] {key} must be a positive whole number")
    return value


def _device(table: Mapping[str, object], key: str) -> DeviceSpec:
    value = table.get(key)
    if value is None:
        raise ConfigError(f"[audio] missing key: {key}")
    if isinstance(value, bool) or not isinstance(value, str | int):
        raise ConfigError(
            f"[audio] {key} must be a device name (string) or index (whole number)"
        )
    if isinstance(value, str) and not value.strip():
        raise ConfigError(f"[audio] {key} must not be empty")
    if isinstance(value, int) and value < 0:
        raise ConfigError(f"[audio] {key} must not be a negative index")
    return value
