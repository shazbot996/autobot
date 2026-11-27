"""One job of the server, `stt`, `route` or `chat`, and the model behind it (IS-23, IS-24).

A `Job` wraps one `Engine`: the model, loaded once at start and kept
resident. The job is `loading` until the model has loaded and answered one
warm-up request, then `ready`; when either fails it is `not ready`, with a
one-line reason that `/health` reports (IS-11).

A ready job admits one running request and one waiting behind it. A further
request is refused at once with `Busy`, which the app answers `503` with
`Retry-After`, so the NUC falls back instead of waiting (IS-24).

`GAMEON1` is the owner's gaming PC, and a game can take the card at any time.
A streamed answer keeps its place until its last piece is sent: `stream`
admits the request as `run` does and gives back the pieces, and the request
counts as running until they are exhausted or closed.

A request that fails with a CUDA error, out of memory among them, marks the
job not ready with the reason instead of crashing the server; `tick`, called
by `keep_warm`, tries the warm-up again once `retry_seconds` have passed,
and the job is ready again when memory has freed. The server is never
restarted for it.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable, Iterator
from typing import Any, Protocol

from autobot_server.contract import JobHealth

# The jobs of the server, in the order `/health` lists them (IS-11).
JOB_NAMES = ("stt", "route", "chat")

LOADING = "loading"
READY = "ready"
NOT_READY = "not ready"

# One request running and one waiting behind it (IS-24).
ADMITTED = 2
# How long a job that is not ready waits before its warm-up is tried again.
RETRY_SECONDS = 30.0
# What an error from a model says when it lost the GPU (IS-24).
GPU_ERRORS = ("out of memory", "cuda")


class NotReady(Exception):
    """A job cannot be handed work. The message is the one-line reason."""


class Busy(Exception):
    """A job already has a request running and one waiting. The message is one line."""


class Engine[Request, Answer](Protocol):
    """One model behind a job: loaded once, warmed once, then run per request."""

    @property
    def model(self) -> str:
        """The model's name, as `/health` reports it."""
        ...

    def load(self) -> None:
        """Load the model from its local files; `NotReady` when one is missing."""
        ...

    def warm_up(self) -> None:
        """Answer one request of the model's own, so that the first real one is quick."""
        ...

    def run(self, request: Request) -> Answer:
        """The model's answer to `request`."""
        ...


class Readiness(Protocol):
    """What `/health` and `keep_warm` need of a job, whatever it runs."""

    @property
    def name(self) -> str: ...

    def health(self) -> JobHealth: ...

    def start(self) -> None: ...

    def tick(self) -> None: ...


class Unavailable:
    """An engine this server cannot load: the job stays not ready with `reason`."""

    def __init__(self, model: str, reason: str):
        self._model = model
        self._reason = reason

    @property
    def model(self) -> str:
        return self._model

    def load(self) -> None:
        raise NotReady(self._reason)

    def warm_up(self) -> None:
        raise NotReady(self._reason)

    def run(self, request: object) -> object:
        raise NotReady(self._reason)


class Job[Request, Answer]:
    """One model's readiness, and the admission of the requests it runs."""

    def __init__(
        self,
        name: str,
        engine: Engine[Request, Answer],
        *,
        retry_seconds: float = RETRY_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._name = name
        self._engine = engine
        self._retry_seconds = retry_seconds
        self._clock = clock
        # Guards the state and the count of admitted requests.
        self._lock = threading.Lock()
        # Held by the one request that runs, and by a warm-up.
        self._running = threading.Lock()
        self._admitted = 0
        self._loaded = False
        self._state = LOADING
        self._reason = LOADING
        self._retry_at: float | None = None

    @property
    def name(self) -> str:
        """The job's name: one of `JOB_NAMES`."""
        return self._name

    @property
    def state(self) -> str:
        """`loading`, `ready` or `not ready`."""
        with self._lock:
            return self._state

    @property
    def reason(self) -> str:
        """Why the job is not ready, in one line; empty when it is."""
        with self._lock:
            return self._reason

    @property
    def admitted(self) -> int:
        """The requests running or waiting."""
        with self._lock:
            return self._admitted

    def health(self) -> JobHealth:
        """The job as `/health` reports it; the model is not touched."""
        with self._lock:
            ready = self._state == READY
            return JobHealth(
                ready=ready,
                model=self._engine.model,
                reason="" if ready else self._reason,
            )

    def start(self) -> None:
        """Load the model, once, and answer one warm-up; ready, or not ready with why."""
        with self._running:
            try:
                if not self._loaded:
                    self._engine.load()
                    self._loaded = True
                self._engine.warm_up()
            except Exception as error:  # noqa: BLE001
                # A missing file, a refused CUDA call, a runtime that is not
                # up: whatever the model raised is the one-line reason.
                self._fail(one_line(error))
                return
            with self._lock:
                self._state, self._reason, self._retry_at = READY, "", None

    def tick(self) -> None:
        """Try the warm-up again when the job is not ready and its retry is due."""
        with self._lock:
            due = (
                self._state == NOT_READY
                and self._retry_at is not None
                and self._clock() >= self._retry_at
            )
        if due:
            self.start()

    def run(self, request: Request) -> Answer:
        """The engine's answer; `Busy` beyond one running and one waiting, `NotReady` when not ready."""
        with self._lock:
            if self._state != READY:
                raise NotReady(f"{self.name} is not ready: {self._reason}")
            if self._admitted >= ADMITTED:
                raise Busy(f"{self.name} is busy: try again in a moment")
            self._admitted += 1
        try:
            with self._running:
                with self._lock:
                    # The request that waited may find the GPU taken meanwhile.
                    if self._state != READY:
                        raise NotReady(f"{self.name} is not ready: {self._reason}")
                try:
                    return self._engine.run(request)
                except Exception as error:
                    if not lost_the_gpu(error):
                        raise
                    reason = one_line(error)
                    self._fail(reason)
                    raise NotReady(f"{self.name} is not ready: {reason}") from None
        finally:
            with self._lock:
                self._admitted -= 1

    def stream(self, request: Request) -> Iterator[Any]:
        """The pieces of the engine's streamed answer, admitted as `run` admits.

        The engine's `run` must answer an iterator. The request holds its
        place until the pieces are exhausted or the iterator is closed, as
        when the NUC hangs up.
        """
        with self._lock:
            if self._state != READY:
                raise NotReady(f"{self.name} is not ready: {self._reason}")
            if self._admitted >= ADMITTED:
                raise Busy(f"{self.name} is busy: try again in a moment")
            self._admitted += 1
        self._running.acquire()
        held = _Held(self._release)
        try:
            with self._lock:
                if self._state != READY:
                    raise NotReady(f"{self.name} is not ready: {self._reason}")
            answer: Any = self._engine.run(request)
            held.pieces = iter(answer)
        except BaseException as error:
            held.close()
            if isinstance(error, Exception) and lost_the_gpu(error):
                reason = one_line(error)
                self._fail(reason)
                raise NotReady(f"{self.name} is not ready: {reason}") from None
            raise
        held.failed = self._stream_failed
        return held

    def _release(self) -> None:
        self._running.release()
        with self._lock:
            self._admitted -= 1

    def _stream_failed(self, error: Exception) -> None:
        if lost_the_gpu(error):
            self._fail(one_line(error))

    def _fail(self, reason: str) -> None:
        with self._lock:
            self._state = NOT_READY
            self._reason = reason
            self._retry_at = self._clock() + self._retry_seconds


class _Held:
    """A streamed answer's pieces, holding the job's running place until done."""

    def __init__(self, release: Callable[[], None]):
        self._release: Callable[[], None] | None = release
        self.pieces: Iterator[Any] = iter(())
        self.failed: Callable[[Exception], None] = lambda error: None

    def __iter__(self) -> Iterator[Any]:
        return self

    def __next__(self) -> Any:
        if self._release is None:
            raise StopIteration
        try:
            return next(self.pieces)
        except StopIteration:
            self.close()
            raise
        except Exception as error:
            self.close()
            self.failed(error)
            raise

    def close(self) -> None:
        """Give the place back, once; closes the engine's iterator too."""
        release, self._release = self._release, None
        if release is None:
            return
        closer = getattr(self.pieces, "close", None)
        try:
            if callable(closer):
                closer()
        finally:
            release()

    def __del__(self) -> None:
        self.close()


def lost_the_gpu(error: BaseException) -> bool:
    """Whether a model's error says it ran out of GPU memory or lost the card."""
    text = f"{type(error).__name__} {error}".lower()
    return any(sign in text for sign in GPU_ERRORS)


def one_line(error: BaseException) -> str:
    """An error as one line: its first line, or its type's name when it says nothing."""
    lines = str(error).strip().splitlines()
    return lines[0] if lines else type(error).__name__


def keep_warm(
    jobs: Iterable[Readiness],
    stop: threading.Event,
    interval: float = 1.0,
) -> None:
    """Start every job, then retry the ones that are not ready until `stop` is set."""
    every = tuple(jobs)
    for job in every:
        if stop.is_set():
            return
        job.start()
    while not stop.wait(interval):
        for job in every:
            job.tick()
