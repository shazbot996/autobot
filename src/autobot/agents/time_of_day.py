"""The `time` agent: it says the time from this computer's clock (AG-6). No drawing.

It reads the wall clock the face was given (`Seams.wall_clock`, the NUC's
own clock in the interface, a fixed one in the tests) and answers in two
parts: the time in a spoken sentence, and the date, the time to the second
and the time zone in the detail panel. It reaches nothing outside this
machine.
"""

from __future__ import annotations

from datetime import datetime

from autobot.agents.base import Launch
from autobot.agents.response import Response
from autobot.agents.speech import Reply
from autobot.tui.face import FaceContext
from autobot.tui.face_reactions import ReactionSet


def part_of_day(hour: int) -> str:
    """How the hour is said: `in the morning`, `in the afternoon`, `in the evening`, `at night`."""
    if 5 <= hour < 12:
        return "in the morning"
    if 12 <= hour < 18:
        return "in the afternoon"
    if 18 <= hour < 21:
        return "in the evening"
    return "at night"


def spoken_time(now: datetime) -> str:
    """The time as autobot says it: `It's 3:42 in the afternoon.`"""
    if now.minute == 0 and now.hour in (0, 12):
        return "It's midnight." if now.hour == 0 else "It's noon."
    hour = now.hour % 12 or 12
    clock = f"{hour} o'clock" if now.minute == 0 else f"{hour}:{now.minute:02d}"
    return f"It's {clock} {part_of_day(now.hour)}."


def time_response(now: datetime) -> Response:
    """The two parts: the spoken time, and the date, the seconds and the time zone."""
    zone = now.astimezone().tzname() or "local time"
    return Response(
        spoken_time(now),
        (
            f"{now:%A}, {now:%B} {now.day}, {now.year}",
            f"{now:%H:%M:%S}",
            f"Time zone: {zone}",
        ),
    )


class TimeOfDay:
    """Say the time once, then end."""

    def __init__(self, launch: Launch) -> None:
        self._reply: Reply | None = None
        self._done = False

    @property
    def reactions(self) -> ReactionSet | None:
        return None

    @property
    def done(self) -> bool:
        return self._done

    def start(self, face: FaceContext) -> None:
        self._reply = Reply(face, time_response(face.seams.wall_clock()))
        self.tick(face)

    def tick(self, face: FaceContext) -> None:
        if self._done or self._reply is None:
            return
        self._reply.tick(face)
        if self._reply.done:
            face.react("done")
            self._done = True
