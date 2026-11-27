"""`autobot.agents.registry`: every built agent, in the one shape (AG-1)."""

import tomllib

from autobot.agents.base import Launch
from autobot.agents.registry import AGENTS
from autobot.config import AGENT_ECHO, AGENT_SETTINGS, ROUTE_AGENTS, parse_config
from autobot.tui.echo import Echo

CONFIG = parse_config(
    tomllib.loads(
        """
[audio]
input_device = "pipewire"
output_device = "pipewire"

[[wake_phrases]]
phrase = "autobot"
kind = "text_match"
target = "ack"
"""
    )
)


def test_every_registered_agent_is_a_routable_name_under_its_own_name() -> None:
    assert AGENT_ECHO in AGENTS
    assert set(AGENTS) <= set(ROUTE_AGENTS)
    for name, agent in AGENTS.items():
        assert agent.name == name


def test_every_agent_says_what_its_code_does_and_owns_its_settings() -> None:
    for name, agent in AGENTS.items():
        assert agent.about
        assert agent.settings == AGENT_SETTINGS.get(name, ())


def test_every_agent_makes_an_experience_from_its_launch() -> None:
    for name, agent in AGENTS.items():
        experience = agent.make(Launch(CONFIG.agent(name), "", name))
        assert not experience.done
        assert callable(experience.start) and callable(experience.tick)


def test_the_echo_is_local_and_runs_the_echo() -> None:
    echo = AGENTS[AGENT_ECHO]
    assert not echo.uses_model
    assert not echo.sends_requests
    assert isinstance(echo.make(Launch(CONFIG.agent(AGENT_ECHO), "", "echo")), Echo)
