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
import re
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

# The agents a routing rule can hand a command to. The echo is the first.
AGENT_ECHO = "echo"
ROUTE_AGENTS = (AGENT_ECHO,)

# The Gemini API the `question` agent calls (GC-11): Vertex AI, as the service
# account, or the Gemini Developer API, with an API key kept as a secret.
GEMINI_VERTEX_AI = "aiplatform.googleapis.com"
GEMINI_DEVELOPER_API = "generativelanguage.googleapis.com"
GEMINI_APIS = (GEMINI_VERTEX_AI, GEMINI_DEVELOPER_API)

# What every service account's address ends in.
SERVICE_ACCOUNT_DOMAIN = ".iam.gserviceaccount.com"

# A Google Cloud project id: 6 to 30 lower-case letters, digits and hyphens,
# starting with a letter and not ending in a hyphen.
_PROJECT_ID = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]")
# A region, as `us-east5` or `northamerica-northeast1`.
_REGION = re.compile(r"[a-z]+(-[a-z]+)+[0-9]+")
# A model's id, as it stands in a request: `gemini-2.5-flash`.
_MODEL_ID = re.compile(r"[a-z0-9][a-z0-9._-]{1,62}")
_SERVICE_ACCOUNT = re.compile(
    r"[a-z0-9][a-z0-9-]*@[a-z0-9.-]+" + re.escape(SERVICE_ACCOUNT_DOMAIN)
)

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
class RouteConfig:
    """One routing rule: a command heard after the wake goes to `agent`."""

    # The word or words to find in what is said after the wake phrase.
    command: str
    # One of `ROUTE_AGENTS`.
    agent: str


# The rules when the file has no `[[routes]]`: "echo" runs the echo.
DEFAULT_ROUTES = (RouteConfig(command="echo", agent=AGENT_ECHO),)


@dataclass(frozen=True)
class QuotaConfig:
    """One intended cap on a billed API (GC-10)."""

    # The API the cap is on, as `weather.googleapis.com`.
    api: str
    # The quota id `gcloud beta quotas info list --service=<api>` prints. The
    # cap's period (a day, a minute) is part of the id.
    metric: str
    # The live limit must be at or below this.
    limit: int
    # For a quota that is counted per model (Vertex AI's are): the model the
    # cap is for, as `gemini-2.5-flash`. Empty for any other quota.
    base_model: str = ""


@dataclass(frozen=True)
class GcpConfig:
    """How autobot reaches Google Cloud: only as its own service account (GC-4)."""

    project: str = "schiele-autobot-dev"
    region: str = "us-east5"
    service_account: str = "autobot-agent@schiele-autobot-dev.iam.gserviceaccount.com"
    # The service account's key, relative to the repository root. Only the
    # path is configuration: the file is a credential and is git-ignored (GC-7).
    credentials_file: str = "secrets/service-account-autobot-agent.json"
    # One of `GEMINI_APIS`.
    gemini_api: str = GEMINI_VERTEX_AI
    # The Gemini model the question agent calls, as `gemini-2.5-flash`; empty
    # until one is chosen on the Gemini step. A cap on a quota that is counted
    # per model is for this model unless its entry names another.
    gemini_model: str = ""
    # The Cloud Storage buckets autobot is meant to use; none yet (GC-12).
    buckets: tuple[str, ...] = ()
    quotas: tuple[QuotaConfig, ...] = ()

    @property
    def credentials_path(self) -> Path:
        """The absolute path of the key file."""
        return resolve_repo_path(self.credentials_file)


@dataclass(frozen=True)
class Config:
    host: HostConfig
    audio: AudioConfig
    wake_phrases: tuple[WakePhraseConfig, ...]
    ack: AckConfig
    tui: TuiConfig = TuiConfig()
    # The routing rules, in the order they are tried.
    routes: tuple[RouteConfig, ...] = DEFAULT_ROUTES
    gcp: GcpConfig = GcpConfig()
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
    _reject_unknown(
        data, "", ("host", "audio", "wake_phrases", "ack", "tui", "routes", "gcp")
    )

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
        routes=_routes(data),
        gcp=_gcp(data),
        path=path,
    )


def project_complaint(text: str) -> str | None:
    """Why `text` is not a Google Cloud project id; None when it is one."""
    if _PROJECT_ID.fullmatch(text):
        return None
    return "a project id is 6 to 30 lower-case letters, digits and hyphens"


def region_complaint(text: str) -> str | None:
    """Why `text` is not a Google Cloud region; None when it is one."""
    if _REGION.fullmatch(text):
        return None
    return "a region looks like us-east5"


def model_complaint(text: str) -> str | None:
    """Why `text` is not a model's id; None when it could be one."""
    if _MODEL_ID.fullmatch(text):
        return None
    return "a model id looks like gemini-2.5-flash"


def _gcp(data: Mapping[str, object]) -> GcpConfig:
    """The `[gcp]` table and its `[[gcp.quotas]]` entries; the defaults when absent."""
    table = _table(data, "gcp", required=False)
    _reject_unknown(
        table,
        "gcp",
        (
            "project",
            "region",
            "service_account",
            "credentials_file",
            "gemini_api",
            "gemini_model",
            "buckets",
            "quotas",
        ),
    )
    project = _str(table, "gcp", "project", default=GcpConfig.project)
    complaint = project_complaint(project)
    if complaint:
        raise ConfigError(f"[gcp] project = {project!r} is not valid: {complaint}")
    region = _str(table, "gcp", "region", default=GcpConfig.region)
    complaint = region_complaint(region)
    if complaint:
        raise ConfigError(f"[gcp] region = {region!r} is not valid: {complaint}")
    account = _str(table, "gcp", "service_account", default=GcpConfig.service_account)
    if not _SERVICE_ACCOUNT.fullmatch(account):
        raise ConfigError(
            f"[gcp] service_account = {account!r} must be a service account's "
            f"address, ending in {SERVICE_ACCOUNT_DOMAIN}"
        )
    gemini_api = _str(table, "gcp", "gemini_api", default=GcpConfig.gemini_api)
    if gemini_api not in GEMINI_APIS:
        raise ConfigError(
            f"[gcp] gemini_api = {gemini_api!r} is not one of: {', '.join(GEMINI_APIS)}"
        )
    gemini_model = _str(table, "gcp", "gemini_model", default="").strip()
    complaint = model_complaint(gemini_model) if gemini_model else None
    if complaint:
        raise ConfigError(
            f"[gcp] gemini_model = {gemini_model!r} is not valid: {complaint}"
        )
    return GcpConfig(
        project=project,
        region=region,
        service_account=account,
        credentials_file=_str(
            table,
            "gcp",
            "credentials_file",
            default=GcpConfig.credentials_file,
            non_empty=True,
        ),
        gemini_api=gemini_api,
        gemini_model=gemini_model,
        buckets=_buckets(table),
        quotas=_quotas(table),
    )


def _buckets(table: Mapping[str, object]) -> tuple[str, ...]:
    raw = table.get("buckets", [])
    if not isinstance(raw, list) or not all(
        isinstance(name, str) and name.strip() for name in raw
    ):
        raise ConfigError("[gcp] buckets must be a list of bucket names")
    names = tuple(str(name).strip() for name in raw)
    for name in names:
        if names.count(name) > 1:
            raise ConfigError(f"[gcp] buckets lists {name!r} more than once")
    return names


def _quotas(table: Mapping[str, object]) -> tuple[QuotaConfig, ...]:
    raw = table.get("quotas", [])
    if not isinstance(raw, list):
        raise ConfigError("[[gcp.quotas]] must be one or more tables")
    entries: list[QuotaConfig] = []
    for number, item in enumerate(raw, start=1):
        where = f"gcp.quotas #{number}"
        if not isinstance(item, dict):
            raise ConfigError(f"[[gcp.quotas]] entry #{number} must be a table")
        _reject_unknown(item, where, ("api", "metric", "limit", "base_model"))
        if "limit" not in item:
            raise ConfigError(f"[{where}] missing key: limit")
        entry = QuotaConfig(
            api=_str(item, where, "api", non_empty=True).strip(),
            metric=_str(item, where, "metric", non_empty=True).strip(),
            limit=_positive_int(item, where, "limit", default=0),
            base_model=_str(item, where, "base_model", default="").strip(),
        )
        if any(
            (entry.api, entry.metric, entry.base_model)
            == (other.api, other.metric, other.base_model)
            for other in entries
        ):
            raise ConfigError(
                f"[[gcp.quotas]] {entry.api} {entry.metric} is configured more than once"
            )
        entries.append(entry)
    return tuple(entries)


def _routes(data: Mapping[str, object]) -> tuple[RouteConfig, ...]:
    """The `[[routes]]` entries; `DEFAULT_ROUTES` when the file has none."""
    raw = data.get("routes")
    if raw is None:
        return DEFAULT_ROUTES
    if not isinstance(raw, list) or not raw:
        raise ConfigError("[[routes]] must be one or more tables")
    entries: list[RouteConfig] = []
    for number, item in enumerate(raw, start=1):
        where = f"routes #{number}"
        if not isinstance(item, dict):
            raise ConfigError(f"[[routes]] entry #{number} must be a table")
        _reject_unknown(item, where, ("command", "agent"))
        agent = _str(item, where, "agent", non_empty=True)
        if agent not in ROUTE_AGENTS:
            raise ConfigError(
                f"[{where}] agent = {agent!r} is not one of: {', '.join(ROUTE_AGENTS)}"
            )
        entries.append(
            RouteConfig(
                command=_str(item, where, "command", non_empty=True).strip(),
                agent=agent,
            )
        )
    commands = [" ".join(entry.command.lower().split()) for entry in entries]
    for command in commands:
        if commands.count(command) > 1:
            raise ConfigError(
                f"[[routes]] command {command!r} is configured more than once"
            )
    return tuple(entries)


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
