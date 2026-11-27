"""The Face screen: autobot's face, and the agent a command after a wake routes to.

It draws `autobot.tui.face.FaceModel` (FS-2), from top to bottom:

- a header row: `MIC` and the microphone's compact meter at the left
  (FS-4), `FACE` in the middle, and whether the listener's event stream is
  connected at the right;
- the upper band, about a third of the body: the **Summary** panel on the
  left (FS-8) and the **autobot** panel on the right, with the eyes (FS-5)
  and the mouth meter under them (FS-6);
- the detail panel, taking the rest of the body (FS-9), which is a game
  screen (AG-3): see below;
- the ticker of what the listener hears, on the last row (FS-3).

While no agent runs, the detail panel is titled **Map** and shows the agent
tree (`autobot.agents.tree`) with the protagonist, a mini-autobot with the
face's own eyes, at its centre: the map slides under him as the session
moves (`autobot.tui.tree_game`), the box he is at or heading for is drawn in
the theme's green, the global action keywords stand in the panel's edge, and
a confirmation's question is written under the node it is asked at. While an
agent runs the panel is that agent's running view, titled with its name:
the detail it lays out. The view stays for a little while after the agent
ends, or until the next wake, so that an answer can be read; then the
protagonist walks back to the root.

The app's message line and key bar stay below the frame. Up, Down, PgUp,
PgDn and End scroll an agent's running view, as on the Live monitor; on the
map they do nothing. Tab or Esc returns to the main menu (AG-8).

The screen does no logic of its own: every frame it polls the event stream
on `run/listener.sock` and feeds the model, which moves the ticker and the
eyes and runs the router (`autobot.tui.router`): a wake starts it, it
listens for a command, and the agent the command routes to runs on the
face. Off the NUC the screen still
opens, with the meter and the mouth idle and the ticker reading the socket,
and the microphone and autobot's voice refuse with the usual line (FS-14,
TS-11). All audio stays on this machine (`CN-1`, `CN-2`).
"""

from __future__ import annotations

import curses
import itertools
import math
from collections.abc import Sequence

from autobot.agents.tree import (
    LEVEL_ROOT,
    Node,
    Picture,
    build_tree,
    layout,
)
from autobot.tui.app import App, View
from autobot.tui.echo import prepare
from autobot.tui.face import FaceModel
from autobot.tui.face_meters import Bar
from autobot.tui.face_reactions import draw_eyes
from autobot.tui.router import Router
from autobot.tui.tree_game import (
    PHASE_CONFIRMING,
    PHASE_LISTENING,
    PHASE_RUNNING,
    PROTAGONIST_ROWS,
    PROTAGONIST_WIDTH,
    Game,
    Position,
    draw_protagonist,
)
from autobot.tui.ui import Canvas, Rect, fit, wrap

# What `App.require_nuc` names off the NUC.
WHAT = "the face's microphone and voice"
HINT = 'Say the wake phrase, then a command such as "time"'
NAME = "FACE"
CONNECTED = "connected"
DOWN = "down"
DETAIL_EMPTY = "(nothing yet: what the agent is doing appears here)"
# The detail panel's title while it shows the map.
MAP = "Map"
# How long an agent's running view stays after the agent ends, in seconds,
# unless a wake comes first: long enough to read an answer.
LEVEL_LINGER_SECONDS = 20.0
# The upper band is a third of the body, and never fewer rows than this.
BAND_MIN_ROWS = 6
# The summary panel's share of the upper band's width, in fifths.
SUMMARY_FIFTHS = 3
# The meters' widths, in cells.
MIC_CELLS = 12
MOUTH_CELLS = 9


def draw_bar(canvas: Canvas, y: int, x: int, meter: Bar) -> None:
    """A meter at (y, x), in the Audio screen's three bands, with its hold marker."""
    theme = canvas.theme
    glyphs = canvas.glyphs
    canvas.put(y, x, glyphs.empty * meter.width, theme.dim)
    for start, end, attr in (
        (0, meter.warn, theme.meter_low),
        (meter.warn, meter.hot, theme.meter_mid),
        (meter.hot, meter.width, theme.meter_high),
    ):
        filled = min(meter.cells, end) - start
        if filled > 0:
            canvas.put(y, x + start, glyphs.full * filled, attr)
    if meter.hold:
        canvas.put(y, x + meter.hold - 1, glyphs.hold, theme.value)


class FaceScreen(View):
    title = "Face"
    help_lines = (
        "Up, Down       scroll an agent's view (also PgUp, PgDn)",
        "End            follow the agent's view again",
        "Tab            back to the main menu (also Esc)",
        "",
    )

    def __init__(self, app: App) -> None:
        super().__init__(app)
        # Built in `enter`, on the config file as it is when the screen opens.
        self.model: FaceModel
        self.picture: Picture
        self.game: Game
        # The running position of the agent whose view is shown, and when
        # the view goes once the agent has ended; None on the map.
        self._level: Position | None = None
        self._level_until: float | None = None

    # --- opening and closing --------------------------------------------------

    def enter(self) -> None:
        app = self.app
        self.model = FaceModel.from_services(
            app.config, app.services, Router, marker=app.canvas.glyphs.bullet
        )
        # The map: the agent tree as it is configured now, in this terminal's
        # line-drawing characters.
        self.picture = layout(build_tree(app.config), app.canvas.glyphs)
        self.game = Game(self.picture)
        self._level = None
        self._level_until = None
        if not app.require_nuc(WHAT):
            return
        if not self.model.open():
            app.error(f"The microphone did not open: {self.model.mic.error}")
        # Rendered once per visit, so the first wake's reply is quick.
        prepare(self.model.voice)

    def leave(self) -> None:
        self.model.close()
        self.app.services.events.close()

    def tick(self) -> None:
        events = self.app.services.events
        self.model.feed(events.poll(), connected=events.connected)
        self.model.tick()
        self.follow(self.app.services.clock())

    def follow(self, now: float) -> None:
        """Give the game the session's position, holding an agent's view a while."""
        position = self.model.position
        if position.phase == PHASE_RUNNING:
            self._level = position
            self._level_until = None
        elif self._level is not None:
            if position.phase != PHASE_LISTENING:
                # A wake, or `cancel`: back on the map at once.
                self._level = None
            elif self._level_until is None:
                self._level_until = now + LEVEL_LINGER_SECONDS
            elif now >= self._level_until:
                self._level = None
        self.game.follow(self._level or position, now)

    @property
    def level_view(self) -> bool:
        """Whether the detail panel is an agent's running view, not the map."""
        return self.game.level_view(self.app.services.clock())

    def frame_wait(self) -> int:
        # The next frame is drawn when the ticker's next column is due, so it
        # scrolls a column at a time at even intervals.
        return math.ceil(self.model.next_frame() * 1000)

    # --- keys -----------------------------------------------------------------

    def key(self, key: int) -> bool:
        detail = self.model.detail
        scrolls = (
            curses.KEY_UP,
            curses.KEY_DOWN,
            curses.KEY_PPAGE,
            curses.KEY_NPAGE,
            curses.KEY_END,
        )
        if key in scrolls and not self.level_view:
            # The map follows the protagonist: there is nothing to scroll.
            return True
        if key == curses.KEY_UP:
            detail.up()
        elif key == curses.KEY_DOWN:
            detail.down()
        elif key == curses.KEY_PPAGE:
            detail.page_up()
        elif key == curses.KEY_NPAGE:
            detail.page_down()
        elif key == curses.KEY_END:
            detail.end()
        else:
            return False
        return True

    def hint(self) -> str:
        return self.model.mic.warning or HINT

    def key_bar(self) -> Sequence[tuple[str, str]]:
        return (
            ("F1", "Help"),
            (self.app.canvas.glyphs.up_down, "Scroll"),
            ("Tab", "Menu"),
            ("Esc", "Back"),
            ("F10", "Quit"),
        )

    # --- drawing --------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        if rect.height < 4 or rect.width < 20:
            return
        self.draw_header(canvas, Rect(rect.y, rect.x, 1, rect.width))
        body = rect.height - 2
        band = min(body, max(BAND_MIN_ROWS, body // 3))
        summary_width = rect.width * SUMMARY_FIFTHS // 5
        self.draw_summary(canvas, Rect(rect.y + 1, rect.x, band, summary_width))
        self.draw_character(
            canvas,
            Rect(rect.y + 1, rect.x + summary_width, band, rect.width - summary_width),
        )
        detail = body - band
        if detail >= 3:
            self.draw_detail(
                canvas, Rect(rect.y + 1 + band, rect.x, detail, rect.width)
            )
        self.draw_ticker(canvas, Rect(rect.bottom - 1, rect.x, 1, rect.width))

    def draw_header(self, canvas: Canvas, rect: Rect) -> None:
        """`MIC` and its meter, the screen's name, and the listener's connection."""
        theme = canvas.theme
        mic = self.model.mic
        canvas.put_in(rect, 0, 1, "MIC", theme.heading)
        cells = min(MIC_CELLS, max(0, rect.width // 4))
        draw_bar(canvas, rect.y, rect.x + 5, mic.bar(cells))
        name_at = max(0, (rect.width - len(NAME)) // 2)
        reading_at = 5 + cells + 1
        role = {
            "live": theme.value,
            "silent": theme.error,
            "failed": theme.warning,
            "off": theme.dim,
        }[mic.state]
        canvas.put_in(
            rect, 0, reading_at, fit(mic.text, name_at - reading_at - 1), role
        )
        canvas.put_in(rect, 0, name_at, NAME, theme.heading)
        connected = self.app.services.events.connected
        state = CONNECTED if connected else DOWN
        label = "listener: "
        at = rect.width - len(label) - len(state) - 1
        if at > name_at + len(NAME):
            canvas.put_in(rect, 0, at, label, theme.normal)
            canvas.put_in(
                rect,
                0,
                at + len(label),
                state,
                theme.ok if connected else theme.warning,
            )

    def draw_summary(self, canvas: Canvas, rect: Rect) -> None:
        """What the experience says about the session, newest at the bottom."""
        theme = canvas.theme
        inside = canvas.panel(rect, "Summary", title_attr=theme.heading)
        if inside.height <= 0 or inside.width <= 0:
            return
        rows: list[tuple[str, int]] = []
        for line in self.model.summary.lines:
            attr = theme.warning if line.trouble else theme.normal
            rows += [(text, attr) for text in wrap([line.text], inside.width)]
        for row, (text, attr) in enumerate(rows[-inside.height :]):
            canvas.put_in(inside, row, 0, fit(text, inside.width), attr)

    def draw_character(self, canvas: Canvas, rect: Rect) -> None:
        """The eyes, centred, and the mouth meter under them.

        Where the panel has no row to spare under the eyes, the mouth is set
        into the panel's lower edge.
        """
        theme = canvas.theme
        inside = canvas.panel(rect, "autobot", title_attr=theme.heading)
        if inside.height <= 0 or inside.width <= 0:
            return
        eyes = draw_eyes(self.model.pose(), canvas.glyphs)
        left = max(0, (inside.width - len(eyes[0])) // 2)
        top = max(0, (inside.height - len(eyes) - 1) // 2)
        for row, line in enumerate(eyes):
            canvas.put_in(inside, top + row, left, line, theme.value)
        cells = min(MOUTH_CELLS, inside.width)
        mouth_row = top + len(eyes)
        y = inside.y + mouth_row if mouth_row < inside.height else rect.bottom - 1
        x = inside.x + max(0, (inside.width - cells) // 2)
        draw_bar(canvas, y, x, self.model.mouth.bar(cells))

    def draw_detail(self, canvas: Canvas, rect: Rect) -> None:
        """The game screen: the map, or the running view of the agent that runs."""
        now = self.app.services.clock()
        detail = self.model.detail
        if self.game.level_view(now):
            self.draw_level(canvas, rect, self.game.position.node)
            return
        inside = canvas.panel(rect, MAP, title_attr=canvas.theme.heading)
        # The global action keywords work at every level of the map (AG-14):
        # they stand in the panel's edge, where the sliding map never hides them.
        keywords = " ".join(f"[{word}]" for word in self.picture.tree.keywords)
        note = f" say {keywords} to go back "
        at = rect.width - len(note) - 2
        if keywords and at > len(MAP) + 4:
            canvas.put_in(rect, 0, at, note, canvas.theme.accent)
        # An answer is laid out to the panel as it is shown now (AG-4).
        detail.page = max(1, inside.height)
        detail.cols = max(1, inside.width)
        if inside.height > 0 and inside.width > 0:
            self.draw_map(canvas, inside, now)

    def draw_level(self, canvas: Canvas, rect: Rect, agent: str) -> None:
        """An agent's running view: its lines; a view scrolled back says so in its edge."""
        theme = canvas.theme
        title = agent or MAP
        inside = canvas.panel(rect, title, title_attr=theme.heading)
        detail = self.model.detail
        detail.cols = max(1, inside.width)
        lines = detail.shown(inside.height)
        if not lines:
            canvas.put_in(inside, 0, 0, fit(DETAIL_EMPTY, inside.width), theme.dim)
            return
        for row, line in enumerate(lines):
            canvas.put_in(inside, row, 0, fit(line, inside.width), theme.normal)
        if detail.scroll:
            note = f" scrolled back {detail.scroll} lines: End follows "
            at = rect.width - len(note) - 2
            if at > len(title) + 4:
                canvas.put_in(rect, 0, at, note, theme.warning)

    def draw_map(self, canvas: Canvas, inside: Rect, now: float) -> None:
        """The agent tree, slid so that the protagonist's cell is the panel's centre."""
        theme = canvas.theme
        picture = self.picture
        game = self.game
        top, left = game.offset(now, inside.height, inside.width)

        def put(map_row: int, map_col: int, text: str, attr: int) -> None:
            """Write `text`, which starts at a cell of the map, clipped to the panel."""
            row, col = map_row - top, map_col - left
            if not 0 <= row < inside.height:
                return
            if col < 0:
                text, col = text[-col:], 0
            canvas.put_in(inside, row, col, text[: max(0, inside.width - col)], attr)

        for map_row, text in enumerate(picture.rows):
            put(map_row, 0, text, theme.normal)
        position = game.position
        # The box he is at, or heading for, in the theme's green (AG-3).
        node = self.target(position)
        if node is not None:
            box = picture.boxes[node]
            for map_row in range(box.y, box.bottom):
                put(map_row, box.x, picture.rows[map_row][box.x : box.right], theme.ok)
        # A confirmation's question, under the node it is asked at (AG-13).
        if position.phase == PHASE_CONFIRMING and node is not None and position.node:
            question = self.app.config.agent(position.node).confirm_entry_question
            text = fit(question, inside.width)
            row = picture.boxes[node].bottom - top
            if 0 <= row < inside.height:
                canvas.put_in(
                    inside,
                    row,
                    max(0, (inside.width - len(text)) // 2),
                    text,
                    theme.warning,
                )
        # The protagonist stands over the centre cell, on top of the box.
        eyes = draw_protagonist(self.model.pose(), canvas.glyphs)
        centre_row, centre_col = inside.height // 2, inside.width // 2
        for offset, line in enumerate(eyes):
            row = centre_row - 1 - PROTAGONIST_ROWS + offset
            if 0 <= row < inside.height:
                canvas.put_in(
                    inside,
                    row,
                    max(0, centre_col - PROTAGONIST_WIDTH // 2),
                    line,
                    theme.value,
                )

    def target(self, position: Position) -> Node | None:
        """The node the protagonist is at or heading for; None between the levels."""
        tree = self.picture.tree
        if position.level == LEVEL_ROOT or position.phase == PHASE_LISTENING:
            return tree.root
        if position.node:
            try:
                return tree.node(position.node)
            except KeyError:
                return None
        return None

    def draw_ticker(self, canvas: Canvas, rect: Rect) -> None:
        """What the listener hears, with the popped wake phrase in `theme.wake`."""
        theme = canvas.theme
        ticker = self.model.ticker
        cells = ticker.view(rect.width - 2)
        if ticker.down:
            text = "".join(cell.char for cell in cells)
            canvas.put_in(rect, 0, 1, text, theme.warning)
            return
        column = 1
        for wake, group in itertools.groupby(cells, key=lambda cell: cell.wake):
            text = "".join(cell.char for cell in group)
            canvas.put_in(rect, 0, column, text, theme.wake if wake else theme.normal)
            column += len(text)
