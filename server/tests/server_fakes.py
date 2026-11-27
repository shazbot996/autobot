"""Fakes for the server's tests: the card, the models and the clock (IS-12).

No test of the server needs a GPU, a model or a network: the card is a
`FakeGpu`, every model is a `FakeEngine`, and the token file is written in a
temporary directory at mode `0600`.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from autobot_server.app import App, Endpoint, Response, json_response, request_json
from autobot_server.contract import GpuMemory, Message, Tool
from autobot_server.gpu import GpuError
from autobot_server.job import Job, NotReady, Readiness
from autobot_server.runtime import Answer, LanguageModel, Request, Token

TOKEN = "a-test-token-that-is-not-a-secret"
BEARER = {"Authorization": f"Bearer {TOKEN}"}


class FakeGpu:
    """A card with the memory of `server/contract/health.json`, or none at all."""

    def __init__(
        self, used_mib: int = 7650, free_mib: int = 3360, broken: bool = False
    ):
        self.used_mib = used_mib
        self.free_mib = free_mib
        self.broken = broken

    def memory(self) -> GpuMemory:
        if self.broken:
            raise GpuError("nvidia-smi not found: the WSL GPU driver is not installed")
        return GpuMemory(used_mib=self.used_mib, free_mib=self.free_mib)


class FakeClock:
    """A monotonic clock that moves only when told to."""

    def __init__(self, now: float = 0.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeEngine:
    """A model that answers each request with itself, and can fail or hold.

    `load_error` and `warm_error` make the load or the warm-up fail;
    `run_error` makes the next run fail. With `hold` set, a run waits until
    `release` is called, so that a test can have one request running while
    it sends others.
    """

    def __init__(self, model: str = "fake-model", *, hold: bool = False):
        self._model = model
        self.loads = 0
        self.warm_ups = 0
        self.runs: list[object] = []
        self.load_error: Exception | None = None
        self.warm_error: Exception | None = None
        self.run_error: Exception | None = None
        self.hold = hold
        self.running = threading.Event()
        self._released = threading.Event()

    @property
    def model(self) -> str:
        return self._model

    def load(self) -> None:
        self.loads += 1
        if self.load_error is not None:
            raise self.load_error

    def warm_up(self) -> None:
        self.warm_ups += 1
        if self.warm_error is not None:
            raise self.warm_error

    def run(self, request: object) -> object:
        self.runs.append(request)
        if self.run_error is not None:
            error, self.run_error = self.run_error, None
            raise error
        if self.hold:
            self.running.set()
            self._released.wait(timeout=5.0)
        return request

    def release(self) -> None:
        self._released.set()


def ready_job(
    name: str, model: str, clock: FakeClock | None = None
) -> Job[object, object]:
    """A job whose fake model has loaded and warmed up."""
    job: Job[object, object] = Job(name, FakeEngine(model), clock=clock or FakeClock())
    job.start()
    return job


def health_jobs() -> dict[str, Readiness]:
    """The jobs of `server/contract/health.json`, all ready."""
    return {
        "stt": ready_job("stt", "large-v3-turbo"),
        "route": ready_job("route", "qwen3:8b"),
        "chat": ready_job("chat", "qwen3:8b"),
    }


def echo_endpoint(job: Job[object, object]) -> Endpoint:
    """A job endpoint that runs the request's JSON through `job` and answers it."""

    def endpoint(body: bytes) -> Response:
        answer = job.run(request_json(body))
        assert isinstance(answer, Mapping)
        return json_response(200, answer)

    return endpoint


def write_token(directory: Path, token: str = TOKEN, mode: int = 0o600) -> Path:
    """A token file in `directory`, at `mode`."""
    path = directory / "token"
    path.write_text(f"{token}\n", encoding="utf-8")
    path.chmod(mode)
    return path


def make_app(
    jobs: Mapping[str, Readiness] | None = None,
    *,
    gpu: FakeGpu | None = None,
    endpoints: Mapping[str, Endpoint] | None = None,
    log: list[str] | None = None,
    clock: FakeClock | None = None,
) -> App:
    """An app with the fake token, the given jobs and endpoints, logging into `log`."""
    lines = log if log is not None else []
    return App(
        token=TOKEN,
        jobs=jobs if jobs is not None else health_jobs(),
        gpu=gpu or FakeGpu(),
        contract_version="1",
        endpoints=endpoints,
        log=lines.append,
        clock=clock or FakeClock(),
    )


class FakeRuntime:
    """A language model runtime that answers what it is told to, and keeps the calls.

    `answers` are given out in order, the last one again once they run out.
    A whole answer is cut at spaces into tokens when a stream is asked for,
    unless `pieces` says how. `missing` makes `check` fail as a runtime
    without the model does, and `error` makes the next generation fail.
    """

    def __init__(
        self,
        *answers: Answer,
        model: str = "qwen3:8b",
        pieces: tuple[str, ...] | None = None,
    ):
        self._model = model
        self.answers = list(answers) or [Answer(content="ready")]
        self.pieces = pieces
        self.calls: list[Request] = []
        self.checks = 0
        self.missing = False
        self.error: Exception | None = None
        self.stream_error: Exception | None = None

    @property
    def model(self) -> str:
        return self._model

    def check(self) -> None:
        self.checks += 1
        if self.missing:
            raise NotReady(
                f"the runtime does not have the model {self._model}: "
                f"'ollama pull {self._model}' fetches it (the server never pulls)"
            )

    def generate(
        self,
        system: str,
        messages: Sequence[Message],
        schema: dict[str, Any] | None = None,
        tools: Sequence[Tool] = (),
        stream: bool = False,
    ) -> Answer | Iterator[Token]:
        self.calls.append(
            Request(system, tuple(messages), schema, tuple(tools), stream)
        )
        if self.error is not None:
            error, self.error = self.error, None
            raise error
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return self._tokens(answer) if stream else answer

    def _tokens(self, answer: Answer) -> Iterator[Token]:
        pieces = self.pieces
        if pieces is None:
            words = answer.content.split(" ")
            pieces = tuple(
                word if number == 0 else f" {word}" for number, word in enumerate(words)
            )
        for piece in pieces:
            yield Token(piece)
            if self.stream_error is not None:
                error, self.stream_error = self.stream_error, None
                raise error
        yield Token("", final=answer)


def llm_job(
    name: str, runtime: FakeRuntime, clock: FakeClock | None = None
) -> Job[Request, Answer | Iterator[Token]]:
    """A `route` or `chat` job on `runtime`, loaded and warmed up, its calls cleared."""
    job: Job[Request, Answer | Iterator[Token]] = Job(
        name, LanguageModel(runtime), clock=clock or FakeClock()
    )
    job.start()
    runtime.calls.clear()
    return job
