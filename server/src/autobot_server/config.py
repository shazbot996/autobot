"""The server's settings: the `[server]` table of `config/autobot.toml` (IS-3).

The one configuration file holds the server's settings beside the NUC's. The
server reads only that table, with `tomllib`, and never imports `autobot`, so
`GAMEON1` needs none of the NUC's dependencies (plan choice 2). The NUC's
reader, `autobot.config`, and this one are both tested against the shipped
file.

The server uses its hostname, the port of `url` (the one it binds), its
models and its runtime. The table's other keys are the NUC's: they are
known here, so that a misspelt key is still refused, but not read. Every key
has the same default as on the NUC.

`$AUTOBOT_CONFIG` names another file, as on the NUC; a relative path is
resolved against the checkout's root.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from urllib.parse import urlsplit

CONFIG_ENV_VAR = "AUTOBOT_CONFIG"
DEFAULT_CONFIG_PATH = "config/autobot.toml"

# The compute types faster-whisper may run the model in on Turing (IS-10).
STT_COMPUTE_TYPES = ("int8_float16", "float16")
# The runtimes the language model may be served by (IS-10).
RUNTIMES = ("ollama",)

# The keys of `[server]` the server reads, and the type each must have.
_STRINGS = (
    "url",
    "hostname",
    "stt_model",
    "stt_compute_type",
    "llm_model",
    "runtime",
    "runtime_url",
)
_COUNTS = ("llm_context",)
# The keys of `[server]` only the NUC reads (`autobot.config.ServerConfig`).
_NUC_KEYS = (
    "enabled",
    "stt_enabled",
    "token_file",
    "health_timeout",
    "stt_timeout_base",
    "stt_timeout_per_second",
    "route_timeout",
    "chat_first_token_timeout",
    "chat_timeout",
    "poll_up_seconds",
    "poll_down_min_seconds",
    "poll_down_max_seconds",
    "tier2_threshold",
    "tier3_threshold",
    "hint_chars",
    "tool_rounds",
    "core_listen_seconds",
    "embedding_model",
)
KNOWN_KEYS = frozenset((*_STRINGS, *_COUNTS, *_NUC_KEYS))


class ServerConfigError(Exception):
    """The configuration cannot be used by the server. The message is one line."""


@dataclass(frozen=True)
class ServerSettings:
    """What the server reads of `[server]`; the defaults are the NUC's."""

    # The NUC's address of the server, `http://host:port`; the server binds
    # the port on every address of its own (IS-2).
    url: str = "http://192.168.86.53:8300"
    # The hostname the server refuses to run anywhere but (IS-25).
    hostname: str = "GAMEON1"
    # The speech-to-text model's directory, relative to the checkout (IS-10).
    stt_model: str = "models/faster-whisper/large-v3-turbo"
    # One of `STT_COMPUTE_TYPES`.
    stt_compute_type: str = "int8_float16"
    # The language model, as the runtime names it; empty until
    # `make server-eval` has chosen one (IS-26).
    llm_model: str = ""
    # The language model's context, in tokens.
    llm_context: int = 8192
    # One of `RUNTIMES`, reached at `runtime_url` on loopback only (IS-10).
    runtime: str = "ollama"
    runtime_url: str = "http://127.0.0.1:11434"

    @property
    def port(self) -> int:
        """The port of `url`: the one the server binds."""
        return urlsplit(self.url).port or 0

    @property
    def stt_model_path(self) -> Path:
        """The absolute path of the speech-to-text model's directory."""
        return resolve_checkout_path(self.stt_model)

    @property
    def stt_model_name(self) -> str:
        """The speech-to-text model's name, as `/health` reports it."""
        return Path(self.stt_model).name


@cache
def checkout_root() -> Path:
    """The checkout's root: the nearest ancestor of this file holding `server/pyproject.toml`."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "server" / "pyproject.toml").is_file():
            return parent
    raise ServerConfigError(
        "cannot find the autobot checkout's root: run from a checkout set up "
        "with 'make server-setup'"
    )


def resolve_checkout_path(path: str | Path) -> Path:
    """Resolve a path against the checkout's root; absolute paths pass through."""
    candidate = Path(path).expanduser()
    return candidate if candidate.is_absolute() else checkout_root() / candidate


def config_path() -> Path:
    """The config file: `$AUTOBOT_CONFIG` when set, else `config/autobot.toml`."""
    return resolve_checkout_path(os.environ.get(CONFIG_ENV_VAR) or DEFAULT_CONFIG_PATH)


def load_settings(path: str | Path | None = None) -> ServerSettings:
    """The server's settings from `path`, or from `config_path()`."""
    source = config_path() if path is None else resolve_checkout_path(path)
    try:
        with source.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:
        raise ServerConfigError(f"config file not found: {source}") from None
    except OSError as error:
        raise ServerConfigError(
            f"cannot read config file {source}: {error.strerror}"
        ) from None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise ServerConfigError(
            f"config file {source} is not valid TOML: {error}"
        ) from None
    return parse_settings(data)


def parse_settings(data: Mapping[str, object]) -> ServerSettings:
    """The server's settings from a parsed configuration; only `[server]` is read."""
    table = data.get("server", {})
    if not isinstance(table, Mapping):
        raise ServerConfigError("[server] must be a table")
    unknown = sorted(set(table) - KNOWN_KEYS)
    if unknown:
        raise ServerConfigError(f"[server] has an unknown key: {unknown[0]}")
    defaults = ServerSettings()
    strings: dict[str, str] = {}
    for key in _STRINGS:
        value = table.get(key, getattr(defaults, key))
        if not isinstance(value, str) or (key != "llm_model" and not value.strip()):
            raise ServerConfigError(f"[server] {key} must be a non-empty string")
        strings[key] = value.strip()
    for key in ("url", "runtime_url"):
        if not _is_address(strings[key]):
            raise ServerConfigError(
                f"[server] {key} = {strings[key]!r} is not valid: "
                "an address looks like http://192.168.86.53:8300"
            )
    if strings["stt_compute_type"] not in STT_COMPUTE_TYPES:
        raise ServerConfigError(
            f"[server] stt_compute_type = {strings['stt_compute_type']!r} is not "
            f"one of: {', '.join(STT_COMPUTE_TYPES)}"
        )
    if strings["runtime"] not in RUNTIMES:
        raise ServerConfigError(
            f"[server] runtime = {strings['runtime']!r} is not one of: "
            f"{', '.join(RUNTIMES)}"
        )
    context = table.get("llm_context", defaults.llm_context)
    if not isinstance(context, int) or isinstance(context, bool) or context <= 0:
        raise ServerConfigError("[server] llm_context must be a whole number above 0")
    return ServerSettings(
        url=strings["url"],
        hostname=strings["hostname"],
        stt_model=strings["stt_model"],
        stt_compute_type=strings["stt_compute_type"],
        llm_model=strings["llm_model"],
        llm_context=context,
        runtime=strings["runtime"],
        runtime_url=strings["runtime_url"],
    )


def _is_address(text: str) -> bool:
    """Whether `text` is an `http://host:port` address, as the NUC requires."""
    parts = urlsplit(text)
    try:
        port = parts.port
    except ValueError:
        return False
    return bool(
        parts.scheme == "http"
        and parts.hostname
        and port
        and parts.path in ("", "/")
        and not (parts.query or parts.fragment or parts.username)
    )
