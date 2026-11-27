"""The inference server's state, as the main screen and the face show it (IS-23). No drawing.

`describe` puts what the watcher last learned (`autobot.server.watch`) in
one line, shown beside the listener's state:

    server: up (stt, route, chat)
    server: degraded: chat not ready (model missing)
    server: down: no server token: run make server-token
    server: down: contract 2 is not 1
    server: looking
    server: off

`badge` is the short word of the badge on every screen's top line, and
`BADGE_ROLES` the `Theme` role each state's badge is drawn in.

`ServerNotice` is how the face reacts when the server comes or goes: it
remembers whether the server could be reached and answers the ticker line
for a change, "server up" or "server away", once per change. Learning the
state for the first time is not a change.
"""

from __future__ import annotations

from autobot.server.watch import DEGRADED, DOWN, OFF, UNKNOWN, UP, ServerWatch

# What the line says while nothing has been learned yet.
LOOKING = "looking"
# The ticker's lines when the server comes and goes (IS-23).
NOTICE_UP = "server up"
NOTICE_AWAY = "server away"

# The `Theme` role each state's badge is drawn in, beside the listener's.
BADGE_ROLES: dict[str, str] = {
    UP: "badge_ok",
    DEGRADED: "badge_warning",
    DOWN: "badge_error",
    UNKNOWN: "badge_info",
    OFF: "badge_info",
}
# The role the line itself is drawn in on the main screen.
LINE_ROLES: dict[str, str] = {
    UP: "ok",
    DEGRADED: "warning",
    DOWN: "error",
    UNKNOWN: "dim",
    OFF: "dim",
}


def summary(watch: ServerWatch) -> str:
    """The state and what goes with it, without the `server:` label."""
    if watch.state == UP:
        return f"{UP} ({', '.join(watch.ready_jobs())})"
    if watch.state in (DEGRADED, DOWN):
        return f"{watch.state}: {watch.reason}" if watch.reason else watch.state
    if watch.state == OFF:
        return OFF
    return LOOKING


def describe(watch: ServerWatch) -> str:
    """The main screen's line for the server."""
    return f"server: {summary(watch)}"


def badge(watch: ServerWatch) -> str:
    """The badge on every screen's top line: `SERVER UP`, `SERVER DOWN`."""
    word = LOOKING if watch.state == UNKNOWN else watch.state
    return f"SERVER {word.upper()}"


def reachable(watch: ServerWatch) -> bool | None:
    """Whether the server answers; None while that is not known, or it is off."""
    if watch.state in (UP, DEGRADED):
        return True
    if watch.state == DOWN:
        return False
    return None


class ServerNotice:
    """Tells a change in whether the server answers, once per change."""

    def __init__(self) -> None:
        self._reachable: bool | None = None

    def changed(self, watch: ServerWatch) -> str:
        """The ticker line when the server has come or gone since last asked; else empty."""
        now = reachable(watch)
        if now is None:
            # Off, or not known yet: what was last known stands.
            return ""
        before, self._reachable = self._reachable, now
        if before is None or before == now:
            # Learned for the first time, or the same as before.
            return ""
        return NOTICE_UP if now else NOTICE_AWAY
