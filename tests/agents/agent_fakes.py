"""Fakes for the agent tests: no model, no lookup, no secret, no process started.

Each stands in for one seam an agent reaches the world through, records what
it was asked, and answers what the test scripted:

- `FakeModel`, the language model behind an agent that reasons;
- `FakeLookup`, the weather lookup;
- `FakeLaunch`, the start of a detached local command;
- `FakeSecrets`, the read of a secret's value from Secret Manager;
- `FakeHearing`, the local transcription of a recording an agent made;
- `FakeEmbedder`, the sentence embeddings of the routing cascade's tier 2;
- `FakeTool`, a tool a model may call, which the NUC runs.

`AgentRig` is a face on fakes to run one agent's experience on: no terminal,
no microphone, no speaker, no listener.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from autobot.agents.base import Launch
from autobot.agents.gemini import ChatAnswer, Model, Token
from autobot.agents.hearing import LOCAL_SOURCE, Heard
from autobot.audio_check import DeviceKind
from autobot.config import Config, parse_config
from autobot.gcp.client import GcpError
from autobot.server.contract import ChatRequest
from autobot.tui.audio import BlockHandler, Clip, InputStream
from autobot.tui.face import Experience, FaceContext, FaceModel, Seams
from autobot.tui.runner import CommandResult, FunctionTask, Task


@dataclass
class FakeModel:
    """Answers every question with `answer`, or raises `error`.

    In a conversation it gives out `replies` in order, the last one again
    once they run out, and `answer` when it has none. A reply that is an
    exception is raised. When the request streams, a reply's text is cut at
    spaces into tokens, and `stream_error` is raised after `stream_error_after`
    of them.
    """

    answer: str = '{"summary": "It is far.", "detail": ["384,400 km"]}'
    error: Exception | None = None
    # Each call's prompt and query, in order.
    calls: list[tuple[str, str]] = field(default_factory=list)
    # The conversation's answers, in order.
    replies: list[ChatAnswer | Exception] = field(default_factory=list)
    # Each conversation request, in order.
    chats: list[ChatRequest] = field(default_factory=list)
    stream_error: Exception | None = None
    stream_error_after: int = 0
    # Called between the tokens of a stream, so a test can look at the face.
    on_token: Callable[[int], None] | None = None

    def ask(self, prompt: str, query: str) -> str:
        self.calls.append((prompt, query))
        if self.error is not None:
            raise self.error
        return self.answer

    def chat(self, request: ChatRequest) -> ChatAnswer | Iterator[Token]:
        self.chats.append(request)
        if self.error is not None:
            raise self.error
        if not self.replies:
            reply: ChatAnswer | Exception = ChatAnswer(self.answer)
        elif len(self.replies) > 1:
            reply = self.replies.pop(0)
        else:
            reply = self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return self._tokens(reply) if request.stream else reply

    def _tokens(self, reply: ChatAnswer) -> Iterator[Token]:
        words = reply.content.split(" ") if reply.content else []
        for number, word in enumerate(words):
            if self.stream_error is not None and number == self.stream_error_after:
                raise self.stream_error
            yield Token(word if number == 0 else f" {word}")
            if self.on_token is not None:
                self.on_token(number)
        yield Token("", final=reply)


def fake_models(
    gemini: FakeModel | None = None, local: FakeModel | None = None
) -> dict[str, Callable[[Config, Path], Model]]:
    """`Seams.models` on fakes: the kinds given, each made anew from its one fake."""
    models: dict[str, Callable[[Config, Path], Model]] = {}
    if gemini is not None:
        models["gemini"] = lambda config, repo: gemini
    if local is not None:
        models["local"] = lambda config, repo: local
    return models


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


@dataclass
class FakeHearing:
    """Transcribes every recording as `text`, or raises `error`; loads no model."""

    text: str = "how big is the sun"
    error: Exception | None = None
    # Where the transcript says it came from (IS-15).
    source: str = LOCAL_SOURCE
    loads: int = 0
    # The length in seconds of each recording it was given.
    heard: list[float] = field(default_factory=list)

    def load(self) -> None:
        self.loads += 1

    def transcribe(self, clip: Clip) -> Heard:
        self.heard.append(clip.seconds)
        if self.error is not None:
            raise self.error
        return Heard(self.text, self.source)


@dataclass
class FakeTool:
    """A tool that does nothing: answers `result`, or raises `error`, and keeps its calls."""

    result: str = "done"
    error: Exception | None = None
    # Each call's arguments, in order.
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self, arguments: Mapping[str, Any]) -> str:
        self.calls.append(dict(arguments))
        if self.error is not None:
            raise self.error
        return self.result


class FakeEmbedder:
    """Sentence embeddings without a model: a text is near what the test says it is.

    Every text has a direction of its own, so two different texts are
    unrelated (similarity 0) and a text is itself (1). `near` maps a text to
    a phrase and the similarity it has to it: `{"what's it like outside":
    ("weather", 0.81)}`. Texts are compared lower-cased, spaces squeezed.
    `error` is raised by every call while it is set.
    """

    DIMENSIONS = 256

    def __init__(self, near: Mapping[str, tuple[str, float]] | None = None) -> None:
        self.near = {self._key(text): pair for text, pair in (near or {}).items()}
        self.error: Exception | None = None
        self.loads = 0
        # Each call's texts, in order.
        self.calls: list[list[str]] = []
        self._axes: dict[str, int] = {}

    @staticmethod
    def _key(text: str) -> str:
        return " ".join(text.lower().split())

    def _axis(self, key: str) -> np.ndarray[Any, np.dtype[np.float32]]:
        index = self._axes.setdefault(key, len(self._axes))
        vector = np.zeros(self.DIMENSIONS, dtype=np.float32)
        vector[index] = 1.0
        return vector

    def load(self) -> None:
        self.loads += 1
        if self.error is not None:
            raise self.error

    def embed(self, texts: Sequence[str]) -> np.ndarray[Any, np.dtype[np.float32]]:
        self.calls.append(list(texts))
        if self.error is not None:
            raise self.error
        rows = []
        for text in texts:
            key = self._key(text)
            own = self._axis(key)
            if key in self.near:
                phrase, score = self.near[key]
                rows.append(
                    score * self._axis(self._key(phrase))
                    + float(np.sqrt(1.0 - score * score)) * own
                )
            else:
                rows.append(own)
        return np.stack(rows).astype(np.float32)


class FakeStream:
    """An open microphone stream: the rig feeds the capture itself."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


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

    def open_mic(self) -> None:
        """The face's microphone is open: the capture is fed by `speak`."""
        self.face.mic.stream = FakeStream()

    def speak(self, seconds: float, peak: int = 8000) -> None:
        """Frames of the microphone delivering a level of `peak`, 0 for quiet."""
        capture = self.face.capture
        block = np.full(round(capture.sample_rate * FRAME), peak, dtype=np.int16)
        for _ in range(round(seconds / FRAME)):
            capture.feed(block)
            self.clock.now += FRAME
            self.model.tick()

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
