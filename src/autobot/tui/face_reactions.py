"""The face's eyes: poses, reactions defined as data, and their animation. No drawing.

`autobot.tui.screens.face` draws what `draw_eyes` returns (FS-5). A
reaction is a short, named animation of the eyes, a list of poses with their
timing, played when something happens (FS-7). Reactions are plain data, so
an experience can carry a set of its own: `reaction_set` builds one from a
mapping like `BUILTIN_REACTIONS`, and `Eyes.play` falls back to the built-in
reaction for any name the experience's set does not define.

A reaction in data is a mapping with these keys:

- `poses`: a list of poses, at least one.
- `loop`: whether it starts again after its last pose. Default false.
- `random`: whether each next pose is drawn at random from the list, never
  the same entry twice running, instead of taken in order. Default false.

A pose in data is a mapping with these keys:

- `pupil`: where the pupils look, as words: one of `left`, `center`,
  `right` and one of `up`, `middle`, `down`, in either order, each
  optional. `"up left"`, `"right"` and `"center"` are poses. Default
  `"center middle"`.
- `lid`: `open`, `wide`, `half` or `closed`. Default `open`.
- `hold`: how long the pose is held, in milliseconds: a whole number, or a
  `[min, max]` pair from which each showing draws one. Required.

A reaction that does not loop holds its last pose for that pose's time and
then hands back to *idle*, the experience's own if its set has one.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from autobot.tui.ui import Glyphs

ACROSS = ("left", "center", "right")
LEVELS = ("up", "middle", "down")
LIDS = ("open", "wide", "half", "closed")

IDLE = "idle"
# The reactions the built-in set defines: one per state the face passes through.
REACTION_NAMES = (
    IDLE,
    "wake",
    "listening",
    "speaking",
    "working",
    "trouble",
    "done",
    "notice",
)

_POSE_KEYS = ("pupil", "lid", "hold")
_REACTION_KEYS = ("poses", "loop", "random")

# A glance and the time it is held, in the idle reaction.
_GLANCE = [800, 3000]
# A blink is short whatever the reaction.
_BLINK = [120, 180]

# The built-in set, as data (FS-7). An experience's set has the same shape.
BUILTIN_REACTIONS: dict[str, dict[str, Any]] = {
    # Looking around: four directions and back to centre, and a blink about
    # once in six poses (there are seven entries, never one twice running).
    IDLE: {
        "loop": True,
        "random": True,
        "poses": [
            {"pupil": "center", "hold": _GLANCE},
            {"pupil": "left", "hold": _GLANCE},
            {"pupil": "center", "hold": _GLANCE},
            {"pupil": "right", "hold": _GLANCE},
            {"pupil": "up", "hold": _GLANCE},
            {"pupil": "down", "hold": _GLANCE},
            {"lid": "closed", "hold": _BLINK},
        ],
    },
    # Something changed that the owner did not ask about, as when the
    # inference server comes or goes (IS-23): a wide look up, then back.
    "notice": {
        "poses": [
            {"pupil": "up", "lid": "wide", "hold": 500},
            {"pupil": "center", "lid": "wide", "hold": 400},
            {"pupil": "center", "hold": 300},
        ],
    },
    # The phrase was heard: eyes wide, straight ahead.
    "wake": {
        "poses": [{"pupil": "center", "lid": "wide", "hold": 1500}],
    },
    # Recording the owner: half-closed, with slow glances to the sides.
    "listening": {
        "loop": True,
        "poses": [
            {"pupil": "center", "lid": "half", "hold": [1200, 1800]},
            {"pupil": "left", "lid": "half", "hold": [1500, 2200]},
            {"pupil": "center", "lid": "half", "hold": [1200, 1800]},
            {"pupil": "right", "lid": "half", "hold": [1500, 2200]},
        ],
    },
    # autobot speaks: looking at the owner, with a short glance now and then.
    "speaking": {
        "loop": True,
        "poses": [
            {"pupil": "center", "hold": [900, 1600]},
            {"pupil": "left", "hold": [300, 500]},
            {"pupil": "center", "hold": [900, 1600]},
            {"pupil": "right", "hold": [300, 500]},
        ],
    },
    # The agent is processing: looking up, from one side to the other.
    "working": {
        "loop": True,
        "poses": [
            {"pupil": "up left", "hold": [600, 900]},
            {"pupil": "up right", "hold": [600, 900]},
        ],
    },
    # Something went wrong: half-closed, looking down.
    "trouble": {
        "poses": [{"pupil": "down", "lid": "half", "hold": 3000}],
    },
    # The run is over: a blink, then straight ahead.
    "done": {
        "poses": [
            {"pupil": "center", "lid": "closed", "hold": _BLINK},
            {"pupil": "center", "hold": 1200},
        ],
    },
}


class ReactionError(Exception):
    """A reaction set's data is not one the face can play. The message is one line."""


@dataclass(frozen=True)
class Pose:
    """Where the pupils look, how open the lids are, and for how long."""

    across: str = "center"
    level: str = "middle"
    lid: str = "open"
    # Milliseconds: the pose is held for a time drawn from this range.
    hold: tuple[int, int] = (1000, 1000)

    def seconds(self, chance: random.Random) -> float:
        """How long this showing of the pose lasts."""
        low, high = self.hold
        return (low if low == high else chance.randint(low, high)) / 1000


@dataclass(frozen=True)
class Reaction:
    """A named animation of the eyes: its poses, and how they are played."""

    poses: tuple[Pose, ...]
    loop: bool = False
    random: bool = False


# A reaction set: reactions by name.
type ReactionSet = Mapping[str, Reaction]


def _pupil(words: Any, where: str) -> tuple[str, str]:
    if not isinstance(words, str):
        raise ReactionError(f"{where}: the pupil is words, not {words!r}")
    across = level = None
    for word in words.split():
        if word in ACROSS and across is None:
            across = word
        elif word in LEVELS and level is None:
            level = word
        else:
            raise ReactionError(
                f"{where}: {word!r} is not a pupil word "
                f"(one of {', '.join(ACROSS)}; one of {', '.join(LEVELS)})"
            )
    return across or "center", level or "middle"


def _whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _hold(value: Any, where: str) -> tuple[int, int]:
    if _whole(value):
        return value, value
    if (
        isinstance(value, list | tuple)
        and len(value) == 2
        and all(_whole(part) for part in value)
        and value[0] <= value[1]
    ):
        return value[0], value[1]
    raise ReactionError(
        f"{where}: the hold is milliseconds, a whole number or [min, max], "
        f"not {value!r}"
    )


def _pose(data: Any, where: str) -> Pose:
    if not isinstance(data, Mapping):
        raise ReactionError(f"{where}: a pose is a mapping, not {data!r}")
    for key in data:
        if key not in _POSE_KEYS:
            raise ReactionError(
                f"{where}: {key!r} is not a pose key ({', '.join(_POSE_KEYS)})"
            )
    if "hold" not in data:
        raise ReactionError(f"{where}: the pose has no hold")
    across, level = _pupil(data.get("pupil", "center"), where)
    lid = data.get("lid", "open")
    if lid not in LIDS:
        raise ReactionError(f"{where}: {lid!r} is not a lid word ({', '.join(LIDS)})")
    return Pose(across, level, lid, _hold(data["hold"], where))


def _flag(data: Mapping[str, Any], key: str, where: str) -> bool:
    value = data.get(key, False)
    if not isinstance(value, bool):
        raise ReactionError(f"{where}: {key} is true or false, not {value!r}")
    return value


def reaction_set(mapping: Mapping[str, Any]) -> dict[str, Reaction]:
    """The reactions `mapping` defines, by name, or `ReactionError`."""
    reactions: dict[str, Reaction] = {}
    for name, data in mapping.items():
        where = f"reaction {name!r}"
        if not isinstance(data, Mapping):
            raise ReactionError(f"{where}: a reaction is a mapping, not {data!r}")
        for key in data:
            if key not in _REACTION_KEYS:
                raise ReactionError(
                    f"{where}: {key!r} is not a reaction key "
                    f"({', '.join(_REACTION_KEYS)})"
                )
        poses = data.get("poses")
        if not isinstance(poses, list | tuple) or not poses:
            raise ReactionError(f"{where}: it needs a list of poses")
        reactions[str(name)] = Reaction(
            tuple(
                _pose(pose, f"{where}, pose {number}")
                for number, pose in enumerate(poses, start=1)
            ),
            loop=_flag(data, "loop", where),
            random=_flag(data, "random", where),
        )
    return reactions


BUILTIN_SET = reaction_set(BUILTIN_REACTIONS)


class Eyes:
    """The eyes' animator: which reaction plays, and which of its poses is on show.

    It is driven by `pose()`, called once a frame; nothing runs in between.
    The clock is in seconds, `time.monotonic` in the interface and a fake in
    the tests, as is the `random.Random` the random reactions draw from.
    """

    def __init__(
        self, clock: Callable[[], float], chance: random.Random | None = None
    ) -> None:
        self._clock = clock
        self._chance = chance or random.Random()
        self._set: ReactionSet = BUILTIN_SET
        self.name = IDLE
        self._reaction = BUILTIN_SET[IDLE]
        self._index = 0
        self._shown = 1
        self._until = 0.0
        self.play(IDLE)

    def _find(self, name: str, reactions: ReactionSet) -> tuple[str, Reaction]:
        if name in reactions:
            return name, reactions[name]
        if name in BUILTIN_SET:
            return name, BUILTIN_SET[name]
        # A name neither set defines: the eyes carry on looking around.
        return IDLE, reactions.get(IDLE, BUILTIN_SET[IDLE])

    def play(self, name: str, reactions: ReactionSet | None = None) -> None:
        """Start the reaction `name` from its first pose, at once.

        `reactions` is the running experience's set; a name it does not
        define is played from the built-in set.
        """
        self._set = reactions if reactions is not None else BUILTIN_SET
        self.name, self._reaction = self._find(name, self._set)
        self._show(0, self._clock())
        self._shown = 1

    def _show(self, index: int, now: float) -> None:
        self._index = index
        self._until = now + self._reaction.poses[index].seconds(self._chance)

    def _next_index(self) -> int:
        count = len(self._reaction.poses)
        if self._reaction.random and count > 1:
            # Any entry but the one on show.
            step = self._chance.randrange(1, count)
            return (self._index + step) % count
        return (self._index + 1) % count

    def pose(self) -> Pose:
        """The pose on show now, after moving on from any whose time is up."""
        now = self._clock()
        if now >= self._until:
            count = len(self._reaction.poses)
            if not self._reaction.loop and self._shown >= count:
                self.play(IDLE, self._set)
            else:
                self._show(self._next_index(), now)
                self._shown += 1
        return self._reaction.poses[self._index]


# --- drawing as text ----------------------------------------------------------

# Columns between the two eyes.
EYE_GAP = 3
# Each eye is this wide and this tall: a frame round three columns, two rows.
EYE_WIDTH = 5
EYE_ROWS = 4

# The pupil's two interior cells, top and bottom, by lid and level. An open
# pupil is one cell tall, a wide one (more white round it) half as tall; a
# half-closed lid leaves only the bottom row to look through.
_UNICODE_PUPILS: dict[str, dict[str, tuple[str, str]]] = {
    "open": {"up": ("█", " "), "middle": ("▄", "▀"), "down": (" ", "█")},
    "wide": {"up": ("▀", " "), "middle": ("▄", " "), "down": (" ", "▄")},
    "half": {"up": (" ", "▀"), "middle": (" ", "█"), "down": (" ", "▄")},
}
_ASCII_PUPILS: dict[str, dict[str, tuple[str, str]]] = {
    "open": {"up": ("o", " "), "middle": (".", "'"), "down": (" ", "o")},
    "wide": {"up": ("'", " "), "middle": (".", " "), "down": (" ", ".")},
    "half": {"up": (" ", "'"), "middle": (" ", "o"), "down": (" ", ".")},
}


@dataclass(frozen=True)
class _Strokes:
    top: str
    bottom: str
    side_left: str
    side_right: str
    lid: str
    closed: str


_UNICODE_STROKES = _Strokes("╭───╮", "╰───╯", "│", "│", "───", "╰───╯")
_ASCII_STROKES = _Strokes(".---.", "'---'", "(", ")", "---", "'---'")


def _eye(
    pose: Pose, strokes: _Strokes, pupils: dict[str, dict[str, tuple[str, str]]]
) -> list[str]:
    blank = " " * EYE_WIDTH
    if pose.lid == "closed":
        return [blank, blank, strokes.closed, blank]
    top, bottom = pupils[pose.lid][pose.level]
    column = ACROSS.index(pose.across)
    rows = []
    for cell in (top, bottom):
        inside = [" ", " ", " "]
        inside[column] = cell
        rows.append(strokes.side_left + "".join(inside) + strokes.side_right)
    if pose.lid == "half":
        rows[0] = strokes.side_left + strokes.lid + strokes.side_right
    return [strokes.top, *rows, strokes.bottom]


def draw_eyes(pose: Pose, glyphs: Glyphs) -> list[str]:
    """Two eyes in `pose`, as `EYE_ROWS` lines of the same width.

    Rounded line-drawing characters where `glyphs.block_art` is set, plain
    ASCII otherwise. There is nothing but the eyes: no brows, no other
    feature (FS-5).
    """
    if glyphs.block_art:
        eye = _eye(pose, _UNICODE_STROKES, _UNICODE_PUPILS)
    else:
        eye = _eye(pose, _ASCII_STROKES, _ASCII_PUPILS)
    gap = " " * EYE_GAP
    return [row + gap + row for row in eye]
