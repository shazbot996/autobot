"""The `camera` agent: two local commands started on the NUC, neither waited on."""

from pathlib import Path

from agents.agent_fakes import AgentRig, FakeLaunch
from autobot.agents.camera import NO_COMMANDS, OPENING, command_lines
from autobot.agents.registry import AGENTS
from autobot.config import AGENT_CAMERA
from autobot.tui.face import Seams
from autobot.tui.runner import launch_detached


def rig_with(tmp_path: Path, launch: FakeLaunch, **kwargs: str) -> AgentRig:
    (tmp_path / "assistant").mkdir(exist_ok=True)
    return AgentRig(tmp_path, seams=Seams(launch=launch), **kwargs)


def test_on_the_nuc_the_two_commands_are_started_in_order(tmp_path: Path) -> None:
    launch = FakeLaunch()
    rig = rig_with(tmp_path, launch)
    agent = rig.run(AGENT_CAMERA, "open the cameras")
    # Both were started by the time `start` returned: nothing was waited on.
    assert launch.calls == [
        (["make", "fourway-server"], tmp_path / "assistant"),
        (["make", "fourway"], tmp_path / "assistant"),
    ]
    rig.finish(agent)
    assert rig.summary == [OPENING]
    assert rig.voice.said == [OPENING]
    assert rig.detail == [
        "Started in assistant/, not waited on:",
        "  make fourway-server",
        "  make fourway",
        "Their output goes to run/agents.log.",
    ]
    assert rig.troubles == []
    # The only command the face itself ran is the listener's hold.
    assert all("hold" in call for call in rig.runner.calls)


def test_off_the_nuc_it_refuses_and_starts_nothing(tmp_path: Path) -> None:
    launch = FakeLaunch()
    rig = rig_with(tmp_path, launch, hostname="testbox")
    agent = rig.run(AGENT_CAMERA)
    assert agent.done
    assert rig.troubles == [
        "the camera agent runs on the NUC only; this host is testbox"
    ]
    assert launch.calls == []
    assert rig.voice.said == []


def test_a_missing_folder_is_one_line(tmp_path: Path) -> None:
    launch = FakeLaunch()
    rig = AgentRig(tmp_path, seams=Seams(launch=launch))
    agent = rig.run(AGENT_CAMERA)
    assert agent.done
    assert rig.troubles == ["the camera agent's folder assistant does not exist"]
    assert launch.calls == []


def test_a_start_that_fails_is_one_line_and_the_next_is_not_started(
    tmp_path: Path,
) -> None:
    launch = FakeLaunch(error="cannot start make: No such file or directory")
    rig = rig_with(tmp_path, launch)
    agent = rig.run(AGENT_CAMERA)
    assert agent.done
    assert rig.troubles == [
        "the camera agent: cannot start make: No such file or directory"
    ]
    assert len(launch.calls) == 1


def test_the_definition_names_the_folder_and_the_commands(tmp_path: Path) -> None:
    extra = '''
[[agents]]
name = "camera"
folder = "cams"
commands = """
./serve --port 8099

firefox "http://localhost:8123/a page"
"""
'''
    (tmp_path / "cams").mkdir()
    launch = FakeLaunch()
    rig = AgentRig(tmp_path, extra=extra, seams=Seams(launch=launch))
    rig.finish(rig.run(AGENT_CAMERA))
    assert launch.calls == [
        (["./serve", "--port", "8099"], tmp_path / "cams"),
        (["firefox", "http://localhost:8123/a page"], tmp_path / "cams"),
    ]


def test_no_commands_is_one_line(tmp_path: Path) -> None:
    extra = '[[agents]]\nname = "camera"\ncommands = ""\n'
    launch = FakeLaunch()
    rig = rig_with(tmp_path, launch, extra=extra)
    assert rig.run(AGENT_CAMERA).done
    assert rig.troubles == [NO_COMMANDS]
    assert command_lines("\n a \n\n b\n") == ["a", "b"]


def test_the_camera_agent_is_local() -> None:
    agent = AGENTS[AGENT_CAMERA]
    assert not agent.uses_model and not agent.sends_requests
    assert agent.settings == ("folder", "commands")


def test_a_detached_command_is_started_and_not_waited_on(tmp_path: Path) -> None:
    log = tmp_path / "run" / "agents.log"
    marker = tmp_path / "started"
    # A real, harmless command: it writes a file a moment after it starts.
    argv = ["sh", "-c", f"sleep 0.2; echo ran; touch {marker}"]
    assert launch_detached(argv, tmp_path, log) == ""
    # It returned before the command ended.
    assert not marker.exists()
    for _ in range(100):
        if marker.exists():
            break
        import time

        time.sleep(0.05)
    assert marker.exists()
    text = log.read_text(encoding="utf-8")
    assert text.startswith("--- sh -c ") and "ran" in text


def test_a_detached_start_that_cannot_happen_is_one_line(tmp_path: Path) -> None:
    log = tmp_path / "run" / "agents.log"
    assert launch_detached(["make"], tmp_path / "nowhere", log) == (
        f"the folder {tmp_path / 'nowhere'} does not exist"
    )
    line = launch_detached(["no-such-command-here"], tmp_path, log)
    assert line.startswith("cannot start no-such-command-here: ") and "\n" not in line
    assert launch_detached([], tmp_path, log) == "no command to start"
