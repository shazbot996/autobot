"""`autobot.tui.pipewire`: reading and setting PipeWire's defaults through wpctl."""

from collections.abc import Sequence

import pytest
from tui_fakes import WPCTL_STATUS

from autobot.tui.pipewire import Node, PipeWire, PipeWireError, parse_status
from autobot.tui.runner import CommandResult


def test_sinks_and_sources_are_read_from_the_audio_section_only() -> None:
    status = parse_status(WPCTL_STATUS)
    assert status.sinks == [Node(48, "Built-in Audio Analog Stereo", True)]
    assert status.sources == [
        Node(33, "AIRHUG 21 Analog Stereo", True),
        Node(49, "Built-in Audio Analog Stereo", False),
    ]


def test_the_default_is_the_starred_node() -> None:
    status = parse_status(WPCTL_STATUS)
    source = status.default("source")
    assert source is not None and source.id == 33
    assert (
        parse_status("Audio\n ├─ Sinks:\n │      48. Speaker [vol: 1.00]\n").default(
            "sink"
        )
        is None
    )


def test_ascii_tree_drawing_is_read_too() -> None:
    text = "Audio\n +- Sources:\n |  *   33. Mic   [vol: 0.50]\n |\n"
    assert parse_status(text).sources == [Node(33, "Mic", True)]


def test_empty_output_has_no_nodes() -> None:
    status = parse_status("")
    assert status.sinks == [] and status.sources == []


def test_set_default_runs_wpctl_with_the_node_id() -> None:
    calls: list[list[str]] = []

    def run(argv: Sequence[str]) -> CommandResult:
        calls.append(list(argv))
        return CommandResult(0, "")

    PipeWire(run).set_default(Node(49, "Built-in", False))
    assert calls == [["wpctl", "set-default", "49"]]


def test_a_missing_wpctl_is_one_line() -> None:
    pipewire = PipeWire(lambda argv: CommandResult(127, "command not found: wpctl"))
    with pytest.raises(PipeWireError, match="not available: command not found: wpctl"):
        pipewire.status()
    with pytest.raises(PipeWireError, match="cannot make Mic the default"):
        pipewire.set_default(Node(1, "Mic", False))
