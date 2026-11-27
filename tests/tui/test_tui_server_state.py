"""The inference server's state as the interface words it, and the face's notice (IS-23)."""

from __future__ import annotations

from pathlib import Path

from agents.agent_fakes import AgentRig
from autobot.server.watch import DEGRADED, DOWN, OFF, UNKNOWN, UP
from autobot.tui.face import Seams
from autobot.tui.face_reactions import BUILTIN_SET, REACTION_NAMES
from autobot.tui.face_ticker import GAP, Ticker
from autobot.tui.server_state import (
    BADGE_ROLES,
    LINE_ROLES,
    NOTICE_AWAY,
    NOTICE_UP,
    ServerNotice,
    badge,
    describe,
    reachable,
)
from autobot.tui.ui import Theme
from server.server_fakes import FakeWatch


def test_the_line_says_the_state_and_its_one_reason() -> None:
    watch = FakeWatch()
    assert describe(watch) == "server: up (stt, route, chat)"
    watch.set(DEGRADED, chat="model missing")
    assert describe(watch) == "server: degraded: chat not ready (model missing)"
    watch.set(DOWN, "no server token: run make server-token")
    assert describe(watch) == "server: down: no server token: run make server-token"
    watch.set(DOWN, "contract 2 is not 1")
    assert describe(watch) == "server: down: contract 2 is not 1"
    watch.state, watch.reason = UNKNOWN, ""
    assert describe(watch) == "server: looking"
    watch.state = OFF
    assert describe(watch) == "server: off"


def test_every_state_has_a_badge_and_a_role_the_theme_knows() -> None:
    theme = Theme.mono()
    watch = FakeWatch()
    seen = []
    for state in (UP, DEGRADED, DOWN, UNKNOWN, OFF):
        watch.state = state
        seen.append(badge(watch))
        assert isinstance(getattr(theme, BADGE_ROLES[state]), int)
        assert isinstance(getattr(theme, LINE_ROLES[state]), int)
    assert seen == [
        "SERVER UP",
        "SERVER DEGRADED",
        "SERVER DOWN",
        "SERVER LOOKING",
        "SERVER OFF",
    ]


def test_a_notice_is_told_once_for_each_coming_and_going() -> None:
    watch = FakeWatch(DOWN)
    notice = ServerNotice()
    # Learning the state for the first time is not a change.
    assert notice.changed(watch) == ""
    assert notice.changed(watch) == ""
    watch.set(UP)
    assert notice.changed(watch) == NOTICE_UP == "server up"
    assert notice.changed(watch) == ""
    # Degraded still answers: the server has not gone.
    watch.set(DEGRADED, chat="loading")
    assert notice.changed(watch) == ""
    watch.set(DOWN)
    assert notice.changed(watch) == NOTICE_AWAY == "server away"
    # While it is off or not known, what was last known stands.
    watch.state = UNKNOWN
    assert (reachable(watch), notice.changed(watch)) == (None, "")
    watch.set(DOWN)
    assert notice.changed(watch) == ""
    watch.set(UP)
    assert notice.changed(watch) == NOTICE_UP


def test_the_ticker_takes_a_line_of_the_faces_own_even_while_muted() -> None:
    ticker = Ticker()
    ticker.muted = True
    ticker.note("  server   up ")
    ticker.note("")
    ticker.note("server away")
    assert ticker.text == "server up" + " " * GAP + "server away"


def test_notice_is_a_built_in_reaction() -> None:
    assert "notice" in REACTION_NAMES
    assert set(REACTION_NAMES) == set(BUILTIN_SET)
    assert not BUILTIN_SET["notice"].loop


def test_the_face_reacts_when_the_server_comes_or_goes(tmp_path: Path) -> None:
    watch = FakeWatch(DOWN)
    rig = AgentRig(tmp_path, seams=Seams(watch=watch))
    rig.frames(0.3)
    assert rig.model.ticker.text == ""
    assert rig.model.eyes.name == "idle"
    watch.set(UP)
    rig.frames(0.1)
    assert rig.model.ticker.text == "server up"
    assert rig.model.eyes.name == "notice"
    # The notice is short: the eyes go back to looking around.
    rig.frames(3.0)
    assert rig.model.eyes.name == "idle"
    watch.set(DOWN)
    rig.frames(0.1)
    assert rig.model.ticker.text == "server up" + " " * GAP + "server away"
    assert rig.model.eyes.name == "notice"


def test_a_face_given_no_watcher_notices_nothing(tmp_path: Path) -> None:
    rig = AgentRig(tmp_path)
    rig.frames(0.3)
    assert rig.model.ticker.text == ""
