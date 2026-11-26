"""PipeWire's default source and sink, through `wpctl`.

On the NUC the configured audio device is "pipewire", an ALSA device that
follows PipeWire's default source (microphone) and sink (speaker). Which
hardware the listener hears is therefore decided by those defaults, not by the
config file, so the Audio screen lets the owner set them.

`parse_status` is pure; `PipeWire` runs `wpctl` through an injected function.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from autobot.tui.runner import CommandResult, run_command

type NodeKind = Literal["sink", "source"]

WPCTL = "wpctl"
WPCTL_TIMEOUT = 5.0

_NODE = re.compile(r"^[\s│|]*(\*)?\s*(\d+)\.\s+(.*?)\s*(\[[^\]]*\])?\s*$")
_GROUP = re.compile(r"^[\s│|]*[├└+`][─-]+\s*(.+?):\s*$")


class PipeWireError(Exception):
    """PipeWire could not be asked or changed. The message is one line."""


@dataclass(frozen=True)
class Node:
    id: int
    name: str
    default: bool


@dataclass(frozen=True)
class PipeWireStatus:
    sinks: list[Node] = field(default_factory=list)
    sources: list[Node] = field(default_factory=list)

    def nodes(self, kind: NodeKind) -> list[Node]:
        return self.sinks if kind == "sink" else self.sources

    def default(self, kind: NodeKind) -> Node | None:
        for node in self.nodes(kind):
            if node.default:
                return node
        return None


def parse_status(text: str) -> PipeWireStatus:
    """Read the audio sinks and sources out of `wpctl status`."""
    status = PipeWireStatus()
    in_audio = False
    group = ""
    for line in text.splitlines():
        if line and not line[0].isspace() and line[0] not in "│├└|":
            # A top-level section: "Audio", "Video", "Settings", or the header.
            in_audio = line.strip() == "Audio"
            group = ""
            continue
        if not in_audio:
            continue
        heading = _GROUP.match(line)
        if heading:
            group = heading.group(1).strip().lower()
            continue
        if group not in ("sinks", "sources"):
            continue
        node = _NODE.match(line)
        if node:
            entry = Node(int(node.group(2)), node.group(3), node.group(1) == "*")
            (status.sinks if group == "sinks" else status.sources).append(entry)
    return status


class PipeWire:
    def __init__(
        self, run: Callable[[Sequence[str]], CommandResult] | None = None
    ) -> None:
        self._run = run or (lambda argv: run_command(argv, timeout=WPCTL_TIMEOUT))

    def status(self) -> PipeWireStatus:
        result = self._run([WPCTL, "status"])
        if not result.ok:
            raise PipeWireError(f"PipeWire is not available: {result.summary()}")
        return parse_status(result.output)

    def set_default(self, node: Node) -> None:
        result = self._run([WPCTL, "set-default", str(node.id)])
        if not result.ok:
            raise PipeWireError(
                f"cannot make {node.name} the default: {result.summary()}"
            )
