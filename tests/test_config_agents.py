"""`autobot.config`: the agents' definitions and the shared word lists."""

from typing import Any

import pytest

from autobot.config import (
    CONFIG_ENV_VAR,
    DEFAULT_AGENTS,
    DEFAULT_ENTRY_QUESTION,
    DEFAULT_REQUEST_QUESTION,
    MODE_FORECAST,
    ROUTE_AGENTS,
    AgentConfig,
    ConfigError,
    WordsConfig,
    load_config,
    parse_config,
)


def data(**extra: object) -> dict[str, Any]:
    return {
        "audio": {"input_device": "pipewire", "output_device": "pipewire"},
        "wake_phrases": [{"phrase": "autobot", "kind": "text_match", "target": "ack"}],
        **extra,
    }


def with_agents(*agents: object) -> dict[str, Any]:
    return data(agents=list(agents))


def test_the_committed_config_defines_the_six_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)
    config = load_config()
    assert [agent.name for agent in config.agents] == list(ROUTE_AGENTS)
    assert ROUTE_AGENTS == (
        "echo",
        "question",
        "weather",
        "time",
        "camera",
        "introduce",
    )
    forecast = [route for route in config.routes if route.command == "forecast"]
    assert [(route.agent, route.mode) for route in forecast] == [
        ("weather", MODE_FORECAST)
    ]
    assert config.words.actions == ("cancel",)


def test_the_committed_definitions_seed_what_the_agents_need(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)
    config = load_config()
    for agent in config.agents:
        assert agent.description, agent.name
        assert not agent.confirm_entry and not agent.confirm_request, agent.name
        assert agent.listening, agent.name
    assert config.agent("question").prompt.count("\n") > 2
    assert '"summary"' in config.agent("question").prompt
    assert config.agent("introduce").prompt.startswith("Hi, I'm autobot")
    weather = config.agent("weather")
    assert (weather.daily_limit, weather.minute_limit) == (100, 10)
    assert weather.secret == "autobot-weather-api-key"
    camera = config.agent("camera")
    assert camera.folder == "assistant"
    assert camera.commands.splitlines() == ["make fourway-server", "make fourway"]
    assert config.agent("question").confirm_request_question == (
        "This may cost a few dollars. Are you sure you want to run this?"
    )


def test_a_file_without_agents_or_words_loads_the_defaults() -> None:
    config = parse_config(data())
    assert config.agents == DEFAULT_AGENTS
    assert [agent.name for agent in config.agents] == list(ROUTE_AGENTS)
    assert config.words == WordsConfig()
    assert "you betcha" in config.words.yes
    assert "no way" in config.words.no
    assert config.words.actions == ("cancel",)


def test_an_agent_the_file_does_not_define_keeps_its_default() -> None:
    config = parse_config(
        with_agents({"name": "weather", "zip_code": "43215", "daily_limit": 50})
    )
    assert [agent.name for agent in config.agents] == [
        "weather",
        "echo",
        "question",
        "time",
        "camera",
        "introduce",
    ]
    weather = config.agent("weather")
    assert (weather.zip_code, weather.daily_limit, weather.minute_limit) == (
        "43215",
        50,
        10,
    )
    assert (
        weather.description
        == next(
            agent for agent in DEFAULT_AGENTS if agent.name == "weather"
        ).description
    )
    assert config.agent("time") in DEFAULT_AGENTS


def test_every_key_of_a_definition_is_read() -> None:
    config = parse_config(
        with_agents(
            {
                "name": "question",
                "description": " Asks. ",
                "prompt": "line one\nline two\n",
                "confirm_entry": True,
                "confirm_entry_question": "Ready?",
                "confirm_request": True,
                "confirm_request_question": "Spend?",
                "listening": False,
            },
            {
                "name": "camera",
                "folder": "elsewhere",
                "commands": "make one\nmake two\n",
            },
        )
    )
    assert config.agent("question") == AgentConfig(
        name="question",
        description="Asks.",
        prompt="line one\nline two\n",
        confirm_entry=True,
        confirm_entry_question="Ready?",
        confirm_request=True,
        confirm_request_question="Spend?",
        listening=False,
    )
    camera = config.agent("camera")
    assert (camera.folder, camera.commands) == ("elsewhere", "make one\nmake two\n")
    assert camera.confirm_entry_question == DEFAULT_ENTRY_QUESTION
    assert camera.confirm_request_question == DEFAULT_REQUEST_QUESTION


def test_an_agent_that_is_not_defined_is_a_key_error() -> None:
    with pytest.raises(KeyError):
        parse_config(data()).agent("lists")


@pytest.mark.parametrize(
    ("config", "message"),
    [
        (
            with_agents({"name": "lists"}),
            r"\[agents #1\] name = 'lists' is not one of: echo, question",
        ),
        (
            with_agents({"name": "time"}, {"name": "time"}),
            r"\[\[agents\]\] agent 'time' is defined more than once",
        ),
        (
            with_agents({"name": "camera", "zip_code": "43215"}),
            r"\[agents #1\] zip_code is not a setting of the camera agent",
        ),
        (
            with_agents({"name": "weather", "folder": "x"}),
            r"\[agents #1\] folder is not a setting of the weather agent",
        ),
        (
            with_agents({"name": "echo", "colour": "red"}),
            r"\[agents #1\] unknown key: colour",
        ),
        (
            with_agents({"name": "weather", "zip_code": "4321"}),
            r"zip_code = '4321' is not valid: a zip code is five digits",
        ),
        (
            with_agents({"name": "weather", "daily_limit": 0}),
            r"\[agents #1\] daily_limit must be a positive whole number",
        ),
        (
            with_agents({"name": "weather", "minute_limit": "10"}),
            r"\[agents #1\] minute_limit must be a positive whole number",
        ),
        (
            with_agents({"name": "weather", "secret": "a key"}),
            r"\[agents #1\] secret = 'a key' is not valid",
        ),
        (
            with_agents({"name": "time", "listening": "yes"}),
            r"\[agents #1\] listening must be true or false",
        ),
        (
            with_agents({"name": "time", "confirm_entry_question": " "}),
            r"\[agents #1\] confirm_entry_question must not be empty",
        ),
        (with_agents({"description": "x"}), r"\[agents #1\] missing key: name"),
        (with_agents("time"), r"\[\[agents\]\] entry #1 must be a table"),
        (data(agents={"name": "time"}), r"\[\[agents\]\] must be one or more tables"),
        (
            data(words={"yes": ["yes", "cancel"]}),
            r"\[words\] 'cancel' is in both yes and actions",
        ),
        (
            data(words={"no": ["nope", "Nope"]}),
            r"\[words\] no lists 'Nope' more than once",
        ),
        (data(words={"yes": ["yes", " "]}), r"\[words\] yes must be a list of words"),
        (data(words={"actions": "cancel"}), r"\[words\] actions must be a list"),
        (data(words={"maybe": ["perhaps"]}), r"\[words\] unknown key: maybe"),
    ],
)
def test_a_bad_definition_or_word_list_is_one_line(
    config: dict[str, Any], message: str
) -> None:
    with pytest.raises(ConfigError, match=message) as raised:
        parse_config(config)
    assert "\n" not in str(raised.value)


def test_the_word_lists_are_read_and_tidied() -> None:
    words = parse_config(
        data(words={"yes": [" go  ahead ", "roger"], "no": ["nope"], "actions": []})
    ).words
    assert words == WordsConfig(yes=("go ahead", "roger"), no=("nope",), actions=())
