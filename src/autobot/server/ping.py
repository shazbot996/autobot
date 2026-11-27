"""`make server-ping`: ask the inference server once, from the NUC (IS-25).

    python -m autobot.server.ping

One `/health` call to `[server] url` with the NUC's token, printed as the
watcher reads it (`autobot.server.watch`): `up` and the contract version,
`degraded` with the jobs that are not ready, or `down` with the one-line
reason, such as a missing token or a contract of another version. The exit
status is 0 when the server is up or degraded and 1 otherwise.

It is one request and waits at most `[server] health_timeout`. Nothing is
written, and the token is never printed.
"""

from __future__ import annotations

import sys

from autobot.config import ConfigError, ServerConfig, load_config
from autobot.server.client import ServerClient, Transport
from autobot.server.watch import DEGRADED, DOWN, JOBS, OFF, UP, ServerWatch


def ping(config: ServerConfig, transport: Transport | None = None) -> ServerWatch:
    """The watcher after one ask of the server, made now."""
    watch = ServerWatch(config, ServerClient(config, transport))
    watch.poll_now()
    return watch


def describe(config: ServerConfig, watch: ServerWatch) -> list[str]:
    """What one ask learned, as lines: the state first."""
    if watch.state == OFF:
        return ["off: [server] enabled is false", f"server: {config.url}"]
    health = watch.health
    if health is None:
        # One failed ask is the answer here; the watcher waits for a second.
        why = watch.last_failure or "the server did not answer"
        return [f"{DOWN}: {why}", f"server: {config.url}"]
    versions = f"version {health.server_version}, contract {health.contract_version}"
    lines = [
        f"{watch.state}{': ' + watch.reason if watch.reason else ''}",
        f"server: {config.url}, {versions}",
    ]
    for job in JOBS:
        state = getattr(health.jobs, job)
        ready = "ready" if state.ready else f"not ready: {state.reason}"
        lines.append(f"  {job:<6}{state.model}: {ready}")
    lines.append(f"gpu: {health.gpu.used_mib} MiB used, {health.gpu.free_mib} MiB free")
    return lines


def main(transport: Transport | None = None) -> int:
    try:
        config = load_config().server
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 1
    watch = ping(config, transport)
    for line in describe(config, watch):
        print(line)
    return 0 if watch.state in (UP, DEGRADED) else 1


if __name__ == "__main__":
    raise SystemExit(main())
