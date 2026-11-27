"""The agent tree: the root, the agents, the commands between them (AG-2). No drawing.

The tree is one model, built from `config/autobot.toml`, and drawn by both
the face's game screen (AG-3) and the Agent editor (AG-9):

- the **root** is the first wake phrase, `autobot`;
- under it stands one **node** per agent, in the order of the agent's first
  rule in `[[routes]]`;
- each rule is an **edge** from the root to its agent, labelled with its
  command, and with its mode in brackets when it carries one (AG-7), so two
  rules to one agent are two edges into one node: aliases;
- a node carries a mark for each confirmation its definition turns on: `?`
  for the one on the way in, `$?` for the one before a request (AG-13);
- the global action keywords, `[words] actions`, stand beside both levels,
  since they can be said at either (AG-14).

`layout` turns the tree into a `Picture`: the text of a top-down flowchart in
the theme's line-drawing characters, or in plain ASCII, with the place of
every box, label and keyword list, so that a screen colours the node the
protagonist stands on, or the one selected, without reading the text back.
The picture is as wide as the agents need; a screen narrower than that
scrolls it, it never wraps.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from autobot.agents.registry import AGENTS
from autobot.config import Config
from autobot.tui.ui import Glyphs, Rect

# The levels of the tree, top-down: the wake phrase, then the agents.
LEVEL_ROOT = "root"
LEVEL_AGENTS = "agents"
LEVELS = (LEVEL_ROOT, LEVEL_AGENTS)

# The mark on a node for its confirmation on the way in, and before a request.
MARK_ENTRY = "?"
MARK_REQUEST = "$?"

# Columns between two agents' places on their level.
GAP = 2
# Columns between the widest level and the keywords beside it.
KEYWORD_GAP = 3

# The rows of the picture, top-down.
_ROOT_TOP = 0
_ROOT_NAME = 1
_ROOT_BOTTOM = 2
_BUS = 4
_LABEL = 6
_MERGE = 8
_AGENT_TOP = 9
_AGENT_NAME = 10
_AGENT_BOTTOM = 11
_HEIGHT = 12

# The joints of the line-drawing set that `Glyphs` does not carry, by the
# directions a joint connects: (up, down, left, right).
_UNICODE_TEES = {
    (False, True, True, True): "┬",
    (True, False, True, True): "┴",
    (True, True, True, True): "┼",
    (True, True, False, True): "├",
    (True, True, True, False): "┤",
}

type Cell = tuple[int, int]
# Writes text into the picture at (row, column).
type _Putter = Callable[[int, int, str], None]
# Writes the joint at (row, column) that connects up, down, left and right.
type _JointDrawer = Callable[[int, int, bool, bool, bool, bool], None]


@dataclass(frozen=True)
class Node:
    """One box of the tree: the root, or an agent."""

    # The wake phrase for the root; the agent's name otherwise.
    name: str
    root: bool = False
    # The confirmations its definition turns on (AG-13).
    confirm_entry: bool = False
    confirm_request: bool = False
    # Whether a language model stands behind the agent, from the registry.
    uses_model: bool = False

    @property
    def marks(self) -> str:
        """The confirmation marks drawn on the box's top edge."""
        marks = [MARK_ENTRY] if self.confirm_entry else []
        if self.confirm_request:
            marks.append(MARK_REQUEST)
        return " ".join(marks)


@dataclass(frozen=True)
class Edge:
    """One rule: the command that leads from the root to `node` (AG-2, AG-7)."""

    command: str
    mode: str
    node: Node

    @property
    def label(self) -> str:
        """What is written on the edge: the command, and its mode when it has one."""
        return f"{self.command} ({self.mode})" if self.mode else self.command


@dataclass(frozen=True)
class Tree:
    """The root, the agents in rule order, an edge per rule, and the keywords."""

    root: Node
    agents: tuple[Node, ...]
    edges: tuple[Edge, ...]
    # The global action keywords, shown beside both levels (AG-14).
    keywords: tuple[str, ...]

    @property
    def levels(self) -> tuple[tuple[Node, ...], ...]:
        """The rows of nodes, top-down, one per name of `LEVELS`."""
        return ((self.root,), self.agents)

    def node(self, name: str) -> Node:
        """The agent node `name`, or the root by its wake phrase; KeyError otherwise."""
        for node in (self.root, *self.agents):
            if node.name == name:
                return node
        raise KeyError(name)

    def edges_to(self, node: Node) -> tuple[Edge, ...]:
        """The edges into `node`, in rule order; more than one makes aliases."""
        return tuple(edge for edge in self.edges if edge.node == node)


def build_tree(config: Config) -> Tree:
    """The tree of `config`: its first wake phrase, its rules, its agents, its words."""
    root = Node(config.wake_phrases[0].phrase, root=True)
    nodes: dict[str, Node] = {}
    edges: list[Edge] = []
    for route in config.routes:
        node = nodes.get(route.agent)
        if node is None:
            definition = config.agent(route.agent)
            agent = AGENTS.get(route.agent)
            node = nodes[route.agent] = Node(
                route.agent,
                confirm_entry=definition.confirm_entry,
                confirm_request=definition.confirm_request,
                uses_model=agent.uses_model if agent is not None else False,
            )
        edges.append(Edge(route.command, route.mode, node))
    return Tree(root, tuple(nodes.values()), tuple(edges), config.words.actions)


@dataclass(frozen=True)
class Picture:
    """The tree drawn as text, and where each of its parts stands in it."""

    tree: Tree
    # The rows of text, all of the picture's width.
    rows: list[str]
    # Each node's box, frame included.
    boxes: dict[Node, Rect]
    # Where each edge's label starts: (row, column).
    labels: dict[Edge, Cell]
    # Where the keywords beside each level start, by the name of the level.
    keywords: dict[str, Cell]
    # The cells of the line from the root's centre to each agent's centre,
    # through the agent's first edge.
    paths: dict[Node, tuple[Cell, ...]] = field(default_factory=dict)

    @property
    def width(self) -> int:
        return len(self.rows[0]) if self.rows else 0

    @property
    def height(self) -> int:
        return len(self.rows)

    def centre(self, node: Node) -> Cell:
        """The cell at the centre of `node`'s box: its name's row, its middle column."""
        box = self.boxes[node]
        return (box.y + box.height // 2, _middle(box.x, box.width))

    def path(self, a: Node, b: Node) -> tuple[Cell, ...]:
        """The cells from `a`'s centre to `b`'s, one step at a time, both ends included.

        Between the root and an agent it is the line of the agent's first
        edge; between two agents it goes up through the root.
        """
        if a == b:
            return (self.centre(a),)
        if a.root:
            return self.paths[b]
        if b.root:
            return tuple(reversed(self.paths[a]))
        up = self.path(a, self.tree.root)
        return up + self.path(self.tree.root, b)[1:]


def layout(tree: Tree, glyphs: Glyphs) -> Picture:
    """The tree as a top-down flowchart in `glyphs`: the root over the agents."""
    plain = glyphs.rule.isascii()
    places = [_Place.of(node, tree.edges_to(node)) for node in tree.agents]
    agents_width = sum(place.width for place in places) + GAP * max(0, len(places) - 1)
    root_width = _box_width(tree.root)
    width = max(agents_width, root_width)
    x = (width - agents_width) // 2
    for place in places:
        place.at(x)
        x += place.width + GAP
    drops = [drop for place in places for drop in place.drops]
    root_middle = (drops[0] + drops[-1]) // 2 if drops else _middle(0, width)
    root_box = Rect(
        _ROOT_TOP, max(0, root_middle - (root_width - 1) // 2), 3, root_width
    )

    keyword_text = " ".join(f"[{word}]" for word in tree.keywords)
    keyword_col = max(width, root_box.right) + KEYWORD_GAP
    total = (
        keyword_col + len(keyword_text) if keyword_text else max(width, root_box.right)
    )
    grid = [[" "] * total for _ in range(_HEIGHT if places else _ROOT_BOTTOM + 1)]

    def put(row: int, col: int, text: str) -> None:
        for offset, char in enumerate(text):
            grid[row][col + offset] = char

    def joint(
        row: int, col: int, up: bool, down: bool, left: bool, right: bool
    ) -> None:
        grid[row][col] = _joint(glyphs, plain, up, down, left, right)

    boxes: dict[Node, Rect] = {tree.root: root_box}
    _draw_box(put, joint, glyphs, root_box, tree.root, below=bool(places))
    keywords: dict[str, Cell] = {}
    if keyword_text:
        keywords[LEVEL_ROOT] = (_ROOT_NAME, keyword_col)
        put(_ROOT_NAME, keyword_col, keyword_text)

    labels: dict[Edge, Cell] = {}
    paths: dict[Node, tuple[Cell, ...]] = {}
    if places:
        grid[_BUS - 1][root_middle] = glyphs.panel_vertical
        _draw_run(joint, _BUS, {root_middle}, set(drops))
        for place in places:
            for edge, label_x, drop in zip(
                place.edges, place.label_xs, place.drops, strict=True
            ):
                grid[_BUS + 1][drop] = glyphs.panel_vertical
                put(_LABEL, label_x, edge.label)
                labels[edge] = (_LABEL, label_x)
                grid[_LABEL + 1][drop] = glyphs.panel_vertical
            _draw_run(joint, _MERGE, set(place.drops), {place.middle})
            box = Rect(_AGENT_TOP, place.box_x, 3, place.box_width)
            boxes[place.node] = box
            _draw_box(put, joint, glyphs, box, place.node, above=True)
            paths[place.node] = _path(
                (_ROOT_NAME, root_middle),
                (_BUS, root_middle),
                (_BUS, place.drops[0]),
                (_MERGE, place.drops[0]),
                (_MERGE, place.middle),
                (_AGENT_NAME, place.middle),
            )
        if keyword_text:
            keywords[LEVEL_AGENTS] = (_AGENT_NAME, keyword_col)
            put(_AGENT_NAME, keyword_col, keyword_text)

    rows = ["".join(row) for row in grid]
    return Picture(tree, rows, boxes, labels, keywords, paths)


@dataclass
class _Place:
    """An agent's place on its level: its labels above, its box below."""

    node: Node
    edges: tuple[Edge, ...]
    width: int
    box_width: int
    x: int = 0
    label_xs: list[int] = field(default_factory=list)
    drops: list[int] = field(default_factory=list)

    @classmethod
    def of(cls, node: Node, edges: tuple[Edge, ...]) -> _Place:
        box_width = _box_width(node)
        return cls(node, edges, max(box_width, _span(edges)), box_width)

    @property
    def middle(self) -> int:
        return _middle(self.x, self.width)

    @property
    def box_x(self) -> int:
        return self.middle - (self.box_width - 1) // 2

    def at(self, x: int) -> None:
        """Place it from column `x`: its labels centred over its box's middle."""
        self.x = x
        label_x = self.middle - (_span(self.edges) - 1) // 2
        for edge in self.edges:
            self.label_xs.append(label_x)
            self.drops.append(_middle(label_x, len(edge.label)))
            label_x += len(edge.label) + GAP


def _span(edges: tuple[Edge, ...]) -> int:
    """The columns the labels of `edges` take side by side."""
    return sum(len(edge.label) for edge in edges) + GAP * max(0, len(edges) - 1)


def _middle(x: int, width: int) -> int:
    return x + (width - 1) // 2


def _box_width(node: Node) -> int:
    """Its name with a space and a frame each side, wide enough for its marks.

    The marks stand right of the middle of the top edge, where the line
    comes in, so a box with marks is at least twice their width wide.
    """
    width = len(node.name) + 4
    if node.marks:
        width = max(width, 2 * len(node.marks) + 5)
    return width


def _joint(
    glyphs: Glyphs, plain: bool, up: bool, down: bool, left: bool, right: bool
) -> str:
    """The character where lines meet, by the directions it connects."""
    if (left or right) and not (up or down):
        return glyphs.rule
    if (up or down) and not (left or right):
        return glyphs.panel_vertical
    corners = {
        (False, True, False, True): glyphs.panel_top_left,
        (False, True, True, False): glyphs.panel_top_right,
        (True, False, False, True): glyphs.panel_bottom_left,
        (True, False, True, False): glyphs.panel_bottom_right,
    }
    key = (up, down, left, right)
    if key in corners:
        return corners[key]
    return "+" if plain else _UNICODE_TEES.get(key, "+")


def _draw_run(joint: _JointDrawer, row: int, ups: set[int], downs: set[int]) -> None:
    """A horizontal run on `row` joining the lines above at `ups` to those below at `downs`."""
    columns = sorted(ups | downs)
    first, last = columns[0], columns[-1]
    for col in range(first, last + 1):
        joint(row, col, col in ups, col in downs, col > first, col < last)


def _draw_box(
    put: _Putter,
    joint: _JointDrawer,
    glyphs: Glyphs,
    box: Rect,
    node: Node,
    *,
    above: bool = False,
    below: bool = False,
) -> None:
    """A box framed in the panel's glyphs, with its name centred and its marks."""
    middle = _middle(box.x, box.width)
    edge = glyphs.rule * (box.width - 2)
    put(box.y, box.x, glyphs.panel_top_left + edge + glyphs.panel_top_right)
    put(box.y + 2, box.x, glyphs.panel_bottom_left + edge + glyphs.panel_bottom_right)
    if above:
        joint(box.y, middle, True, False, True, True)
    if below:
        joint(box.y + 2, middle, False, True, True, True)
    if node.marks:
        put(box.y, box.right - 2 - len(node.marks), node.marks)
    inside = box.width - 2
    name = node.name.center(inside)
    put(box.y + 1, box.x, glyphs.panel_vertical + name + glyphs.panel_vertical)


def _path(*corners: Cell) -> tuple[Cell, ...]:
    """The cells from the first corner to the last, straight between each two."""
    cells = [corners[0]]
    for row, col in corners[1:]:
        at_row, at_col = cells[-1]
        while (at_row, at_col) != (row, col):
            at_row += (row > at_row) - (row < at_row)
            at_col += (col > at_col) - (col < at_col)
            cells.append((at_row, at_col))
    return tuple(cells)
