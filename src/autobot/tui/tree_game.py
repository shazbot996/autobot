"""The face's game screen in motion: where the protagonist stands on the map. No drawing.

While no agent runs, the face's detail panel is a game screen (AG-3): the
agent tree (`autobot.agents.tree`) is the map, and the protagonist, a
mini-autobot with the face's eyes, stands at the panel's centre while the
map slides under him.

- `Position` is where the session stands: its level of the tree, the agent
  it reached, and its phase. The router publishes one each frame.
- `Game` follows the positions it is given. When the place a position
  names changes, the protagonist travels there along the tree's lines, one
  cell at a time, in `TRAVEL_SECONDS` whatever the distance, interpolated
  from the clock each frame. A change in the middle of a travel starts the
  next one from the cell he stands on, so he never jumps.
- `Game.offset` is the cell of the map at the panel's top-left that puts
  the protagonist's cell at the panel's centre.

The game reads the positions the session has already reached and nothing
waits on it: it neither sleeps nor holds the session up, and a slow frame
only shortens the animation (AG-3, AG-12). `autobot.tui.screens.face`
draws it.
"""

from __future__ import annotations

from dataclasses import dataclass

from autobot.agents.tree import LEVEL_AGENTS, LEVEL_ROOT, Cell, Node, Picture
from autobot.tui.face_reactions import ACROSS, Pose
from autobot.tui.ui import Glyphs

# The phases of a session, as the game screen follows them (AG-3).
PHASE_LISTENING = "listening"
PHASE_WAKE = "wake"
PHASE_COMMAND = "command"
PHASE_CONFIRMING = "confirming"
PHASE_RUNNING = "running"
PHASE_DONE = "done"
PHASES = (
    PHASE_LISTENING,
    PHASE_WAKE,
    PHASE_COMMAND,
    PHASE_CONFIRMING,
    PHASE_RUNNING,
    PHASE_DONE,
)

# How long one travel takes, in seconds: well under a second (AG-3).
TRAVEL_SECONDS = 0.4
# Floating-point slack when the clock reaches the end of a travel.
_EPSILON = 1e-9


@dataclass(frozen=True)
class Position:
    """Where the session stands on the tree."""

    # `root` or `agents`, from `autobot.agents.tree.LEVELS`.
    level: str = LEVEL_ROOT
    # The agent the command reached; empty while listening for one.
    node: str = ""
    # One of `PHASES`.
    phase: str = PHASE_LISTENING
    # The tier of the routing cascade that decided the command (IS-17):
    # 1, 2 or 3; 0 while none has, and for the core agent, which no tier chose.
    tier: int = 0


class Game:
    """The protagonist's travels over the map, as positions come in.

    The clock is in seconds, `time.monotonic` in the interface and a fake
    in the tests; every call that depends on it is given `now`.
    """

    def __init__(self, picture: Picture) -> None:
        self.picture = picture
        self.position = Position()
        self._target: Node | None = picture.tree.root
        root = picture.centre(picture.tree.root)
        # The cells from the root to where the travel started: the way back.
        self._trail: tuple[Cell, ...] = (root,)
        self._route: tuple[Cell, ...] = (root,)
        self._started = 0.0

    @property
    def route(self) -> tuple[Cell, ...]:
        """The cells of the current travel, from where it started to its end."""
        return self._route

    def follow(self, position: Position, now: float) -> None:
        """Take the session's newest position; travel when its place changed."""
        self.position = position
        target = self._target_of(position)
        if target == self._target:
            return
        index = self._index(now)
        trail = _loopless((*self._trail, *self._route[1 : index + 1]))
        self._route = _loopless((*reversed(trail), *self._down_to(target)[1:]))
        self._trail = trail
        self._target = target
        self._started = now

    def cell(self, now: float) -> Cell:
        """The map cell the protagonist stands on at `now`."""
        return self._route[self._index(now)]

    def arrived(self, now: float) -> bool:
        """Whether the current travel has ended."""
        return self._progress(now) >= 1.0

    def offset(self, now: float, rows: int, cols: int) -> Cell:
        """The map cell at the top-left of a panel `rows` by `cols`.

        It puts the protagonist's cell at the panel's centre; it may be
        negative, or past the map, where the map does not fill the panel.
        """
        row, col = self.cell(now)
        return (row - rows // 2, col - cols // 2)

    def highlight(self, now: float) -> str:
        """The name of the node under the protagonist once he arrives; empty otherwise."""
        if self._target is None or not self.arrived(now):
            return ""
        return self._target.name

    def tier_mark(self) -> str:
        """The mark on the edge into the node the command reached: `tier 2`; else empty."""
        position = self.position
        if not position.tier or not position.node or position.phase == PHASE_DONE:
            return ""
        return f"tier {position.tier}"

    def level_view(self, now: float) -> bool:
        """Whether the face shows the agent's running view instead of the map.

        It is once the agent runs and the protagonist has reached its node,
        so the slide to the node is seen before the level opens.
        """
        return self.position.phase == PHASE_RUNNING and self.arrived(now)

    def _target_of(self, position: Position) -> Node | None:
        """The node `position` stands on: None for the agents' level before a command."""
        tree = self.picture.tree
        if position.level == LEVEL_ROOT or position.phase in (
            PHASE_LISTENING,
            PHASE_DONE,
        ):
            return tree.root
        if position.level == LEVEL_AGENTS and position.node:
            try:
                node = tree.node(position.node)
            except KeyError:
                return None
            return None if node.root else node
        return None

    def _down_to(self, target: Node | None) -> tuple[Cell, ...]:
        """The cells from the root's centre down to `target`'s place."""
        picture = self.picture
        root = picture.tree.root
        if target is not None:
            return picture.path(root, target)
        # The agents' level before a command: straight down from the root.
        top_row, col = picture.centre(root)
        agents = picture.tree.agents
        bottom_row = picture.centre(agents[0])[0] if agents else top_row
        return tuple((row, col) for row in range(top_row, bottom_row + 1))

    def _progress(self, now: float) -> float:
        if len(self._route) == 1:
            return 1.0
        progress = (now - self._started) / TRAVEL_SECONDS
        return 1.0 if progress >= 1.0 - _EPSILON else max(0.0, progress)

    def _index(self, now: float) -> int:
        return int(self._progress(now) * (len(self._route) - 1))


def _loopless(cells: tuple[Cell, ...]) -> tuple[Cell, ...]:
    """`cells` with every loop cut out: a cell seen again goes back to its first visit."""
    kept: list[Cell] = []
    seen: dict[Cell, int] = {}
    for cell in cells:
        if cell in seen:
            index = seen[cell]
            for dropped in kept[index + 1 :]:
                del seen[dropped]
            del kept[index + 1 :]
            continue
        seen[cell] = len(kept)
        kept.append(cell)
    return tuple(kept)


# --- the protagonist as text --------------------------------------------------

# Columns of the protagonist: his two eyes side by side, with a column of
# room each side for them to look into.
PROTAGONIST_WIDTH = 8
PROTAGONIST_ROWS = 2

# The pupil in its one cell, by lid and level; a closed eye is a line.
_ASCII_PUPILS = {
    "open": {"up": "'", "middle": "o", "down": "."},
    "wide": {"up": "O", "middle": "O", "down": "O"},
    "half": {"up": "_", "middle": "_", "down": "_"},
    "closed": {"up": "-", "middle": "-", "down": "-"},
}
_UNICODE_PUPILS = {
    "open": {"up": "▀", "middle": "█", "down": "▄"},
    "wide": {"up": "●", "middle": "●", "down": "●"},
    "half": {"up": "▄", "middle": "▄", "down": "▄"},
    "closed": {"up": "─", "middle": "─", "down": "─"},
}


def draw_protagonist(pose: Pose, glyphs: Glyphs) -> list[str]:
    """The mini-autobot in `pose`: two eyes, `PROTAGONIST_ROWS` lines of the same width.

    They are the face's eyes made small (AG-3, FS-5): rounded line-drawing
    strokes where `glyphs.block_art` is set, `(o)(o)` in plain ASCII
    otherwise. The pair looks the way `pose.across` says by standing a
    column to that side.
    """
    if glyphs.block_art:
        top, left, right = "╭─╮", "│", "│"
        pupil = _UNICODE_PUPILS[pose.lid][pose.level]
    else:
        top, left, right = ".-.", "(", ")"
        pupil = _ASCII_PUPILS[pose.lid][pose.level]
    eye = left + pupil + right
    shift = ACROSS.index(pose.across)
    rows = [top * 2, eye * 2]
    return [(" " * shift + row).ljust(PROTAGONIST_WIDTH) for row in rows]
