"""The Agent editor's model: the selection, the fields, the text area. No drawing.

The Agent editor (AG-9, AG-10) is two screens over this module:

- the tree screen walks the agent tree with a `Selection`: Up and Down change
  the level, Left and Right move along it;
- the editor view shows one agent as a list of `Field`s, built by `fields`
  from the agent's code (`autobot.agents.registry`) and its definition (its
  `[[agents]]` table). A field knows how it is shown, the one-line complaint
  for a bad value, and the `Change` that writes it to `config/autobot.toml`;
- a multi-line field (a prompt, the introduce script, the camera's commands)
  is edited in a `TextArea`: lines, a cursor, and the window on them.

A `Change` to an `[[agents]]` table needs the table's number in the file, and
the file may define fewer tables than there are agents: the definitions it
lacks come from `autobot.config.DEFAULT_AGENTS`. `table_index` reads that
number from the file's text, by the agent's name, so that a change is never
written to another agent's table.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from autobot.agents.base import Agent
from autobot.agents.tree import Node, Tree
from autobot.config import AGENT_INTRODUCE, AgentConfig, RouteConfig
from autobot.tui.config_edit import Change

TABLE = "agents"

# What Enter does on a field.
READ_ONLY = "read-only"
TEXT = "text"
TOGGLE = "toggle"
MULTI_LINE = "multi-line"
SECRET = "secret"

COMMANDS_NOTE = "edited on the Agent routing screen"
SECRET_NOTE = "stored in Secret Manager; never shown"
# What is said of a field that neither is edited here nor has a note.
CODE_NOTE = "part of the agent's code: it is not edited here"

# The rules of `autobot.config._agents`, which has no public complaint
# helpers: a zip code is five digits, or empty until it is set; a limit is a
# positive whole number; a question and the folder are not empty.
_ZIP_CODE = re.compile(r"[0-9]{5}")
_WHOLE = re.compile(r"[0-9]+")

type Complaint = Callable[[str], str | None]


def zip_code_complaint(text: str) -> str | None:
    typed = text.strip()
    if typed and not _ZIP_CODE.fullmatch(typed):
        return "a zip code is five digits"
    return None


def limit_complaint(text: str) -> str | None:
    typed = text.strip()
    if not _WHOLE.fullmatch(typed) or int(typed) <= 0:
        return "a limit is a positive whole number"
    return None


def empty_complaint(text: str) -> str | None:
    return None if text.strip() else "it must not be empty"


# --- the tree screen's selection ----------------------------------------------


class Selection:
    """The node of the tree the arrow keys stand on (AG-9).

    The column is the node's place on its level, counted from the left. A
    change of level keeps the column nearest the current one: the same
    place, or the level's last when it is shorter.
    """

    def __init__(self, tree: Tree, level: int = 0, column: int = 0) -> None:
        # A level with no node, as the agents' with no rule, cannot be stood on.
        self.levels = tuple(level for level in tree.levels if level)
        self.level = max(0, min(level, len(self.levels) - 1))
        self.column = self._nearest(column)

    def _nearest(self, column: int) -> int:
        return max(0, min(column, len(self.levels[self.level]) - 1))

    @property
    def current(self) -> Node:
        return self.levels[self.level][self.column]

    def _to_level(self, level: int) -> None:
        if 0 <= level < len(self.levels):
            self.level = level
            self.column = self._nearest(self.column)

    def up(self) -> None:
        self._to_level(self.level - 1)

    def down(self) -> None:
        self._to_level(self.level + 1)

    def left(self) -> None:
        self.column = self._nearest(self.column - 1)

    def right(self) -> None:
        self.column = self._nearest(self.column + 1)


# --- the editor view's fields -------------------------------------------------


@dataclass(frozen=True)
class Field:
    """One row of the editor view: a part of the agent, and how it is changed."""

    label: str
    # One of READ_ONLY, TEXT, TOGGLE, MULTI_LINE, SECRET.
    kind: str
    # What the definition holds now; for a secret, the secret's name only.
    value: str | int | bool = ""
    # The key of the `[[agents]]` table the field writes; empty when it writes none.
    key: str = ""
    # The number of the agent's `[[agents]]` table in the config file.
    index: int = 0
    # What is said beside a field that is not typed into here.
    note: str = ""
    # The one-line complaint for a typed value; None accepts every text.
    check: Complaint | None = None

    @property
    def editable(self) -> bool:
        """Whether the field writes a key of the definition."""
        return self.kind in (TEXT, TOGGLE, MULTI_LINE)

    @property
    def text(self) -> str:
        """The value as it stands in a text field."""
        if isinstance(self.value, bool):
            return "on" if self.value else "off"
        return str(self.value)

    @property
    def shown(self) -> str:
        """The value on one line, as the field's row shows it."""
        if self.kind != MULTI_LINE:
            return self.text
        lines = self.text.splitlines()
        if not lines:
            return "(empty)"
        count = f"{len(lines)} line{'s' if len(lines) != 1 else ''}"
        return f"{count}: {lines[0]}"

    @property
    def hint(self) -> str:
        """What Enter does on the field, in one line."""
        where = f"[[agents]] {self.key}"
        if self.kind == TEXT:
            return f"Enter changes {where}, written at once"
        if self.kind == TOGGLE:
            turn = "off" if self.value else "on"
            return f"Enter turns {where} {turn}, written at once"
        if self.kind == MULTI_LINE:
            return f"Enter opens the text; Esc closes it and writes {where}"
        if self.kind == SECRET:
            return "Enter opens a masked field: paste the key for Secret Manager only"
        return self.note or CODE_NOTE

    def complaint(self, text: str) -> str | None:
        """The one line that says why `text` is refused; None when it is accepted."""
        return self.check(text) if self.check else None

    def change(self, text: str = "") -> Change:
        """The change that writes the field: `text` as typed, or the toggle flipped.

        A whole number is written as one, a line of text without the spaces
        around it, and a multi-line text as it stands.
        """
        if self.kind == TOGGLE:
            return Change(TABLE, self.key, not self.value, self.index)
        if self.kind == MULTI_LINE:
            return Change(TABLE, self.key, text, self.index)
        if self.kind != TEXT:
            raise ValueError(f"{self.label} is not written to the config file")
        if isinstance(self.value, int):
            return Change(TABLE, self.key, int(text.strip()), self.index)
        return Change(TABLE, self.key, text.strip(), self.index)


def commands_text(name: str, routes: Sequence[RouteConfig]) -> str:
    """The commands that lead to the agent `name`, each with its mode in brackets."""
    commands = [
        f"{route.command} ({route.mode})" if route.mode else route.command
        for route in routes
        if route.agent == name
    ]
    return ", ".join(commands) if commands else "(none)"


def _setting(key: str, definition: AgentConfig, index: int) -> Field:
    """The field of one of the settings an agent owns."""
    value = getattr(definition, key)
    if key == "secret":
        return Field("API key", SECRET, value, note=SECRET_NOTE)
    if key == "commands":
        return Field(key, MULTI_LINE, value, key, index)
    checks: dict[str, Complaint] = {
        "zip_code": zip_code_complaint,
        "daily_limit": limit_complaint,
        "minute_limit": limit_complaint,
        "folder": empty_complaint,
    }
    return Field(key, TEXT, value, key, index, check=checks.get(key))


def fields(
    agent: Agent,
    definition: AgentConfig,
    index: int,
    routes: Sequence[RouteConfig] = (),
) -> list[Field]:
    """The editor view's rows for one agent, in order (AG-10).

    `index` is the number of the agent's `[[agents]]` table in the config
    file, and `routes` the rules its commands are read from.
    """
    rows = [
        Field("Name", READ_ONLY, definition.name),
        Field("Description", TEXT, definition.description, "description", index),
        Field(
            "Commands",
            READ_ONLY,
            commands_text(definition.name, routes),
            note=COMMANDS_NOTE,
        ),
        Field("Language model", READ_ONLY, "yes" if agent.uses_model else "no"),
    ]
    if agent.uses_model or agent.name == AGENT_INTRODUCE:
        label = "Script" if agent.name == AGENT_INTRODUCE else "Prompt"
        rows.append(Field(label, MULTI_LINE, definition.prompt, "prompt", index))
    rows += [
        Field(
            "Confirm on the way in",
            TOGGLE,
            definition.confirm_entry,
            "confirm_entry",
            index,
        ),
        Field(
            "Question",
            TEXT,
            definition.confirm_entry_question,
            "confirm_entry_question",
            index,
            check=empty_complaint,
        ),
    ]
    if agent.sends_requests:
        rows += [
            Field(
                "Confirm before a request",
                TOGGLE,
                definition.confirm_request,
                "confirm_request",
                index,
            ),
            Field(
                "Question",
                TEXT,
                definition.confirm_request_question,
                "confirm_request_question",
                index,
                check=empty_complaint,
            ),
        ]
    rows.append(Field("Listening", TOGGLE, definition.listening, "listening", index))
    rows += [_setting(key, definition, index) for key in agent.settings]
    return rows


def table_index(text: str, name: str) -> int | None:
    """The number of the `[[agents]]` table named `name` in the config file's text.

    None when the file has no table for the agent, whose definition is then
    a default. Raises `ValueError` when the text is not TOML.
    """
    tables = tomllib.loads(text).get(TABLE, [])
    if not isinstance(tables, list):
        return None
    for index, table in enumerate(tables):
        if isinstance(table, dict) and str(table.get("name", "")).strip() == name:
            return index
    return None


# --- the multi-line field -----------------------------------------------------


class TextArea:
    """A multi-line text field: its lines, its cursor, and the window on them.

    `typing` is true from `open` to `close`: the field then has the
    keyboard, so every printable key, `T` among them, is a letter (TS-13).
    """

    def __init__(self) -> None:
        self.typing = False
        self.lines: list[str] = [""]
        # The cursor: a line, and the column in it the next character goes to.
        self.row = 0
        self.col = 0
        # The first line and the first column the window shows.
        self.top = 0
        self.left = 0

    def open(self, text: str) -> None:
        """Take `text` to edit, with the cursor at its start."""
        self.lines = text.split("\n")
        self.row = self.col = self.top = self.left = 0
        self.typing = True

    def close(self) -> None:
        self.typing = False

    def text(self) -> str:
        return "\n".join(self.lines)

    def insert(self, text: str) -> None:
        """Put `text`, which holds no newline, at the cursor."""
        line = self.lines[self.row]
        self.lines[self.row] = line[: self.col] + text + line[self.col :]
        self.col += len(text)

    def newline(self) -> None:
        """Break the line at the cursor."""
        line = self.lines[self.row]
        self.lines[self.row : self.row + 1] = [line[: self.col], line[self.col :]]
        self.row += 1
        self.col = 0

    def backspace(self) -> None:
        """Delete the character before the cursor; at a line's start, join it to the one above."""
        line = self.lines[self.row]
        if self.col > 0:
            self.lines[self.row] = line[: self.col - 1] + line[self.col :]
            self.col -= 1
        elif self.row > 0:
            above = self.lines[self.row - 1]
            self.lines[self.row - 1 : self.row + 1] = [above + line]
            self.row -= 1
            self.col = len(above)

    def delete(self) -> None:
        """Delete the character at the cursor; at a line's end, join the one below to it."""
        line = self.lines[self.row]
        if self.col < len(line):
            self.lines[self.row] = line[: self.col] + line[self.col + 1 :]
        elif self.row < len(self.lines) - 1:
            self.lines[self.row : self.row + 2] = [line + self.lines[self.row + 1]]

    def move_left(self) -> None:
        if self.col > 0:
            self.col -= 1
        elif self.row > 0:
            self.row -= 1
            self.col = len(self.lines[self.row])

    def move_right(self) -> None:
        if self.col < len(self.lines[self.row]):
            self.col += 1
        elif self.row < len(self.lines) - 1:
            self.row += 1
            self.col = 0

    def move_up(self) -> None:
        self._to_row(self.row - 1)

    def move_down(self) -> None:
        self._to_row(self.row + 1)

    def _to_row(self, row: int) -> None:
        self.row = max(0, min(row, len(self.lines) - 1))
        self.col = min(self.col, len(self.lines[self.row]))

    def home(self) -> None:
        self.col = 0

    def end(self) -> None:
        self.col = len(self.lines[self.row])

    def window(self, height: int, width: int) -> list[str]:
        """The lines a window of `height` by `width` shows, moved to hold the cursor."""
        height = max(1, height)
        width = max(1, width)
        if self.row < self.top:
            self.top = self.row
        elif self.row >= self.top + height:
            self.top = self.row - height + 1
        self.top = max(0, min(self.top, max(0, len(self.lines) - height)))
        if self.col < self.left:
            self.left = self.col
        elif self.col >= self.left + width:
            self.left = self.col - width + 1
        return [
            line[self.left : self.left + width]
            for line in self.lines[self.top : self.top + height]
        ]
