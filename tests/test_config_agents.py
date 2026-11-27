"""`autobot.config`: the agents' definitions and the shared word lists."""

from typing import Any

import pytest

from autobot.config import (
    AGENT_CORE,
    CONFIG_ENV_VAR,
    CORE_PROMPT,
    DEFAULT_AGENTS,
    DEFAULT_ENTRY_QUESTION,
    DEFAULT_REQUEST_QUESTION,
    MODE_FORECAST,
    ROUTE_AGENTS,
    AgentConfig,
    ConfigError,
    ParameterConfig,
    RouteConfig,
    ToolConfig,
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


def test_the_committed_config_defines_the_seven_agents(
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
        "core",
    )
    forecast = [route for route in config.routes if route.command == "forecast"]
    assert [(route.agent, route.mode) for route in forecast] == [
        ("weather", MODE_FORECAST)
    ]
    assert config.words.actions == ("cancel",)


def test_the_committed_core_agent_talks_on_the_local_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)
    config = load_config()
    core = config.agent(AGENT_CORE)
    assert core.model == "local"
    assert core.fallback == ("gemini", "the server is away")
    assert core.prompt == CORE_PROMPT
    assert core.listening
    assert core == next(agent for agent in DEFAULT_AGENTS if agent.name == "core")
    # The core agent is the default when no rule matches, not a command.
    assert all(route.agent != AGENT_CORE for route in config.routes)
    assert config.agent("question").model == "gemini"
    for name in ("echo", "weather", "time", "camera", "introduce"):
        assert config.agent(name).model == "none", name
        assert config.agent(name).fallback == (), name
    assert all(not agent.parameters and not agent.tools for agent in config.agents)
    assert all(route.examples == () for route in config.routes)


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
        "core",
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
                "model": "local",
                "fallback": ["gemini", " Sorry,  not now. "],
                "parameters": [
                    {"name": "topic", "values": ["news", "sport"], "required": True},
                    {"name": "count", "type": "integer", "values": [1, 2]},
                    {"name": "scale", "type": "number", "values": [0.5, 2]},
                    {"name": "loud", "type": "boolean"},
                ],
                "tools": [
                    {
                        "name": "lists_add",
                        "description": " Adds an item. ",
                        "parameters": [{"name": "item", "required": True}],
                    },
                    {"name": "lists_read"},
                ],
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
        model="local",
        fallback=("gemini", "Sorry, not now."),
        parameters=(
            ParameterConfig("topic", "string", ("news", "sport"), required=True),
            ParameterConfig("count", "integer", (1, 2)),
            ParameterConfig("scale", "number", (0.5, 2)),
            ParameterConfig("loud", "boolean"),
        ),
        tools=(
            ToolConfig(
                "lists_add",
                "Adds an item.",
                (ParameterConfig("item", required=True),),
            ),
            ToolConfig("lists_read"),
        ),
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
        (
            with_agents({"name": "question", "model": "openai"}),
            r"\[agents #1\] model = 'openai' is not one of: none, local, gemini",
        ),
        (
            with_agents({"name": "core", "fallback": ["the server is away", "gemini"]}),
            (
                r"\[agents #1\] fallback entry #1 'the server is away' is not one "
                r"of: none, local, gemini; a spoken line can only be last"
            ),
        ),
        (
            with_agents({"name": "core", "fallback": "gemini"}),
            r"\[agents #1\] fallback must be a list of models and, last, a spoken line",
        ),
        (
            with_agents({"name": "core", "fallback": ["gemini", " "]}),
            r"\[agents #1\] fallback must be a list",
        ),
        (
            with_agents(
                {"name": "weather", "parameters": [{"name": "day", "type": "date"}]}
            ),
            (
                r"\[agents #1 agents.parameters #1\] type = 'date' is not one of: "
                r"string, integer, number, boolean"
            ),
        ),
        (
            with_agents({"name": "weather", "parameters": [{"type": "string"}]}),
            r"\[agents #1 agents.parameters #1\] missing key: name",
        ),
        (
            with_agents({"name": "weather", "parameters": [{"name": "Day"}]}),
            r"\[agents #1 agents.parameters #1\] name = 'Day' is not valid",
        ),
        (
            with_agents(
                {"name": "weather", "parameters": [{"name": "day"}, {"name": "day"}]}
            ),
            r"\[agents #1\] \[\[agents.parameters\]\] 'day' is defined more than once",
        ),
        (
            with_agents(
                {
                    "name": "weather",
                    "parameters": [
                        {"name": "days", "type": "integer", "values": ["3"]}
                    ],
                }
            ),
            r"\[agents #1 agents.parameters #1\] values must be a list of integer values",
        ),
        (
            with_agents(
                {
                    "name": "weather",
                    "parameters": [
                        {"name": "days", "type": "integer", "values": [True]}
                    ],
                }
            ),
            r"values must be a list of integer values",
        ),
        (
            with_agents(
                {"name": "weather", "parameters": [{"name": "d", "required": 1}]}
            ),
            r"\[agents #1 agents.parameters #1\] required must be true or false",
        ),
        (
            with_agents(
                {"name": "weather", "parameters": [{"name": "d", "unit": "c"}]}
            ),
            r"\[agents #1 agents.parameters #1\] unknown key: unit",
        ),
        (
            with_agents({"name": "weather", "parameters": {"name": "day"}}),
            r"\[agents #1\] \[\[agents.parameters\]\] must be one or more tables",
        ),
        (
            with_agents({"name": "core", "tools": [{"description": "x"}]}),
            r"\[agents #1 tools #1\] missing key: name",
        ),
        (
            with_agents({"name": "core", "tools": [{"name": "a"}, {"name": "a"}]}),
            r"\[agents #1\] \[\[agents.tools\]\] 'a' is defined more than once",
        ),
        (
            with_agents({"name": "core", "tools": [{"name": "a", "run": "rm"}]}),
            r"\[agents #1 tools #1\] unknown key: run",
        ),
        (
            with_agents(
                {
                    "name": "core",
                    "tools": [
                        {"name": "a", "parameters": [{"name": "b", "type": "list"}]}
                    ],
                }
            ),
            r"\[agents #1 tools #1 agents.tools.parameters #1\] type = 'list' is not",
        ),
        (
            with_agents({"name": "core", "tools": ["a"]}),
            r"\[agents #1\] \[\[agents.tools\]\] entry #1 must be a table",
        ),
        (
            data(routes=[{"command": "weather", "agent": "weather", "examples": "x"}]),
            r"\[routes #1\] examples must be a list of phrases",
        ),
        (
            data(
                routes=[
                    {
                        "command": "weather",
                        "agent": "weather",
                        "examples": ["is it cold", "Is it  cold"],
                    }
                ]
            ),
            r"\[routes #1\] examples lists 'Is it cold' more than once",
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


def test_a_fallback_may_end_in_a_spoken_line_or_in_a_model() -> None:
    config = parse_config(
        with_agents(
            {"name": "core", "fallback": ["gemini", "none", "local"]},
            {"name": "question", "fallback": ["the server is away"]},
            {"name": "time", "fallback": []},
        )
    )
    assert config.agent("core").fallback == ("gemini", "none", "local")
    assert config.agent("question").fallback == ("the server is away",)
    assert config.agent("time").fallback == ()


def test_a_rule_reads_its_example_phrases() -> None:
    routes = parse_config(
        data(
            routes=[
                {
                    "command": "weather",
                    "agent": "weather",
                    "examples": [" what's it like  outside ", "is it cold"],
                },
                {"command": "time", "agent": "time"},
            ]
        )
    ).routes
    assert routes == (
        RouteConfig(
            "weather", "weather", examples=("what's it like outside", "is it cold")
        ),
        RouteConfig("time", "time"),
    )
