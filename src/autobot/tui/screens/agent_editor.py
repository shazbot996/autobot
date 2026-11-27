"""The Agent editor: the agent tree walked with the arrow keys, and one agent's editor.

`AgentTreeScreen` draws the tree of `autobot.agents.tree`, the same picture
the face's game screen shows, with a selection on one node (AG-9). The arrow
keys move the selection; a picture wider or taller than the panel is scrolled
so that the selected box is on the screen. Enter on an agent opens its
`AgentView`; Enter on the root says that the root is the wake phrase.

`AgentView` shows how one agent is built (AG-10): what its code does, as
text, then its definition as a list of fields (`autobot.tui.agent_edit`).
Enter on a field changes it: a line of text is typed into a prompt, a
confirmation or the listening setting is turned on or off (AG-13, AG-15),
and a prompt, the introduce script or the camera's commands are edited in a
text area inside the panel, which Esc closes and saves. Every change is
written at once to the agent's `[[agents]]` table in `config/autobot.toml`.

The weather agent's API key is the exception (AG-6): it is pasted into a
masked field and sent to Secret Manager through the service account, as on
the Google Cloud screen (GC-9). It is never drawn and never written to a file.
"""

from __future__ import annotations

import curses
import textwrap
from collections.abc import Sequence
from dataclasses import dataclass

from autobot.agents.registry import AGENTS
from autobot.agents.tree import Picture, Tree, build_tree, layout
from autobot.config import Config
from autobot.gcp.client import GcpError
from autobot.gcp.secrets import store_secret
from autobot.tui.agent_edit import (
    MULTI_LINE,
    SECRET,
    TEXT,
    TOGGLE,
    Field,
    Selection,
    TextArea,
    commands_text,
    fields,
    table_index,
)
from autobot.tui.app import KEY_ESC, KEYS_BACKSPACE, KEYS_ENTER, App, View
from autobot.tui.config_edit import Change
from autobot.tui.ui import Canvas, Rect, fit

ROOT_LINE = "The root is the wake phrase: the Wake phrase screen edits it"

# The rows under the tree that say what the selected node is.
INFO_ROWS = 4
# The column the fields' values start at, after the pointer and the label.
LABEL_WIDTH = 25


def scroll(start: int, size: int, extent: int, room: int) -> int:
    """Where a picture `extent` long starts in `room`, to show `size` cells from `start`.

    A picture that fits is centred, which makes the result negative: the
    margin before it. One that does not is scrolled to centre the cells,
    as far as its ends allow.
    """
    if extent <= room:
        return -((room - extent) // 2)
    return max(0, min(extent - room, start + size // 2 - room // 2))


def put_clipped(
    canvas: Canvas, rect: Rect, row: int, col: int, text: str, attr: int
) -> None:
    """Write `text` inside `rect` at a column that may be left of it."""
    if col < 0:
        text = text[-col:]
        col = 0
    canvas.put_in(rect, row, col, text, attr)


class AgentTreeScreen(View):
    title = "Agent editor"
    help_lines = (
        "Up, Down       move the selection a level of the tree",
        "Left, Right    move it between the agents of a level",
        "Enter          open the selected agent's editor",
        "",
        "A ? on an agent's box: it asks before it starts.",
        "A $? on it: it asks before it sends a request out.",
    )

    def __init__(self, app: App) -> None:
        super().__init__(app)
        self.built: Config | None = None
        self.tree: Tree = build_tree(app.config)
        self.selection = Selection(self.tree)
        self.picture: Picture = layout(self.tree, app.canvas.glyphs)
        self.built = app.config

    def sync(self) -> None:
        """Build the tree again when the configuration changed, keeping the selection's place."""
        config = self.app.config
        if self.built is config:
            return
        self.tree = build_tree(config)
        self.selection = Selection(
            self.tree, self.selection.level, self.selection.column
        )
        self.picture = layout(self.tree, self.app.canvas.glyphs)
        self.built = config

    # --- keys ---------------------------------------------------------------

    def key(self, key: int) -> bool:
        self.sync()
        selection = self.selection
        if key in (curses.KEY_UP, ord("k")):
            selection.up()
        elif key in (curses.KEY_DOWN, ord("j")):
            selection.down()
        elif key in (curses.KEY_LEFT, ord("h")):
            selection.left()
        elif key in (curses.KEY_RIGHT, ord("l")):
            selection.right()
        elif key in KEYS_ENTER:
            self.open()
        else:
            return False
        return True

    def open(self) -> None:
        app = self.app
        node = self.selection.current
        if node.root:
            app.say(ROOT_LINE)
        elif node.name in AGENTS:
            app.push(AgentView(app, node.name))
        else:
            app.error(f"{node.name} is not an agent autobot has")

    # --- drawing ------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        self.sync()
        theme = canvas.theme
        picture = self.picture
        node = self.selection.current
        area = Rect(rect.y, rect.x + 1, max(1, rect.height - INFO_ROWS), rect.width - 2)
        box = picture.boxes[node]
        top = scroll(box.y, box.height, picture.height, area.height)
        left = scroll(box.x, box.width, picture.width, area.width)
        for row in range(area.height):
            if 0 <= row + top < picture.height:
                put_clipped(
                    canvas, area, row, -left, picture.rows[row + top], theme.normal
                )
        for row in range(box.y, box.bottom):
            put_clipped(
                canvas,
                area,
                row - top,
                box.x - left,
                picture.rows[row][box.x : box.right],
                theme.selected,
            )
        if rect.height > INFO_ROWS:
            self.draw_info(
                canvas, Rect(rect.y + area.height, rect.x, INFO_ROWS, rect.width)
            )

    def draw_info(self, canvas: Canvas, rect: Rect) -> None:
        """What the selected node is: its commands and its description."""
        theme = canvas.theme
        config = self.app.config
        node = self.selection.current
        width = rect.width - 2
        canvas.rule(rect, 0, theme.frame)
        if node.root:
            head = f"{node.name}: the wake phrase"
            words = ", ".join(self.tree.keywords) or "(none)"
            lines = [
                "Say it, then one of the commands on the lines below it.",
                f"Heard at every level: {words}",
            ]
        else:
            head = f"{node.name} agent: {commands_text(node.name, config.routes)}"
            lines = textwrap.wrap(config.agent(node.name).description, width)
        canvas.put_in(rect, 1, 1, fit(head, width), theme.heading)
        for offset, line in enumerate(lines[: INFO_ROWS - 2]):
            canvas.put_in(rect, 2 + offset, 1, fit(line, width), theme.normal)

    def hint(self) -> str:
        node = self.selection.current
        if node.root:
            return "The arrows walk the tree; Down goes to the agents"
        return f"Enter opens the {node.name} agent's editor"

    def key_bar(self) -> Sequence[tuple[str, str]]:
        return (("F1", "Help"), ("Arrows", "Move"), ("Enter", "Open"), ("Esc", "Back"))


@dataclass(frozen=True)
class Row:
    """One row of the editor view's body."""

    text: str = ""
    role: str = "normal"
    # The index of the field the row is, or -1.
    field: int = -1
    # Whether the row is the note under a field: drawn at the values' column.
    note: bool = False


class AgentView(View):
    help_lines = (
        "Up, Down       move between the fields",
        "Enter          change the field: type it, turn it on or off,",
        "               open its text, or paste the API key",
        "Esc            in an open text, close it and save what changed",
        "",
        "Every change is written to config/autobot.toml at once.",
        "Commands are edited on the Agent routing screen.",
    )

    def __init__(self, app: App, name: str) -> None:
        super().__init__(app)
        self.name = name
        self.title = f"Agent editor: {name}"
        self.agent = AGENTS[name]
        self.cursor = 0
        self.top = 0
        # The open multi-line field and its text; `typing` is true while it is open.
        self.area = TextArea()
        self.editing: Field | None = None
        self.typing = False
        # Whether the config file has an `[[agents]]` table for the agent.
        self.in_file = True
        self.fields: list[Field] = []
        self.refresh()

    def refresh(self) -> None:
        """Read the fields again from the configuration, and the table's number from the file."""
        app = self.app
        config = app.config
        definition = config.agent(self.name)
        # The file's tables come first in `config.agents`, in the file's
        # order, so this is the table's number when the file defines it.
        index = config.agents.index(definition)
        self.in_file = True
        try:
            text = app.services.store.path.read_text(encoding="utf-8")
            found = table_index(text, self.name)
        except (OSError, ValueError):
            # A file that cannot be read: the save says what is wrong with it.
            pass
        else:
            self.in_file = found is not None
            if found is not None:
                index = found
        self.fields = fields(self.agent, definition, index, config.routes)
        self.cursor = min(self.cursor, len(self.fields) - 1)

    def leave(self) -> None:
        if self.editing is not None:
            self.close_area()

    # --- changing a field -----------------------------------------------------

    def save(self, change: Change) -> bool:
        """Write `change` to the agent's table at once; say why in one line when it cannot be."""
        app = self.app
        if not self.in_file:
            app.error(
                f"Not saved: no [[agents]] table named {self.name} in the config file"
            )
            return False
        saved = app.save(change)
        self.refresh()
        return saved

    def activate(self) -> None:
        """Enter on the selected field."""
        self.refresh()
        app = self.app
        field = self.fields[self.cursor]
        if field.kind == TEXT:
            typed = app.prompt(
                f"{self.name}: {field.label}",
                f"[[agents]] {field.key} of the {self.name} agent:",
                field.text,
                field.complaint,
            )
            if typed is None:
                return
            change = field.change(typed)
            if change.value != field.value:
                self.save(change)
        elif field.kind == TOGGLE:
            self.save(field.change())
        elif field.kind == MULTI_LINE:
            self.editing = field
            self.area.open(field.text)
            self.typing = True
        elif field.kind == SECRET:
            self.paste_key(field)
        else:
            app.say(f"{field.label}: {field.hint}")

    def paste_key(self, field: Field) -> None:
        """Paste the API key into a masked field and store it in Secret Manager (AG-6)."""
        app = self.app
        services = app.services
        secret = field.text
        try:
            cloud = services.gcp_connect(app.config, services.repo)
        except GcpError as error:
            app.error(f"{secret} not stored: {error}")
            return
        value = app.prompt_secret(
            f"Paste {secret}",
            "The value goes to Secret Manager and is kept nowhere else:",
        )
        if value is None:
            app.say(f"Cancelled: {secret} is unchanged")
            return
        try:
            # Clears `value` whether it works or not.
            store_secret(cloud, app.config.gcp, secret, value)
        except GcpError as error:
            app.error(str(error))
            return
        app.say(f"Stored: {secret} has a new version in Secret Manager", "ok")

    def close_area(self) -> None:
        """Close the open text, and write it when it changed."""
        field = self.editing
        text = self.area.text()
        self.area.close()
        self.editing = None
        self.typing = False
        if field is None:
            return
        if text == field.value:
            self.app.say(f"{field.label} is unchanged")
            return
        self.save(field.change(text))

    # --- keys ---------------------------------------------------------------

    def key(self, key: int) -> bool:
        if self.editing is not None:
            return self.area_key(key)
        count = len(self.fields)
        if key in (curses.KEY_UP, ord("k")):
            self.cursor = (self.cursor - 1) % count
        elif key in (curses.KEY_DOWN, ord("j")):
            self.cursor = (self.cursor + 1) % count
        elif key == curses.KEY_HOME:
            self.cursor = 0
        elif key == curses.KEY_END:
            self.cursor = count - 1
        elif key in KEYS_ENTER:
            self.activate()
        else:
            return False
        return True

    def area_key(self, key: int) -> bool:
        """A key while the text is open: every one is the text's, so none leaves the view."""
        area = self.area
        if key == KEY_ESC:
            self.close_area()
        elif key in KEYS_ENTER:
            area.newline()
        elif key in KEYS_BACKSPACE:
            area.backspace()
        elif key == curses.KEY_DC:
            area.delete()
        elif key == curses.KEY_LEFT:
            area.move_left()
        elif key == curses.KEY_RIGHT:
            area.move_right()
        elif key == curses.KEY_UP:
            area.move_up()
        elif key == curses.KEY_DOWN:
            area.move_down()
        elif key == curses.KEY_HOME:
            area.home()
        elif key == curses.KEY_END:
            area.end()
        elif 32 <= key < 127:
            area.insert(chr(key))
        return True

    # --- drawing ------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        if self.editing is not None:
            self.draw_area(canvas, rect, self.editing)
        else:
            self.draw_fields(canvas, rect)

    def rows(self, width: int) -> list[Row]:
        """The body: what the code does, folded, then the fields."""
        about = textwrap.wrap(self.agent.about, max(8, width - 4))
        rows = [Row(line) for line in about]
        rows += [Row(), Row("Its definition: Enter changes a field", "heading")]
        for index, field in enumerate(self.fields):
            rows.append(Row(field=index))
            if field.note:
                rows.append(Row(field.note, "dim", index, note=True))
        return rows

    def draw_fields(self, canvas: Canvas, rect: Rect) -> None:
        theme = canvas.theme
        rows = self.rows(rect.width)
        body = Rect(rect.y + 1, rect.x, max(1, rect.height - 1), rect.width)
        # Scroll so that the selected field, and the note under it, are shown.
        mine = [index for index, row in enumerate(rows) if row.field == self.cursor]
        if self.cursor == 0:
            self.top = 0
        elif mine[0] < self.top:
            self.top = mine[0]
        elif mine[-1] >= self.top + body.height:
            self.top = mine[-1] - body.height + 1
        self.top = max(0, min(self.top, len(rows) - body.height))

        head = f" The {self.name} agent: what its code does"
        canvas.put_in(rect, 0, 0, fit(head, rect.width), theme.heading)
        if len(rows) > body.height:
            last = min(len(rows), self.top + body.height)
            shown = f"rows {self.top + 1}-{last} of {len(rows)}"
            canvas.put_in(
                rect,
                0,
                max(len(head) + 1, rect.width - len(shown) - 1),
                shown,
                theme.dim,
            )
        value_at = LABEL_WIDTH + 5
        for offset, row in enumerate(rows[self.top : self.top + body.height]):
            if row.note:
                canvas.put_in(
                    body,
                    offset,
                    value_at,
                    fit(row.text, rect.width - value_at - 1),
                    theme.dim,
                )
            elif row.field >= 0:
                self.draw_field(canvas, body, offset, row.field)
            else:
                canvas.put_in(
                    body,
                    offset,
                    2,
                    fit(row.text, rect.width - 3),
                    getattr(theme, row.role),
                )

    def draw_field(self, canvas: Canvas, body: Rect, offset: int, index: int) -> None:
        theme = canvas.theme
        field = self.fields[index]
        chosen = index == self.cursor
        pointer = canvas.glyphs.pointer if chosen else " "
        head = f" {pointer} {field.label:<{LABEL_WIDTH}} "
        value = fit(field.shown, body.width - len(head) - 2)
        if chosen:
            canvas.put_in(
                body, offset, 1, f"{head}{value} ".ljust(body.width - 2), theme.selected
            )
            return
        canvas.put_in(body, offset, 1, head, theme.normal)
        canvas.put_in(
            body,
            offset,
            1 + len(head),
            value,
            theme.value if field.editable else theme.dim,
        )

    def draw_area(self, canvas: Canvas, rect: Rect, field: Field) -> None:
        """The open text: its lines, with the cursor's cell in `theme.selected`."""
        theme = canvas.theme
        area = self.area
        head = f" {field.label} of the {self.name} agent"
        canvas.put_in(rect, 0, 0, fit(head, rect.width), theme.heading)
        canvas.rule(rect, 1, theme.frame)
        body = Rect(rect.y + 2, rect.x + 1, max(1, rect.height - 2), rect.width - 2)
        lines = area.window(body.height, body.width)
        where = f"line {area.row + 1} of {len(area.lines)}, column {area.col + 1}"
        canvas.put_in(
            rect, 0, max(len(head) + 1, rect.width - len(where) - 1), where, theme.dim
        )
        for offset, line in enumerate(lines):
            canvas.put_in(body, offset, 0, line, theme.normal)
        line = area.lines[area.row]
        under = line[area.col] if area.col < len(line) else " "
        canvas.put_in(
            body, area.row - area.top, area.col - area.left, under, theme.selected
        )

    def hint(self) -> str:
        if self.editing is not None:
            return "Type the text; Esc closes it and saves it when it changed"
        return self.fields[self.cursor].hint

    def key_bar(self) -> Sequence[tuple[str, str]]:
        if self.editing is not None:
            return (("F1", "Help"), ("Esc", "Save and close"))
        return (
            ("F1", "Help"),
            (self.app.canvas.glyphs.up_down, "Field"),
            ("Enter", "Change"),
            ("Esc", "Tree"),
        )
