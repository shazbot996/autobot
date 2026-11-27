"""The `introduce` agent: the written script, read aloud as it stands."""

from pathlib import Path

from agents.agent_fakes import AgentRig
from autobot.agents.introduce import NO_SCRIPT, script_response
from autobot.agents.registry import AGENTS
from autobot.config import AGENT_INTRODUCE, INTRODUCE_SCRIPT

SCRIPT = '''
[[agents]]
name = "introduce"
prompt = """
I am autobot.
I live on this computer.
"""
'''


def test_the_summary_is_the_script_and_the_detail_its_lines(tmp_path: Path) -> None:
    rig = AgentRig(tmp_path, extra=SCRIPT)
    agent = rig.run(AGENT_INTRODUCE)
    rig.finish(agent)
    assert rig.summary == ["I am autobot. I live on this computer."]
    assert rig.voice.said == ["I am autobot. I live on this computer."]
    assert rig.detail == ["I am autobot.", "I live on this computer."]
    assert rig.troubles == []


def test_the_default_script_is_the_one_the_configuration_seeds(tmp_path: Path) -> None:
    rig = AgentRig(tmp_path)
    agent = rig.run(AGENT_INTRODUCE)
    rig.finish(agent)
    spoken = " ".join(INTRODUCE_SCRIPT.split())
    assert rig.summary == [spoken]
    assert spoken.startswith("Hi, I'm autobot")


def test_no_model_stands_behind_it() -> None:
    agent = AGENTS[AGENT_INTRODUCE]
    assert not agent.uses_model and not agent.sends_requests


def test_an_empty_script_is_one_line_of_trouble(tmp_path: Path) -> None:
    rig = AgentRig(tmp_path, extra='[[agents]]\nname = "introduce"\nprompt = " "\n')
    agent = rig.run(AGENT_INTRODUCE)
    assert agent.done
    assert rig.troubles == [NO_SCRIPT]
    assert rig.voice.said == []
    assert script_response("\n \n") is None
