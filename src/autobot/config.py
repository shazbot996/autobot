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
from urllib.parse import urlsplit

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

# The agents a routing rule can hand a command to, each defined by one
# `[[agents]]` table (AG-1). The echo is the first.
AGENT_ECHO = "echo"
AGENT_QUESTION = "question"
AGENT_WEATHER = "weather"
AGENT_TIME = "time"
AGENT_CAMERA = "camera"
AGENT_INTRODUCE = "introduce"
# The conversational agent: the default when no route is admitted (IS-19).
AGENT_CORE = "core"
ROUTE_AGENTS = (
    AGENT_ECHO,
    AGENT_QUESTION,
    AGENT_WEATHER,
    AGENT_TIME,
    AGENT_CAMERA,
    AGENT_INTRODUCE,
    AGENT_CORE,
)
# The mode the `forecast` rule hands the weather agent (AG-7).
MODE_FORECAST = "forecast"

# The model behind an agent (IS-18): none (code only), the inference server's
# language model, or Gemini as the service account.
MODEL_NONE = "none"
MODEL_LOCAL = "local"
MODEL_GEMINI = "gemini"
AGENT_MODELS = (MODEL_NONE, MODEL_LOCAL, MODEL_GEMINI)

# The type of an agent's parameter, or of a tool's (IS-18, IS-21).
PARAMETER_TYPES = ("string", "integer", "number", "boolean")
# A parameter's or a tool's name: lower-case letters, digits and underscores.
_SLOT_NAME = re.compile(r"[a-z_][a-z0-9_]*")

# The server's speech-to-text compute types that Turing has (IS-10).
STT_COMPUTE_TYPES = ("int8_float16", "float16")
# The language model runtimes the server can reach (IS-10).
SERVER_RUNTIMES = ("ollama",)

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
# The location that serves a model from Google's whole network, not one region.
GLOBAL_LOCATION = "global"
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
    # A word the agent receives with the command, as a parameter (AG-7):
    # `forecast` on a weather rule. Empty when the rule carries none.
    mode: str = ""
    # Phrases that mean the same as the command, for the semantic match of
    # the cascade's tier 2 (IS-17). Empty when the rule has none.
    examples: tuple[str, ...] = ()


# The rules when the file has no `[[routes]]`, in the order of the tree:
# one per agent, and the aliases `forecast` and `cameras` (AG-7).
DEFAULT_ROUTES = (
    RouteConfig(command="question", agent=AGENT_QUESTION),
    RouteConfig(command="weather", agent=AGENT_WEATHER),
    RouteConfig(command="forecast", agent=AGENT_WEATHER, mode=MODE_FORECAST),
    RouteConfig(command="time", agent=AGENT_TIME),
    RouteConfig(command="camera", agent=AGENT_CAMERA),
    RouteConfig(command="cameras", agent=AGENT_CAMERA),
    RouteConfig(command="introduce", agent=AGENT_INTRODUCE),
    RouteConfig(command="echo", agent=AGENT_ECHO),
)

# The question each confirmation asks when the definition gives none (AG-13).
DEFAULT_ENTRY_QUESTION = "Are you sure you want to start this?"
DEFAULT_REQUEST_QUESTION = (
    "This may cost a few dollars. Are you sure you want to run this?"
)
# The weather agent's API key, as a secret in Secret Manager (GC-9).
DEFAULT_WEATHER_SECRET = "autobot-weather-api-key"

# The settings an agent owns, beyond the keys every definition has. A key
# of one agent is refused on another, by name.
AGENT_SETTINGS: Mapping[str, tuple[str, ...]] = {
    AGENT_WEATHER: ("zip_code", "daily_limit", "minute_limit", "secret"),
    AGENT_CAMERA: ("folder", "commands"),
}
# The keys of every `[[agents]]` table.
_AGENT_KEYS = (
    "name",
    "description",
    "prompt",
    "confirm_entry",
    "confirm_entry_question",
    "confirm_request",
    "confirm_request_question",
    "listening",
    "model",
    "fallback",
    "parameters",
    "tools",
)
# A zip code: five digits.
_ZIP_CODE = re.compile(r"[0-9]{5}")
# A Secret Manager secret's id.
_SECRET_ID = re.compile(r"[A-Za-z0-9_-]{1,255}")

# An allowed value of a parameter, of the parameter's type.
type ParameterValue = str | int | float | bool


@dataclass(frozen=True)
class ParameterConfig:
    """One named slot of an agent or a tool: `[[agents.parameters]]` (IS-18)."""

    name: str
    # One of `PARAMETER_TYPES`.
    type: str = "string"
    # The values it may take; empty when any value of its type will do.
    values: tuple[ParameterValue, ...] = ()
    required: bool = False


@dataclass(frozen=True)
class ToolConfig:
    """One tool an agent's model may call, run on the NUC: `[[agents.tools]]` (IS-21)."""

    name: str
    description: str = ""
    parameters: tuple[ParameterConfig, ...] = ()


@dataclass(frozen=True)
class AgentConfig:
    """One agent's definition: one `[[agents]]` table (AG-1).

    The definition is data the owner edits; the agent's code is not in it.
    The settings an agent owns (`AGENT_SETTINGS`) keep their defaults on
    every other agent, where they mean nothing.
    """

    # One of `ROUTE_AGENTS`.
    name: str
    # What the agent is for, in a sentence or two.
    description: str = ""
    # The prompt wrapped around the owner's words, for an agent that calls a
    # language model; the written script of the `introduce` agent.
    prompt: str = ""
    # The confirmation after the command matches, before the agent starts.
    confirm_entry: bool = False
    confirm_entry_question: str = DEFAULT_ENTRY_QUESTION
    # The confirmation before the agent sends a request off the machine.
    confirm_request: bool = False
    confirm_request_question: str = DEFAULT_REQUEST_QUESTION
    # Whether the face listens while the agent's running view is shown (AG-15).
    listening: bool = True
    # The model behind the agent, one of `AGENT_MODELS` (IS-18).
    model: str = MODEL_NONE
    # What the agent falls back to, in order, when its model cannot answer:
    # entries of `AGENT_MODELS` and, last only, a line the agent says.
    fallback: tuple[str, ...] = ()
    # The named slots a route fills for the agent (IS-18).
    parameters: tuple[ParameterConfig, ...] = ()
    # The tools its model may call; the NUC runs them (IS-21).
    tools: tuple[ToolConfig, ...] = ()
    # weather: the location, five digits; empty until the owner sets it.
    zip_code: str = ""
    # weather: the agent's own cap on its lookups (GC-10 is the backstop).
    daily_limit: int = 100
    minute_limit: int = 10
    # weather: the Secret Manager secret holding the API key. Only the name is
    # configuration; the key is never in this file.
    secret: str = DEFAULT_WEATHER_SECRET
    # camera: the folder, relative to the repository root, the commands run in.
    folder: str = "assistant"
    # camera: the local commands, one a line, started in order.
    commands: str = ""


# The question agent's prompt: it asks for the two parts of AG-4 in the shape
# the agent's code takes apart.
QUESTION_PROMPT = """\
You are autobot, a voice assistant on the owner's computer at home. The owner
asks you a question out loud, and you answer it in two parts.
The summary is read aloud: one to three short sentences in plain words, easy
to hear, with no lists, symbols or markup.
The detail is shown on a small text screen: a few short lines of plain text,
at most forty characters each, with the facts the summary leaves out.
Reply with one JSON object and nothing else, in this shape:
{"summary": "...", "detail": ["...", "..."]}
"""

# The introduce agent's script, read aloud as it stands.
INTRODUCE_SCRIPT = """\
Hi, I'm autobot, a voice assistant that lives on this computer.
Say my name, then a command, and I hand it to one of my agents.
I listen and speak right here, so your voice never leaves the house.
When you ask me a question, only the words I heard go out to a language model.
Say cancel at any time to go back a step.
"""

# The core agent's personality: its system prompt (IS-19). It asks for the
# two parts of AG-4, as the question agent's prompt does.
CORE_PROMPT = """\
You are autobot, a friendly voice assistant on the owner's computer at home.
You talk with the owner out loud, one turn at a time, and remember what was
said earlier in the conversation.
Keep each reply short and plain: one to three sentences that are easy to
hear, with no lists, symbols or markup.
When a question needs more knowledge or reasoning than you have, hand it to
one of the agents you are given, with the question that agent should get.
"""
# What the core agent says when neither the server nor Gemini can answer.
CORE_AWAY_LINE = "the server is away"

# The camera agent's commands: the proxy server, then the browser.
CAMERA_COMMANDS = """\
make fourway-server
make fourway
"""

# The definitions used for any agent the file does not define.
DEFAULT_AGENTS = (
    AgentConfig(
        name=AGENT_ECHO,
        description="Records five seconds of your voice and plays it back.",
    ),
    AgentConfig(
        name=AGENT_QUESTION,
        description=(
            "Asks a language model your question and answers with a spoken "
            "summary and the details on the screen."
        ),
        prompt=QUESTION_PROMPT,
        model=MODEL_GEMINI,
    ),
    AgentConfig(
        name=AGENT_WEATHER,
        description=(
            "Says today's weather for your zip code; the forecast command "
            "gives the days ahead."
        ),
    ),
    AgentConfig(
        name=AGENT_TIME,
        description="Says the time from this computer's clock.",
    ),
    AgentConfig(
        name=AGENT_CAMERA,
        description=(
            "Opens the camera application, with the four outside cameras, "
            "in Firefox on the NUC."
        ),
        commands=CAMERA_COMMANDS,
    ),
    AgentConfig(
        name=AGENT_INTRODUCE,
        description="autobot introduces himself and says how he works.",
        prompt=INTRODUCE_SCRIPT,
    ),
    AgentConfig(
        name=AGENT_CORE,
        description=(
            "autobot himself: talks with you when no command matches, on the "
            "inference server's language model."
        ),
        prompt=CORE_PROMPT,
        listening=True,
        model=MODEL_LOCAL,
        fallback=(MODEL_GEMINI, CORE_AWAY_LINE),
    ),
)


@dataclass(frozen=True)
class WordsConfig:
    """The shared word lists of the `[words]` table (AG-13, AG-14).

    A word stands in one list only.
    """

    # The words that answer a confirmation yes.
    yes: tuple[str, ...] = (
        "yes",
        "yeah",
        "yep",
        "yup",
        "sure",
        "okay",
        "ok",
        "affirmative",
        "go ahead",
        "you bet",
        "you betcha",
        "do it",
        "absolutely",
        "please do",
    )
    # The words that answer it no.
    no: tuple[str, ...] = (
        "no",
        "nope",
        "nah",
        "no way",
        "negative",
        "stop",
        "don't",
        "never mind",
    )
    # The global action keywords, checked before the rules.
    actions: tuple[str, ...] = ("cancel",)


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
    # Where Vertex AI serves `gemini_model`: `global`, or a region such as
    # `us-east5`. The newer Gemini models are served only at `global`, and a
    # model asked for in a region that does not serve it answers 404. Quotas
    # are still compared in `region`.
    gemini_location: str = GLOBAL_LOCATION
    # The Cloud Storage buckets autobot is meant to use; none yet (GC-12).
    buckets: tuple[str, ...] = ()
    quotas: tuple[QuotaConfig, ...] = ()

    @property
    def credentials_path(self) -> Path:
        """The absolute path of the key file."""
        return resolve_repo_path(self.credentials_file)


@dataclass(frozen=True)
class ServerConfig:
    """The GPU inference server on `GAMEON1`, and how the NUC reaches it (IS-3).

    The NUC reads the address, the token file, the timeouts, the poll
    intervals and the thresholds; the server reads its hostname and its
    models from the same table.
    """

    # Whether the NUC looks for the server at all.
    enabled: bool = True
    # Whether what is said inside a session is sent to the server to be
    # transcribed (IS-8). Off until `CONSTRAINTS.md` `CN-1` is amended to
    # allow speech-to-text on the owner's other machine in the house: until
    # then every transcript is the NUC's own.
    stt_enabled: bool = False
    # The server's base URL, `http://host:port`; the port is what it binds.
    url: str = "http://192.168.86.53:8300"
    # The shared bearer token's file on the NUC, relative to the repository
    # root. Only the path is configuration: the file is a secret (IS-13).
    token_file: str = "secrets/server-token"
    # The hostname the server refuses to run anywhere but (IS-25).
    hostname: str = "GAMEON1"
    # The timeouts of each call, in seconds (IS-4).
    health_timeout: float = 1.0
    stt_timeout_base: float = 0.5
    stt_timeout_per_second: float = 0.25
    route_timeout: float = 1.5
    chat_first_token_timeout: float = 3.0
    chat_timeout: float = 30.0
    # How often `/health` is polled, in seconds: while up, and the back-off
    # from the least to the most while down (IS-23).
    poll_up_seconds: float = 5.0
    poll_down_min_seconds: float = 5.0
    poll_down_max_seconds: float = 60.0
    # The confidence each routing tier must reach to be admitted (IS-17).
    tier2_threshold: float = 0.6
    tier3_threshold: float = 0.7
    # The most characters of hints sent with a transcription (IS-16).
    hint_chars: int = 500
    # The most rounds of tool calls in one turn (IS-21).
    tool_rounds: int = 3
    # How long, in seconds, the core agent waits for the next turn (IS-19).
    core_listen_seconds: float = 10.0
    # The server's speech-to-text model, relative to its checkout (IS-10).
    stt_model: str = "models/faster-whisper/large-v3-turbo"
    # One of `STT_COMPUTE_TYPES`.
    stt_compute_type: str = "int8_float16"
    # The language model, as the runtime names it; empty until
    # `make server-eval` has chosen one (IS-26).
    llm_model: str = ""
    # The language model's context, in tokens.
    llm_context: int = 8192
    # One of `SERVER_RUNTIMES`, reached at `runtime_url` on the server only.
    runtime: str = "ollama"
    runtime_url: str = "http://127.0.0.1:11434"
    # The NUC's tier 2 embedding model: the int8 ONNX file of
    # all-MiniLM-L6-v2, relative to the repository root, with its
    # `tokenizer.json` beside it (IS-17).
    embedding_model: str = "models/embedding/all-MiniLM-L6-v2/model_int8.onnx"

    @property
    def token_path(self) -> Path:
        """The absolute path of the token file."""
        return resolve_repo_path(self.token_file)

    @property
    def embedding_path(self) -> Path:
        """The absolute path of the embedding model."""
        return resolve_repo_path(self.embedding_model)

    @property
    def host(self) -> str:
        """The host part of `url`."""
        return urlsplit(self.url).hostname or ""

    @property
    def port(self) -> int:
        """The port of `url`: the one the server binds."""
        return urlsplit(self.url).port or 0


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
    # The agents' definitions: the file's, in its order, then the default of
    # every agent it does not define.
    agents: tuple[AgentConfig, ...] = DEFAULT_AGENTS
    words: WordsConfig = WordsConfig()
    server: ServerConfig = ServerConfig()
    # The file the configuration was loaded from; None when built from a mapping.
    path: Path | None = None

    def agent(self, name: str) -> AgentConfig:
        """The definition of the agent `name`; KeyError when there is none."""
        for definition in self.agents:
            if definition.name == name:
                return definition
        raise KeyError(name)


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
        data,
        "",
        (
            "host",
            "audio",
            "wake_phrases",
            "ack",
            "tui",
            "routes",
            "gcp",
            "agents",
            "words",
            "server",
        ),
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
        agents=_agents(data),
        words=_words(data),
        server=_server(data),
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


def location_complaint(text: str) -> str | None:
    """Why `text` is not a Vertex AI location (`global` or a region); None when it is one."""
    if text == GLOBAL_LOCATION or _REGION.fullmatch(text):
        return None
    return "a location is global, or a region such as us-east5"


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
            "gemini_location",
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
    gemini_location = _str(
        table, "gcp", "gemini_location", default=GcpConfig.gemini_location
    ).strip()
    complaint = location_complaint(gemini_location)
    if complaint:
        raise ConfigError(
            f"[gcp] gemini_location = {gemini_location!r} is not valid: {complaint}"
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
        gemini_location=gemini_location,
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
        _reject_unknown(item, where, ("command", "agent", "mode", "examples"))
        agent = _str(item, where, "agent", non_empty=True)
        if agent not in ROUTE_AGENTS:
            raise ConfigError(
                f"[{where}] agent = {agent!r} is not one of: {', '.join(ROUTE_AGENTS)}"
            )
        entries.append(
            RouteConfig(
                command=_str(item, where, "command", non_empty=True).strip(),
                agent=agent,
                mode=_str(item, where, "mode", default="").strip(),
                examples=_examples(item, where),
            )
        )
    commands = [" ".join(entry.command.lower().split()) for entry in entries]
    for command in commands:
        if commands.count(command) > 1:
            raise ConfigError(
                f"[[routes]] command {command!r} is configured more than once"
            )
    return tuple(entries)


def _examples(item: Mapping[str, object], where: str) -> tuple[str, ...]:
    """A rule's `examples`: phrases, tidied to single spaces; none when absent."""
    raw = item.get("examples", [])
    if not isinstance(raw, list) or not all(
        isinstance(phrase, str) and phrase.strip() for phrase in raw
    ):
        raise ConfigError(f"[{where}] examples must be a list of phrases")
    phrases = tuple(" ".join(str(phrase).split()) for phrase in raw)
    seen: set[str] = set()
    for phrase in phrases:
        if phrase.lower() in seen:
            raise ConfigError(f"[{where}] examples lists {phrase!r} more than once")
        seen.add(phrase.lower())
    return phrases


def _agents(data: Mapping[str, object]) -> tuple[AgentConfig, ...]:
    """The `[[agents]]` entries, then the default of every agent not among them."""
    raw = data.get("agents", [])
    if not isinstance(raw, list):
        raise ConfigError("[[agents]] must be one or more tables")
    defaults = {definition.name: definition for definition in DEFAULT_AGENTS}
    owned = {key for keys in AGENT_SETTINGS.values() for key in keys}
    entries: list[AgentConfig] = []
    for number, item in enumerate(raw, start=1):
        where = f"agents #{number}"
        if not isinstance(item, dict):
            raise ConfigError(f"[[agents]] entry #{number} must be a table")
        name = _str(item, where, "name", non_empty=True).strip()
        if name not in ROUTE_AGENTS:
            raise ConfigError(
                f"[{where}] name = {name!r} is not one of: {', '.join(ROUTE_AGENTS)}"
            )
        if any(entry.name == name for entry in entries):
            raise ConfigError(f"[[agents]] agent {name!r} is defined more than once")
        foreign = sorted(set(item) & owned - set(AGENT_SETTINGS.get(name, ())))
        if foreign:
            raise ConfigError(
                f"[{where}] {foreign[0]} is not a setting of the {name} agent"
            )
        _reject_unknown(item, where, (*_AGENT_KEYS, *owned))
        default = defaults[name]
        zip_code = _str(item, where, "zip_code", default=default.zip_code).strip()
        if zip_code and not _ZIP_CODE.fullmatch(zip_code):
            raise ConfigError(
                f"[{where}] zip_code = {zip_code!r} is not valid: a zip code is five digits"
            )
        secret = _str(item, where, "secret", default=default.secret).strip()
        if not _SECRET_ID.fullmatch(secret):
            raise ConfigError(
                f"[{where}] secret = {secret!r} is not valid: a secret's name is "
                "letters, digits, hyphens and underscores"
            )
        model = _str(item, where, "model", default=default.model).strip()
        if model not in AGENT_MODELS:
            raise ConfigError(
                f"[{where}] model = {model!r} is not one of: {', '.join(AGENT_MODELS)}"
            )
        entries.append(
            AgentConfig(
                name=name,
                description=_str(
                    item, where, "description", default=default.description
                ).strip(),
                prompt=_str(item, where, "prompt", default=default.prompt),
                confirm_entry=_bool(
                    item, where, "confirm_entry", default=default.confirm_entry
                ),
                confirm_entry_question=_str(
                    item,
                    where,
                    "confirm_entry_question",
                    default=default.confirm_entry_question,
                    non_empty=True,
                ).strip(),
                confirm_request=_bool(
                    item, where, "confirm_request", default=default.confirm_request
                ),
                confirm_request_question=_str(
                    item,
                    where,
                    "confirm_request_question",
                    default=default.confirm_request_question,
                    non_empty=True,
                ).strip(),
                listening=_bool(item, where, "listening", default=default.listening),
                model=model,
                fallback=_fallback(item, where, default.fallback),
                parameters=(
                    _parameters(item["parameters"], where, "agents.parameters")
                    if "parameters" in item
                    else default.parameters
                ),
                tools=_tools(item, where, default.tools),
                zip_code=zip_code,
                daily_limit=_positive_int(
                    item, where, "daily_limit", default=default.daily_limit
                ),
                minute_limit=_positive_int(
                    item, where, "minute_limit", default=default.minute_limit
                ),
                secret=secret,
                folder=_str(
                    item, where, "folder", default=default.folder, non_empty=True
                ).strip(),
                commands=_str(item, where, "commands", default=default.commands),
            )
        )
    named = {entry.name for entry in entries}
    return (
        *entries,
        *(definition for definition in DEFAULT_AGENTS if definition.name not in named),
    )


def _fallback(
    item: Mapping[str, object], where: str, default: tuple[str, ...]
) -> tuple[str, ...]:
    """An agent's `fallback`: models, and a spoken line last only (IS-18)."""
    raw = item.get("fallback", list(default))
    if not isinstance(raw, list) or not all(
        isinstance(entry, str) and entry.strip() for entry in raw
    ):
        raise ConfigError(
            f"[{where}] fallback must be a list of models and, last, a spoken line"
        )
    entries = tuple(" ".join(str(entry).split()) for entry in raw)
    for position, entry in enumerate(entries[:-1], start=1):
        if entry not in AGENT_MODELS:
            raise ConfigError(
                f"[{where}] fallback entry #{position} {entry!r} is not one of: "
                f"{', '.join(AGENT_MODELS)}; a spoken line can only be last"
            )
    return entries


def _parameters(raw: object, where: str, table: str) -> tuple[ParameterConfig, ...]:
    """The `[[<table>]]` slots of an agent or a tool (IS-18)."""
    if not isinstance(raw, list):
        raise ConfigError(f"[{where}] [[{table}]] must be one or more tables")
    entries: list[ParameterConfig] = []
    for number, slot in enumerate(raw, start=1):
        at = f"{where} {table} #{number}"
        if not isinstance(slot, dict):
            raise ConfigError(f"[{where}] [[{table}]] entry #{number} must be a table")
        _reject_unknown(slot, at, ("name", "type", "values", "required"))
        name = _str(slot, at, "name", non_empty=True).strip()
        if not _SLOT_NAME.fullmatch(name):
            raise ConfigError(
                f"[{at}] name = {name!r} is not valid: a name is lower-case "
                "letters, digits and underscores"
            )
        if any(entry.name == name for entry in entries):
            raise ConfigError(
                f"[{where}] [[{table}]] {name!r} is defined more than once"
            )
        kind = _str(slot, at, "type", default=ParameterConfig.type).strip()
        if kind not in PARAMETER_TYPES:
            raise ConfigError(
                f"[{at}] type = {kind!r} is not one of: {', '.join(PARAMETER_TYPES)}"
            )
        values = slot.get("values", [])
        if not isinstance(values, list) or not all(
            _of_type(value, kind) for value in values
        ):
            raise ConfigError(f"[{at}] values must be a list of {kind} values")
        entries.append(
            ParameterConfig(
                name=name,
                type=kind,
                values=tuple(values),
                required=_bool(slot, at, "required", default=False),
            )
        )
    return tuple(entries)


def _of_type(value: object, kind: str) -> bool:
    """Whether `value` is a value of the parameter type `kind`."""
    if kind == "boolean":
        return isinstance(value, bool)
    if isinstance(value, bool):
        return False
    if kind == "integer":
        return isinstance(value, int)
    if kind == "number":
        return isinstance(value, int | float)
    return isinstance(value, str) and bool(value.strip())


def _tools(
    item: Mapping[str, object], where: str, default: tuple[ToolConfig, ...]
) -> tuple[ToolConfig, ...]:
    """An agent's `[[agents.tools]]` (IS-21); its default when absent."""
    if "tools" not in item:
        return default
    raw = item["tools"]
    if not isinstance(raw, list):
        raise ConfigError(f"[{where}] [[agents.tools]] must be one or more tables")
    entries: list[ToolConfig] = []
    for number, tool in enumerate(raw, start=1):
        at = f"{where} tools #{number}"
        if not isinstance(tool, dict):
            raise ConfigError(
                f"[{where}] [[agents.tools]] entry #{number} must be a table"
            )
        _reject_unknown(tool, at, ("name", "description", "parameters"))
        name = _str(tool, at, "name", non_empty=True).strip()
        if not _SLOT_NAME.fullmatch(name):
            raise ConfigError(
                f"[{at}] name = {name!r} is not valid: a name is lower-case "
                "letters, digits and underscores"
            )
        if any(entry.name == name for entry in entries):
            raise ConfigError(
                f"[{where}] [[agents.tools]] {name!r} is defined more than once"
            )
        entries.append(
            ToolConfig(
                name=name,
                description=_str(tool, at, "description", default="").strip(),
                parameters=_parameters(
                    tool.get("parameters", []), at, "agents.tools.parameters"
                ),
            )
        )
    return tuple(entries)


# The `[server]` keys that are a number of seconds above zero.
_SERVER_SECONDS = (
    "health_timeout",
    "stt_timeout_base",
    "stt_timeout_per_second",
    "route_timeout",
    "chat_first_token_timeout",
    "chat_timeout",
    "poll_up_seconds",
    "poll_down_min_seconds",
    "poll_down_max_seconds",
    "core_listen_seconds",
)
# The `[server]` keys that are a confidence, above 0 and at most 1.
_SERVER_THRESHOLDS = ("tier2_threshold", "tier3_threshold")
# The `[server]` keys that are a positive whole number.
_SERVER_COUNTS = ("hint_chars", "tool_rounds", "llm_context")
# The `[server]` keys that are a string.
_SERVER_STRINGS = (
    "url",
    "token_file",
    "hostname",
    "stt_model",
    "stt_compute_type",
    "llm_model",
    "runtime",
    "runtime_url",
    "embedding_model",
)


def _server(data: Mapping[str, object]) -> ServerConfig:
    """The `[server]` table (IS-3); every key its default when absent."""
    table = _table(data, "server", required=False)
    _reject_unknown(
        table,
        "server",
        (
            "enabled",
            "stt_enabled",
            *_SERVER_STRINGS,
            *_SERVER_SECONDS,
            *_SERVER_THRESHOLDS,
            *_SERVER_COUNTS,
        ),
    )
    defaults = ServerConfig()
    strings = {
        key: _str(
            table,
            "server",
            key,
            default=getattr(defaults, key),
            non_empty=key != "llm_model",
        ).strip()
        for key in _SERVER_STRINGS
    }
    for key in ("url", "runtime_url"):
        complaint = url_complaint(strings[key])
        if complaint:
            raise ConfigError(
                f"[server] {key} = {strings[key]!r} is not valid: {complaint}"
            )
    if strings["stt_compute_type"] not in STT_COMPUTE_TYPES:
        raise ConfigError(
            f"[server] stt_compute_type = {strings['stt_compute_type']!r} is not "
            f"one of: {', '.join(STT_COMPUTE_TYPES)}"
        )
    if strings["runtime"] not in SERVER_RUNTIMES:
        raise ConfigError(
            f"[server] runtime = {strings['runtime']!r} is not one of: "
            f"{', '.join(SERVER_RUNTIMES)}"
        )
    seconds = {
        key: _positive_number(table, "server", key, default=getattr(defaults, key))
        for key in _SERVER_SECONDS
    }
    if seconds["poll_down_max_seconds"] < seconds["poll_down_min_seconds"]:
        raise ConfigError(
            "[server] poll_down_max_seconds must not be below poll_down_min_seconds"
        )
    thresholds: dict[str, float] = {}
    for key in _SERVER_THRESHOLDS:
        value = _positive_number(table, "server", key, default=getattr(defaults, key))
        if value > 1.0:
            raise ConfigError(f"[server] {key} = {value} must be above 0 and at most 1")
        thresholds[key] = value
    counts = {
        key: _positive_int(table, "server", key, default=getattr(defaults, key))
        for key in _SERVER_COUNTS
    }
    return ServerConfig(
        enabled=_bool(table, "server", "enabled", default=defaults.enabled),
        stt_enabled=_bool(table, "server", "stt_enabled", default=defaults.stt_enabled),
        url=strings["url"],
        token_file=strings["token_file"],
        hostname=strings["hostname"],
        health_timeout=seconds["health_timeout"],
        stt_timeout_base=seconds["stt_timeout_base"],
        stt_timeout_per_second=seconds["stt_timeout_per_second"],
        route_timeout=seconds["route_timeout"],
        chat_first_token_timeout=seconds["chat_first_token_timeout"],
        chat_timeout=seconds["chat_timeout"],
        poll_up_seconds=seconds["poll_up_seconds"],
        poll_down_min_seconds=seconds["poll_down_min_seconds"],
        poll_down_max_seconds=seconds["poll_down_max_seconds"],
        tier2_threshold=thresholds["tier2_threshold"],
        tier3_threshold=thresholds["tier3_threshold"],
        hint_chars=counts["hint_chars"],
        tool_rounds=counts["tool_rounds"],
        core_listen_seconds=seconds["core_listen_seconds"],
        stt_model=strings["stt_model"],
        stt_compute_type=strings["stt_compute_type"],
        llm_model=strings["llm_model"],
        llm_context=counts["llm_context"],
        runtime=strings["runtime"],
        runtime_url=strings["runtime_url"],
        embedding_model=strings["embedding_model"],
    )


def url_complaint(text: str) -> str | None:
    """Why `text` is not an `http://host:port` address; None when it is one."""
    parts = urlsplit(text)
    try:
        port = parts.port
    except ValueError:
        port = None
    if (
        parts.scheme == "http"
        and parts.hostname
        and port
        and parts.path in ("", "/")
        and not (parts.query or parts.fragment or parts.username)
    ):
        return None
    return "an address looks like http://192.168.86.53:8300"


def _words(data: Mapping[str, object]) -> WordsConfig:
    """The `[words]` table; each list its default when absent."""
    table = _table(data, "words", required=False)
    keys = ("yes", "no", "actions")
    _reject_unknown(table, "words", keys)
    defaults = WordsConfig()
    lists: dict[str, tuple[str, ...]] = {}
    seen: dict[str, str] = {}
    for key in keys:
        raw = table.get(key, list(getattr(defaults, key)))
        if not isinstance(raw, list) or not all(
            isinstance(word, str) and word.strip() for word in raw
        ):
            raise ConfigError(f"[words] {key} must be a list of words")
        words = tuple(" ".join(str(word).split()) for word in raw)
        for word in words:
            folded = word.lower()
            if seen.get(folded) == key:
                raise ConfigError(f"[words] {key} lists {word!r} more than once")
            if folded in seen:
                raise ConfigError(
                    f"[words] {word!r} is in both {seen[folded]} and {key}: "
                    "a word stands in one list only"
                )
            seen[folded] = key
        lists[key] = words
    return WordsConfig(yes=lists["yes"], no=lists["no"], actions=lists["actions"])


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


def _bool(table: Mapping[str, object], where: str, key: str, *, default: bool) -> bool:
    value = table.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"[{where}] {key} must be true or false")
    return value


def _positive_int(
    table: Mapping[str, object], where: str, key: str, *, default: int
) -> int:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"[{where}] {key} must be a positive whole number")
    return value


def _positive_number(
    table: Mapping[str, object], where: str, key: str, *, default: float
) -> float:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise ConfigError(f"[{where}] {key} must be a number above zero")
    return float(value)


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
