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
headers and one-line scalar values. Anything else (a multi-line string, a
dotted key) fails verification with a `ConfigEditError` instead of being
written.
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

type Scalar = str | int | float | bool


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
        return f"[{where}] {self.key} = {format_value(self.value)}"


_HEADER = re.compile(r"^\s*(\[\[|\[)\s*([A-Za-z0-9_.-]+)\s*(\]\]|\])\s*(#.*)?$")
# A one-line scalar: a basic string, a literal string, or a bare token.
_VALUE = r"""(?:"(?:[^"\\]|\\.)*"|'[^']*'|[^\s#"']+)"""


def format_value(value: Scalar) -> str:
    """A Python scalar as a TOML value."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ConfigEditError(f"cannot write the number {value}")
        return repr(value)
    # A JSON string is a valid TOML basic string.
    return json.dumps(value, ensure_ascii=False)


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
    key_line = re.compile(
        rf"^(\s*{re.escape(change.key)}\s*=\s*)({_VALUE})(\s*(?:#.*)?)$"
    )
    any_key = re.compile(rf"^\s*{re.escape(change.key)}\s*=")
    last_content = start - 1
    for number in range(start, end):
        body = lines[number].rstrip("\r\n")
        ending = lines[number][len(body) :]
        match = key_line.match(body)
        if match:
            lines[number] = (
                match.group(1) + format_value(change.value) + match.group(3) + ending
            )
            return "".join(lines)
        if any_key.match(body):
            raise ConfigEditError(
                f"[{change.table}] {change.key} is not a one-line value; "
                "edit the config file by hand"
            )
        if body.strip() and not body.lstrip().startswith("#"):
            last_content = number
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
