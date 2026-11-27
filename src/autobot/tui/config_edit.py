"""Write changes back to `config/autobot.toml` without losing its comments.

`tomllib` reads TOML but cannot write it, and the config file carries
explanatory comments that must survive. So this module edits the file's text:
it replaces the value on one `key = value` line, inside one table, and leaves
every other byte alone.

Editing text by hand is only safe when it is checked, so every edit is
verified before the file is replaced:

1. the new text is parsed with `tomllib`;
2. the parsed result must equal the old parsed data with exactly the requested
   changes applied (so a comment, an unrelated key or another table cannot
   have been disturbed);
3. the result must pass `autobot.config.parse_config`.

Only then is the file replaced, atomically: the text goes to a temporary file
in the same directory, which is renamed over the original.

The editor handles what the config file uses: `[table]` and `[[array]]`
headers, one-line scalar values, one-line arrays of strings (the word lists)
and multi-line basic strings (the agents' prompts), which it replaces as a
block. A multi-line string of another key is skipped whole, so a line inside
it is never taken for a key. Anything else (a multi-line literal string, a
dotted key, an array over several lines) fails with a `ConfigEditError`
instead of being written.
"""

from __future__ import annotations

import copy
import json
import math
import os
import re
import tempfile
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from autobot.config import Config, ConfigError, load_config, parse_config

type Scalar = str | int | float | bool | list[str]


class ConfigEditError(Exception):
    """A change could not be written. The message is one line."""


@dataclass(frozen=True)
class Change:
    """Set `key` in `table` to `value`.

    `index` is None for a `[table]`, and the zero-based entry number for an
    `[[array of tables]]` such as `wake_phrases`.
    """

    table: str
    key: str
    value: Scalar
    index: int | None = None

    def describe(self) -> str:
        where = self.table if self.index is None else f"{self.table} #{self.index + 1}"
        # A multi-line string is shown on one line, escaped, so a message
        # stays one line.
        shown = (
            json.dumps(self.value, ensure_ascii=False)
            if isinstance(self.value, str)
            else format_value(self.value)
        )
        return f"[{where}] {self.key} = {shown}"


_HEADER = re.compile(r"^\s*(\[\[|\[)\s*([A-Za-z0-9_.-]+)\s*(\]\]|\])\s*(#.*)?$")
# A one-line scalar: a basic string, a literal string, or a bare token; or a
# one-line array with no `]` inside a string.
_VALUE = r"""(?:"(?:[^"\\]|\\.)*"|'[^']*'|[^\s#"'\[]+|\[[^\]]*\])"""
# What may follow a value on its line: spaces and a comment.
_TRAILING = re.compile(r"\s*(?:#.*)?")
# Any `key =` at the start of a line, and what follows the `=`.
_ANY_KEY = re.compile(r"^\s*[A-Za-z0-9_-]+\s*=\s*")
# The delimiters of a multi-line string: basic, then literal.
_MULTI_LINE = ('"""', "'''")


def format_value(value: Scalar) -> str:
    """A Python value as a TOML value.

    A list is a one-line array of basic strings, and a string holding a
    newline a multi-line basic string with its delimiters on lines of their own.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ConfigEditError(f"cannot write the number {value}")
        return repr(value)
    if isinstance(value, list):
        return (
            "["
            + ", ".join(json.dumps(item, ensure_ascii=False) for item in value)
            + "]"
        )
    if "\n" in value:
        return _multi_line(value)
    # A JSON string is a valid TOML basic string.
    return json.dumps(value, ensure_ascii=False)


def _multi_line(value: str) -> str:
    """`value` as a multi-line basic string: three quotes, its lines, three quotes.

    The newline after the opening delimiter is not part of the value. When the
    value does not end in a newline, its last line ends in a backslash, which
    joins it to the closing delimiter.
    """
    body: list[str] = []
    quotes = 0
    for char in value:
        quotes = quotes + 1 if char == '"' else 0
        if char == "\\":
            body.append("\\\\")
        elif char == '"' and quotes == 3:
            # Every third quote in a row is escaped, so none closes the string.
            body.append('\\"')
            quotes = 0
        elif char == "\n" or char == "\t":
            body.append(char)
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            body.append(f"\\u{ord(char):04X}")
        else:
            body.append(char)
    text = "".join(body)
    end = "" if value.endswith("\n") else "\\\n"
    return f'"""\n{text}{end}"""'


def _string_end(text: str, start: int, delimiter: str) -> int | None:
    """Where the multi-line string whose body begins at `start` ends.

    The index just past its closing delimiter, which takes up to two more
    quotes of the body before it; None when it is never closed.
    """
    index = start
    while index < len(text):
        if delimiter == '"""' and text[index] == "\\":
            index += 2
            continue
        if text.startswith(delimiter, index):
            end = index + 3
            for _ in range(2):
                if text.startswith(delimiter[0], end):
                    end += 1
            return end
        index += 1
    return None


def _block_end(
    lines: Sequence[str], number: int, start: int, delimiter: str
) -> tuple[int, int] | None:
    """The line a multi-line string opened on line `number` closes on.

    `start` is the column its body begins at. Returns the closing line's number
    and the column just past its delimiter; None when it is never closed.
    """
    text = "".join(lines[number:])
    end = _string_end(text, start, delimiter)
    if end is None:
        return None
    last = number + text.count("\n", 0, end)
    column = end - (text.rfind("\n", 0, end) + 1)
    return last, column


def _table_span(lines: Sequence[str], change: Change) -> tuple[int, int] | None:
    """The lines of the table `change` names: (first line after header, end)."""
    start: int | None = None
    seen = -1
    for number, line in enumerate(lines):
        match = _HEADER.match(line)
        if not match:
            continue
        if start is not None:
            return start, number
        opener, name, closer = match.group(1), match.group(2), match.group(3)
        if name != change.table or len(opener) != len(closer):
            continue
        if change.index is None and opener == "[":
            start = number + 1
        elif change.index is not None and opener == "[[":
            seen += 1
            if seen == change.index:
                start = number + 1
    return None if start is None else (start, len(lines))


def set_value(text: str, change: Change) -> str:
    """Return `text` with `change` applied. Does not verify: see `apply_changes`."""
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    new_line = f"{change.key} = {format_value(change.value)}\n"
    span = _table_span(lines, change)
    if span is None:
        if change.index is not None:
            raise ConfigEditError(
                f"no [[{change.table}]] entry #{change.index + 1} in the config file"
            )
        gap = [] if not lines or not lines[-1].strip() else ["\n"]
        return "".join([*lines, *gap, f"[{change.table}]\n", new_line])

    start, end = span
    key_line = re.compile(rf"^(\s*{re.escape(change.key)}\s*=\s*)")
    one_line = re.compile(rf"({_VALUE})(\s*(?:#.*)?)$")
    last_content = start - 1
    number = start
    while number < end:
        body = lines[number].rstrip("\r\n")
        ending = lines[number][len(body) :]
        assignment = _ANY_KEY.match(body)
        opener = body[assignment.end() : assignment.end() + 3] if assignment else ""
        block = None
        if opener in _MULTI_LINE and assignment:
            block = _block_end(lines[:end], number, assignment.end() + 3, opener)
        key = key_line.match(body)
        if key:
            value = format_value(change.value).replace("\n", ending or "\n")
            rest = body[key.end() :]
            match = one_line.match(rest)
            if match:
                lines[number] = key.group(1) + value + match.group(2) + ending
                return "".join(lines)
            if opener == '"""' and block is not None:
                last, column = block
                closing = lines[last].rstrip("\r\n")
                trailing = closing[column:]
                if _TRAILING.fullmatch(trailing):
                    tail = lines[last][len(closing) :] or "\n"
                    lines[number : last + 1] = [key.group(1) + value + trailing + tail]
                    return "".join(lines)
            raise ConfigEditError(
                f"[{change.table}] {change.key} is not a value the editor can "
                "rewrite; edit the config file by hand"
            )
        if block is not None:
            # Skip another key's multi-line string whole.
            last_content = number = block[0]
        elif body.strip() and not body.lstrip().startswith("#"):
            last_content = number
        number += 1
    lines.insert(last_content + 1, new_line)
    return "".join(lines)


def _expected(data: dict[str, object], changes: Sequence[Change]) -> dict[str, object]:
    """`data` with `changes` applied, as the edited file must parse."""
    result = copy.deepcopy(data)
    for change in changes:
        if change.index is None:
            table = result.setdefault(change.table, {})
        else:
            entries = result.get(change.table)
            if not isinstance(entries, list) or change.index >= len(entries):
                raise ConfigEditError(
                    f"no [[{change.table}]] entry #{change.index + 1} in the config file"
                )
            table = entries[change.index]
        if not isinstance(table, dict):
            raise ConfigEditError(f"[{change.table}] is not a table")
        table[change.key] = change.value
    return result


def apply_changes(text: str, changes: Sequence[Change]) -> str:
    """Apply `changes` to config text and verify the result.

    Raises `ConfigEditError` when the edit cannot be made safely and
    `ConfigError` when the resulting configuration is invalid.
    """
    try:
        before = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigEditError(f"the config file is not valid TOML: {error}") from None
    new_text = text
    for change in changes:
        new_text = set_value(new_text, change)
    try:
        after = tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigEditError(
            f"refusing to write: the edit would break the config file ({error})"
        ) from None
    if after != _expected(before, changes):
        raise ConfigEditError(
            "refusing to write: the edit would change more than "
            + ", ".join(change.describe() for change in changes)
        )
    parse_config(after)
    return new_text


def write_atomic(path: Path, text: str) -> None:
    """Replace `path` with `text` through a temporary file and a rename."""
    target = path.resolve()
    handle, temp_name = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as temp:
            temp.write(text)
            temp.flush()
            os.fsync(temp.fileno())
        try:
            os.chmod(temp_name, target.stat().st_mode & 0o777)
        except FileNotFoundError:
            pass
        os.replace(temp_name, target)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


class ConfigStore:
    """The config file as the terminal user interface sees it.

    It keeps nothing in memory: `load` reads the file and `update` re-reads it
    immediately before editing, so a change made elsewhere is never
    overwritten by a stale copy.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> Config:
        return load_config(self.path)

    def update(self, *changes: Change) -> Config:
        """Write `changes` to the file and return the configuration it now holds."""
        try:
            with self.path.open(encoding="utf-8", newline="") as handle:
                text = handle.read()
        except FileNotFoundError:
            raise ConfigError(f"config file not found: {self.path}") from None
        except (OSError, UnicodeDecodeError) as error:
            raise ConfigError(f"cannot read config file {self.path}: {error}") from None
        new_text = apply_changes(text, changes)
        if new_text != text:
            try:
                write_atomic(self.path, new_text)
            except OSError as error:
                raise ConfigEditError(
                    f"cannot write config file {self.path}: {error.strerror}"
                ) from None
        return self.load()
