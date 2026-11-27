"""The NUC watches for the inference server (IS-22, IS-23). No drawing.

`ServerWatch` asks the server's `/health` on a schedule and keeps what it
last learned, so that everything else can ask `ready(job)` without waiting
on the network: the hearing, the routing cascade and the agents each fall
back on their own when their job is not ready (IS-4).

The states:

- `off`: `[server] enabled` is false; nothing is ever asked.
- `unknown`: nothing has been learned yet.
- `up`: the server answers and `stt`, `route` and `chat` are all ready.
- `degraded`: it answers, and some job is not ready; `reason` says which,
  with the server's own one-line reason.
- `down`: it does not answer, twice in a row; or the NUC has no usable
  token (IS-13); or the server's contract has another major version
  (IS-22). `reason` is the one line the main screen shows.

While the server is up it is asked every `poll_up_seconds`. A failed ask is
tried again after `poll_down_min_seconds`, and each further failure doubles
the wait, up to `poll_down_max_seconds`. Two failures in a row mark the
server down; one healthy answer marks it up again.

`tick` never blocks: the ask runs on a worker thread, through `spawn`, and
its answer is taken on a later tick. The tests give a `spawn` that runs
inline and a fake clock.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

from autobot.config import ServerConfig
from autobot.server.client import ServerUnavailable
from autobot.server.contract import Health, contract_version
from autobot.tui.runner import FunctionTask

OFF = "off"
UNKNOWN = "unknown"
UP = "up"
DEGRADED = "degraded"
DOWN = "down"

# The server's jobs, in the order they are named (IS-11).
JOBS = ("stt", "route", "chat")
# Failures in a row that mark the server down (IS-23).
FAILURES_TO_DOWN = 2


class HealthSource(Protocol):
    """What the watcher asks: the client, or a test fake."""

    def token_trouble(self) -> str: ...

    def health(self) -> Health: ...


def major(version: str) -> str:
    """The major part of a contract version: `"1"` of `"1.2"`."""
    return version.split(".", 1)[0].strip()


class ServerWatch:
    """What the NUC last learned of the server, and when it asks again."""

    def __init__(
        self,
        config: ServerConfig,
        source: HealthSource,
        *,
        clock: Callable[[], float] = time.monotonic,
        spawn: Callable[[Callable[[], object]], FunctionTask] = FunctionTask,
        contract: Callable[[], str] = contract_version,
    ) -> None:
        self._config = config
        self._source = source
        self._clock = clock
        self._spawn = spawn
        self._contract = contract
        self.state = UNKNOWN if config.enabled else OFF
        # Why the server is down or degraded, in one line; empty when up.
        self.reason = ""
        # The last healthy answer; None until there is one, and while down.
        self.health: Health | None = None
        # Why the last ask failed, in one line, even before the server counts
        # as down; empty after a healthy answer.
        self.last_failure = ""
        self._failures = 0
        self._wait = config.poll_down_min_seconds
        self._due = clock()
        self._task: FunctionTask | None = None

    # --- what the rest asks -------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def ready(self, job: str) -> bool:
        """Whether the server can be handed `job` (`stt`, `route` or `chat`) now."""
        if self.state not in (UP, DEGRADED) or self.health is None:
            return False
        return bool(getattr(self.health.jobs, job).ready)

    def model(self, job: str) -> str:
        """The model behind `job`, as the server last named it; empty when unknown."""
        if self.health is None:
            return ""
        return str(getattr(self.health.jobs, job).model)

    def ready_jobs(self) -> tuple[str, ...]:
        """The jobs that are ready, in order."""
        return tuple(job for job in JOBS if self.ready(job))

    # --- the schedule ---------------------------------------------------------------

    def tick(self) -> None:
        """One frame: take a finished ask's answer, or start one when it is due."""
        if not self._config.enabled:
            return
        task = self._task
        if task is not None:
            result = task.poll()
            if result is None:
                return
            self._task = None
            if result.ok and isinstance(task.value, Health):
                self._answered(task.value)
            else:
                self._failed(result.summary())
            return
        if self._clock() >= self._due:
            self._task = self._spawn(self._ask)
            # An inline spawn has its answer already.
            if self._task.finished:
                self.tick()

    def poll_now(self) -> None:
        """Ask at once, and wait for the answer: what `make server-ping` does."""
        if not self._config.enabled:
            return
        try:
            self._answered(self._ask())
        except ServerUnavailable as error:
            self._failed(str(error))

    def _ask(self) -> Health:
        trouble = self._source.token_trouble()
        if trouble:
            raise ServerUnavailable(trouble)
        return self._source.health()

    def _answered(self, health: Health) -> None:
        mine, theirs = self._contract(), health.contract_version
        if major(theirs) != major(mine):
            # Another contract: the server counts as down at once (IS-22).
            self.last_failure = f"contract {theirs} is not {mine}"
            self._down(self.last_failure)
            self._schedule_failure()
            return
        self._failures = 0
        self.last_failure = ""
        self._wait = self._config.poll_down_min_seconds
        self._due = self._clock() + self._config.poll_up_seconds
        self.health = health
        waiting = [
            f"{job} not ready ({getattr(health.jobs, job).reason or 'no reason given'})"
            for job in JOBS
            if not getattr(health.jobs, job).ready
        ]
        self.state = DEGRADED if waiting else UP
        self.reason = "; ".join(waiting)

    def _failed(self, reason: str) -> None:
        self._failures += 1
        self.last_failure = reason
        # A token the NUC cannot use will not get better by asking again.
        no_token = bool(self._source.token_trouble())
        if no_token or self._failures >= FAILURES_TO_DOWN:
            self._down(reason)
        self._schedule_failure()

    def _down(self, reason: str) -> None:
        self.state = DOWN
        self.reason = reason
        self.health = None

    def _schedule_failure(self) -> None:
        self._due = self._clock() + self._wait
        self._wait = min(self._wait * 2, self._config.poll_down_max_seconds)
