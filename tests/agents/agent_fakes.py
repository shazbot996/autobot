"""Fakes for the agent tests: no model, no lookup, no secret, no process started.

Each stands in for one seam an agent reaches the world through, records what
it was asked, and answers what the test scripted:

- `FakeModel`, the language model behind an agent that reasons;
- `FakeLookup`, the weather lookup;
- `FakeLaunch`, the start of a detached local command;
- `FakeSecrets`, the read of a secret's value from Secret Manager.

`AgentRig` is a face on fakes to run one agent's experience on: no terminal,
no microphone, no speaker, no listener.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from autobot.agents.base import Launch
from autobot.audio_check import DeviceKind
from autobot.config import parse_config
from autobot.gcp.client import GcpError
from autobot.tui.audio import BlockHandler, Clip, InputStream
from autobot.tui.face import Experience, FaceContext, FaceModel, Seams
from autobot.tui.runner import CommandResult, FunctionTask, Task


@dataclass
class FakeModel:
    """Answers every question with `answer`, or raises `error`."""

    answer: str = '{"summary": "It is far.", "detail": ["384,400 km"]}'
    error: Exception | None = None
    # Each call's prompt and query, in order.
    calls: list[tuple[str, str]] = field(default_factory=list)

    def ask(self, prompt: str, query: str) -> str:
        self.calls.append((prompt, query))
        if self.error is not None:
            raise self.error
        return self.answer


@dataclass
class FakeLookup[T]:
    """Answers today's weather and the days ahead with `forecast`, or raises `error`."""

    forecast: T
    error: Exception | None = None
    # Each call's kind (`today`, `days`) and zip code; never the key.
    calls: list[tuple[str, str]] = field(default_factory=list)

    def today(self, zip_code: str, key: bytearray) -> T:
        return self._answer("today", zip_code)

    def days(self, zip_code: str, key: bytearray) -> T:
        return self._answer("days", zip_code)

    def _answer(self, kind: str, zip_code: str) -> T:
        self.calls.append((kind, zip_code))
        if self.error is not None:
            raise self.error
        return self.forecast


@dataclass
class FakeLaunch:
    """Records each command started and where; answers `error`, empty when it started."""

    error: str = ""
    calls: list[tuple[list[str], Path]] = field(default_factory=list)

    def __call__(self, argv: Sequence[str], cwd: Path) -> str:
        self.calls.append((list(argv), cwd))
        return self.error


@dataclass
class FakeSecrets:
    """Secret Manager's latest values, by secret; a secret not in `values` is a 404.

    Every value handed out is kept in `given`, so a test can see the caller
    cleared it.
    """

    values: dict[str, bytes] = field(default_factory=dict)
    reads: list[str] = field(default_factory=list)
    given: list[bytearray] = field(default_factory=list)

    def secret_value(self, secret: str) -> bytearray:
        self.reads.append(secret)
        if secret not in self.values:
            raise GcpError(f"secret {secret} has no enabled version (HTTP 404)")
        value = bytearray(self.values[secret])
        self.given.append(value)
        return value


# --- a face to run an agent on ------------------------------------------------

AGENT_CONFIG_TEXT = """
[host]
nuc_hostname = "nuc"

[audio]
input_device = "pipewire"
output_device = "pipewire"

[[wake_phrases]]
phrase = "autobot"
kind = "text_match"
target = "ack"
"""
# One frame of the rig's clock, in seconds.
FRAME = 0.1
# What the fake voice says every line in: a quarter second of silence.
SPOKEN_SECONDS = 0.25


class RigClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class RigAudio:
    """An output device, `pipewire`, that plays nothing and keeps what it was given."""

    def __init__(self) -> None:
        self.played: list[Clip] = []
        self.stops = 0

    def query_devices(self) -> list[Mapping[str, Any]]:
        return [{"name": "pipewire", "max_input_channels": 2, "max_output_channels": 2}]

    def supports(self, index: int, kind: DeviceKind, sample_rate: int) -> bool:
        return True

    def open_input(
        self, index: int, sample_rate: int, block_samples: int, on_block: BlockHandler
    ) -> InputStream:
        raise AssertionError("an agent opens no microphone")

    def play(self, clip: Clip, index: int) -> None:
        self.played.append(clip)

    def stop_playback(self) -> None:
        self.stops += 1


class _Done:
    def poll(self) -> CommandResult | None:
        return CommandResult(0, "ok\n")

    def cancel(self) -> None:
        pass


class RigRunner:
    """Records the commands started (the listener's hold) and answers ok at once."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def start(
        self, argv: Sequence[str], *, cwd: Path | None = None, timeout: float = 60.0
    ) -> Task:
        self.calls.append(list(argv))
        return _Done()


class RigVoice:
    """autobot's voice without piper: every line is a short silence, and is kept."""

    def __init__(self) -> None:
        self.said: list[str] = []
        self.error: Exception | None = None

    def __call__(self, text: str, voice: str, models_root: Path) -> Clip:
        if self.error is not None:
            raise self.error
        self.said.append(text)
        samples = np.zeros(int(16000 * SPOKEN_SECONDS), dtype=np.int16)
        return Clip(samples, 16000)


class AgentRig:
    """A `FaceModel` on fakes, to run one agent's experience on.

    `seams` are what the agent reaches the world through; `extra` is TOML
    appended to the base configuration (`[[agents]]` tables, `[[routes]]`).
    `hostname="nuc"` is the configured NUC.
    """

    def __init__(
        self,
        tmp_path: Path,
        *,
        extra: str = "",
        seams: Seams | None = None,
        hostname: str = "nuc",
    ) -> None:
        self.clock = RigClock()
        self.audio = RigAudio()
        self.runner = RigRunner()
        self.voice = RigVoice()
        self.config = parse_config(tomllib.loads(AGENT_CONFIG_TEXT + extra))
        self.repo = tmp_path
        self.model = FaceModel(
            self.config,
            experience=lambda: self._never(),
            audio=self.audio,
            runner=self.runner,
            render=self.voice,
            spawn=lambda function: FunctionTask(function, inline=True),
            repo=tmp_path,
            models_root=tmp_path,
            hostname=hostname,
            clock=self.clock,
            seams=seams,
        )

    def _never(self) -> Experience:
        raise AssertionError("the rig runs an agent directly; nothing wakes it")

    @property
    def face(self) -> FaceContext:
        return self.model.context

    def launch(self, name: str, transcript: str = "", mode: str = "") -> Launch:
        return Launch(self.config.agent(name), mode, transcript or name)

    def start(self, experience: Experience) -> Experience:
        """Run `experience` on the face, as the router does when a command starts it."""
        # A session begins: the panels are cleared, as a wake clears them.
        self.face.summary.clear()
        self.face.detail.clear()
        self.model.experience = experience
        self.face.reactions = experience.reactions
        experience.start(self.face)
        return experience

    def run(self, name: str, transcript: str = "", mode: str = "") -> Experience:
        """Make the registered agent `name` for a launch and start it."""
        from autobot.agents.registry import AGENTS

        return self.start(AGENTS[name].make(self.launch(name, transcript, mode)))

    def frames(self, seconds: float) -> None:
        for _ in range(round(seconds / FRAME)):
            self.clock.now += FRAME
            self.model.tick()

    def finish(self, experience: Experience, limit: float = 30.0) -> None:
        """Run frames until the experience ends; fail when it does not."""
        waited = 0.0
        while not experience.done:
            assert waited < limit, f"still running after {limit:g} s: {self.summary}"
            self.frames(FRAME)
            waited += FRAME

    def hear(self, text: str) -> None:
        """What the listener transcribed, as the face puts it for the experience."""
        self.face.heard.append(text)

    @property
    def summary(self) -> list[str]:
        return self.model.summary.texts

    @property
    def troubles(self) -> list[str]:
        return [line.text for line in self.model.summary.lines if line.trouble]

    @property
    def detail(self) -> list[str]:
        return list(self.model.detail.lines)
