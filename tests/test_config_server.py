"""`autobot.config`: the `[server]` table of the GPU inference server (IS-3)."""

from typing import Any

import pytest

from autobot.config import (
    CONFIG_ENV_VAR,
    ConfigError,
    ServerConfig,
    load_config,
    parse_config,
    repo_root,
    url_complaint,
)


def data(**server: Any) -> dict[str, Any]:
    return {
        "audio": {"input_device": "pipewire", "output_device": "pipewire"},
        "wake_phrases": [{"phrase": "autobot", "kind": "text_match", "target": "ack"}],
        "server": server,
    }


def test_the_committed_config_names_the_server_and_its_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)
    config = load_config()
    server = config.server
    assert server.url == "http://192.168.86.53:8300"
    assert (server.host, server.port) == ("192.168.86.53", 8300)
    assert server.enabled
    assert server.hostname == "GAMEON1"
    assert server.token_file == "secrets/server-token"
    assert server.token_path == repo_root() / "secrets" / "server-token"
    assert server.core_listen_seconds == 10
    assert (server.health_timeout, server.route_timeout) == (1.0, 1.5)
    assert (server.stt_timeout_base, server.stt_timeout_per_second) == (0.5, 0.25)
    assert (server.chat_first_token_timeout, server.chat_timeout) == (3.0, 30.0)
    assert (
        server.poll_up_seconds,
        server.poll_down_min_seconds,
        server.poll_down_max_seconds,
    ) == (5.0, 5.0, 60.0)
    assert server.stt_model == "models/faster-whisper/large-v3-turbo"
    assert server.stt_compute_type in ("int8_float16", "float16")
    assert server.llm_context == 8192
    assert (server.runtime, server.runtime_url) == ("ollama", "http://127.0.0.1:11434")
    assert server.embedding_path.name.endswith(".onnx")
    assert config.agent("core").model == "local"


def test_a_file_without_the_table_loads_the_defaults() -> None:
    assert parse_config(data()).server == ServerConfig()
    without = data()
    del without["server"]
    assert parse_config(without).server == ServerConfig()


def test_every_key_is_read() -> None:
    server = parse_config(
        data(
            enabled=False,
            url="http://10.0.0.2:9000/",
            token_file="/elsewhere/token",
            hostname="OTHER",
            health_timeout=2,
            stt_timeout_base=0.75,
            stt_timeout_per_second=0.5,
            route_timeout=2.5,
            chat_first_token_timeout=4,
            chat_timeout=40,
            poll_up_seconds=3,
            poll_down_min_seconds=4,
            poll_down_max_seconds=4,
            tier2_threshold=0.5,
            tier3_threshold=1,
            hint_chars=200,
            tool_rounds=1,
            core_listen_seconds=2.5,
            stt_model="models/other",
            stt_compute_type="float16",
            llm_model="qwen3:8b",
            llm_context=4096,
            runtime="ollama",
            runtime_url="http://127.0.0.1:8080",
            embedding_model="models/e.onnx",
        )
    ).server
    assert server == ServerConfig(
        enabled=False,
        url="http://10.0.0.2:9000/",
        token_file="/elsewhere/token",
        hostname="OTHER",
        health_timeout=2.0,
        stt_timeout_base=0.75,
        stt_timeout_per_second=0.5,
        route_timeout=2.5,
        chat_first_token_timeout=4.0,
        chat_timeout=40.0,
        poll_up_seconds=3.0,
        poll_down_min_seconds=4.0,
        poll_down_max_seconds=4.0,
        tier2_threshold=0.5,
        tier3_threshold=1.0,
        hint_chars=200,
        tool_rounds=1,
        core_listen_seconds=2.5,
        stt_model="models/other",
        stt_compute_type="float16",
        llm_model="qwen3:8b",
        llm_context=4096,
        runtime="ollama",
        runtime_url="http://127.0.0.1:8080",
        embedding_model="models/e.onnx",
    )
    assert server.port == 9000
    assert str(server.token_path) == "/elsewhere/token"


@pytest.mark.parametrize(
    "text",
    ["http://192.168.86.53:8300", "http://gameon1.lan:8300/", "http://[::1]:8300"],
)
def test_an_http_address_with_a_port_is_valid(text: str) -> None:
    assert url_complaint(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "192.168.86.53:8300",
        "http://192.168.86.53",
        "https://192.168.86.53:8300",
        "http://192.168.86.53:8300/v1",
        "http://192.168.86.53:port",
        "http://192.168.86.53:8300?x=1",
        "http://:8300",
    ],
)
def test_anything_else_is_not_an_address(text: str) -> None:
    assert url_complaint(text) == "an address looks like http://192.168.86.53:8300"


@pytest.mark.parametrize(
    ("server", "message"),
    [
        ({"colour": "red"}, r"\[server\] unknown key: colour"),
        ({"enabled": "yes"}, r"\[server\] enabled must be true or false"),
        (
            {"url": "192.168.86.53:8300"},
            r"\[server\] url = '192.168.86.53:8300' is not valid: an address looks",
        ),
        ({"url": " "}, r"\[server\] url must not be empty"),
        (
            {"runtime_url": "http://127.0.0.1"},
            r"\[server\] runtime_url = 'http://127.0.0.1' is not valid",
        ),
        ({"token_file": ""}, r"\[server\] token_file must not be empty"),
        ({"token_file": 3}, r"\[server\] token_file must be a string"),
        ({"hostname": ""}, r"\[server\] hostname must not be empty"),
        ({"health_timeout": 0}, r"\[server\] health_timeout must be a number above"),
        ({"stt_timeout_base": -1}, r"\[server\] stt_timeout_base must be a number"),
        (
            {"stt_timeout_per_second": "0.25"},
            r"\[server\] stt_timeout_per_second must be a number above zero",
        ),
        ({"route_timeout": True}, r"\[server\] route_timeout must be a number"),
        (
            {"chat_first_token_timeout": 0},
            r"\[server\] chat_first_token_timeout must be a number above zero",
        ),
        ({"chat_timeout": 0}, r"\[server\] chat_timeout must be a number above zero"),
        ({"poll_up_seconds": 0}, r"\[server\] poll_up_seconds must be a number"),
        (
            {"poll_down_min_seconds": 0},
            r"\[server\] poll_down_min_seconds must be a number above zero",
        ),
        (
            {"poll_down_max_seconds": 0},
            r"\[server\] poll_down_max_seconds must be a number above zero",
        ),
        (
            {"poll_down_min_seconds": 30, "poll_down_max_seconds": 10},
            r"\[server\] poll_down_max_seconds must not be below poll_down_min",
        ),
        (
            {"tier2_threshold": 0},
            r"\[server\] tier2_threshold must be a number above zero",
        ),
        (
            {"tier3_threshold": 1.5},
            r"\[server\] tier3_threshold = 1.5 must be above 0 and at most 1",
        ),
        ({"hint_chars": 0}, r"\[server\] hint_chars must be a positive whole number"),
        ({"tool_rounds": 1.5}, r"\[server\] tool_rounds must be a positive whole"),
        (
            {"core_listen_seconds": 0},
            r"\[server\] core_listen_seconds must be a number above zero",
        ),
        (
            {"core_listen_seconds": -10},
            r"\[server\] core_listen_seconds must be a number above zero",
        ),
        ({"stt_model": ""}, r"\[server\] stt_model must not be empty"),
        (
            {"stt_compute_type": "bfloat16"},
            r"\[server\] stt_compute_type = 'bfloat16' is not one of: int8_float16",
        ),
        ({"llm_model": 8}, r"\[server\] llm_model must be a string"),
        ({"llm_context": 0}, r"\[server\] llm_context must be a positive whole"),
        (
            {"runtime": "vllm"},
            r"\[server\] runtime = 'vllm' is not one of: ollama",
        ),
        ({"embedding_model": ""}, r"\[server\] embedding_model must not be empty"),
    ],
)
def test_a_bad_server_table_is_one_line_naming_the_key(
    server: dict[str, Any], message: str
) -> None:
    with pytest.raises(ConfigError, match=message) as raised:
        parse_config(data(**server))
    assert "\n" not in str(raised.value)


def test_a_server_that_is_not_a_table_is_refused() -> None:
    config = data()
    config["server"] = "http://192.168.86.53:8300"
    with pytest.raises(ConfigError, match=r"\[server\] must be a table"):
        parse_config(config)
