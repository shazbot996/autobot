"""Drawing primitives: the colour theme, the line-drawing glyphs, the canvas.

Everything the interface draws goes through `Canvas.put`, which clips to the
terminal and never raises, so a resize or a tiny terminal cannot crash a
screen. `Canvas` only needs `getmaxyx`, `addstr`, `erase` and `refresh` from
the screen it is given, which is what lets tests drive the whole interface
with a fake screen instead of a terminal.
"""

from __future__ import annotations

import curses
import textwrap
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Rect:
    y: int
    x: int
    height: int
    width: int

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @property
    def right(self) -> int:
        return self.x + self.width

    def inset(self, rows: int = 1, cols: int = 1) -> Rect:
        return Rect(
            self.y + rows,
            self.x + cols,
            max(0, self.height - 2 * rows),
            max(0, self.width - 2 * cols),
        )


@dataclass(frozen=True)
class Theme:
    """Curses attributes, by role."""

    normal: int
    title: int
    key: int
    key_label: int
    selected: int
    frame: int
    heading: int
    value: int
    dim: int
    warning: int
    error: int
    ok: int
    dialog: int
    dialog_selected: int
    dialog_error: int
    field: int
    shadow: int
    meter_low: int
    meter_mid: int
    meter_high: int

    @classmethod
    def mono(cls) -> Theme:
        """For terminals without colour: reverse video and bold only."""
        bold = curses.A_BOLD
        reverse = curses.A_REVERSE
        return cls(
            normal=curses.A_NORMAL,
            title=reverse,
            key=bold,
            key_label=curses.A_NORMAL,
            selected=reverse,
            frame=curses.A_NORMAL,
            heading=bold,
            value=bold,
            dim=curses.A_DIM,
            warning=bold,
            error=reverse | bold,
            ok=bold,
            dialog=reverse,
            dialog_selected=bold,
            dialog_error=reverse | bold,
            field=curses.A_UNDERLINE,
            shadow=curses.A_NORMAL,
            meter_low=curses.A_NORMAL,
            meter_mid=bold,
            meter_high=bold,
        )

    @classmethod
    def colour(cls) -> Theme:
        """The 90s palette: white on blue, cyan bars. Needs curses started."""
        pairs = {
            1: (curses.COLOR_WHITE, curses.COLOR_BLUE),
            2: (curses.COLOR_BLACK, curses.COLOR_CYAN),
            3: (curses.COLOR_WHITE, curses.COLOR_BLACK),
            4: (curses.COLOR_YELLOW, curses.COLOR_BLUE),
            5: (curses.COLOR_WHITE, curses.COLOR_RED),
            6: (curses.COLOR_BLACK, curses.COLOR_WHITE),
            7: (curses.COLOR_GREEN, curses.COLOR_BLUE),
            8: (curses.COLOR_RED, curses.COLOR_BLUE),
            9: (curses.COLOR_CYAN, curses.COLOR_BLUE),
            10: (curses.COLOR_RED, curses.COLOR_WHITE),
        }
        for number, (foreground, background) in pairs.items():
            curses.init_pair(number, foreground, background)
        pair = curses.color_pair
        bold = curses.A_BOLD
        return cls(
            normal=pair(1),
            title=pair(2),
            key=pair(3) | bold,
            key_label=pair(2),
            selected=pair(2),
            frame=pair(9) | bold,
            heading=pair(4) | bold,
            value=pair(1) | bold,
            dim=pair(9),
            warning=pair(4) | bold,
            error=pair(5) | bold,
            ok=pair(7) | bold,
            dialog=pair(6),
            dialog_selected=pair(3) | bold,
            dialog_error=pair(10),
            field=pair(3) | bold,
            shadow=pair(3),
            meter_low=pair(7) | bold,
            meter_mid=pair(4) | bold,
            meter_high=pair(8) | bold,
        )


@dataclass(frozen=True)
class Glyphs:
    top_left: str
    top_right: str
    bottom_left: str
    bottom_right: str
    horizontal: str
    vertical: str
    rule: str
    full: str
    empty: str
    hold: str
    pointer: str
    bullet: str
    up_down: str

    @classmethod
    def unicode(cls) -> Glyphs:
        return cls("╔", "╗", "╚", "╝", "═", "║", "─", "█", "░", "▌", "►", "●", "↑↓")

    @classmethod
    def ascii(cls) -> Glyphs:
        return cls("+", "+", "+", "+", "=", "|", "-", "#", ".", "|", ">", "*", "Up/Dn")


class Canvas:
    """A clipped drawing surface over a curses window (or a fake one)."""

    def __init__(self, screen: Any, theme: Theme, glyphs: Glyphs) -> None:
        self.screen = screen
        self.theme = theme
        self.glyphs = glyphs

    def size(self) -> tuple[int, int]:
        height, width = self.screen.getmaxyx()
        return int(height), int(width)

    def erase(self) -> None:
        self.screen.erase()

    def refresh(self) -> None:
        self.screen.refresh()

    def put(self, y: int, x: int, text: str, attr: int | None = None) -> None:
        """Write `text` at (y, x), clipped to the screen. Never raises."""
        height, width = self.size()
        if not 0 <= y < height or x >= width or not text:
            return
        if x < 0:
            text = text[-x:]
            x = 0
        text = text[: width - x]
        if not text:
            return
        try:
            self.screen.addstr(y, x, text, self.theme.normal if attr is None else attr)
        except curses.error:
            # Writing the bottom-right cell moves the cursor off the screen,
            # which curses reports as an error after drawing the text.
            pass

    def put_in(
        self, rect: Rect, row: int, col: int, text: str, attr: int | None = None
    ) -> None:
        """Write inside `rect`, clipped to it; (row, col) are relative."""
        if not 0 <= row < rect.height or col >= rect.width:
            return
        self.put(rect.y + row, rect.x + col, text[: max(0, rect.width - col)], attr)

    def fill(self, rect: Rect, attr: int | None = None) -> None:
        for row in range(rect.height):
            self.put(rect.y + row, rect.x, " " * rect.width, attr)

    def rule(self, rect: Rect, row: int, attr: int | None = None) -> None:
        self.put_in(rect, row, 0, self.glyphs.rule * rect.width, attr)

    def box(self, rect: Rect, title: str = "", attr: int | None = None) -> None:
        """A double-line frame around `rect`, with the title set into the top edge."""
        if rect.height < 2 or rect.width < 2:
            return
        glyphs = self.glyphs
        inner = rect.width - 2
        self.put(
            rect.y,
            rect.x,
            glyphs.top_left + glyphs.horizontal * inner + glyphs.top_right,
            attr,
        )
        for row in range(rect.y + 1, rect.bottom - 1):
            self.put(row, rect.x, glyphs.vertical, attr)
            self.put(row, rect.right - 1, glyphs.vertical, attr)
        self.put(
            rect.bottom - 1,
            rect.x,
            glyphs.bottom_left + glyphs.horizontal * inner + glyphs.bottom_right,
            attr,
        )
        if title and inner > 4:
            self.put(rect.y, rect.x + 2, f" {title[: inner - 4]} ", attr)


def wrap(lines: Sequence[str], width: int) -> list[str]:
    """`lines` folded to `width`, continuation lines indented."""
    if width < 8:
        return [fit(line, width) for line in lines]
    folded: list[str] = []
    for line in lines:
        folded.extend(
            textwrap.wrap(
                line,
                width,
                subsequent_indent="    ",
                break_long_words=True,
                break_on_hyphens=False,
            )
            or [""]
        )
    return folded


def fit(text: str, width: int) -> str:
    """`text` cut to `width`, ending in `~` when something was cut off."""
    if width <= 0:
        return ""
    return text if len(text) <= width else text[: width - 1] + "~"


def fit_left(text: str, width: int) -> str:
    """`text` cut to `width` from the left, as a long path is: `~end/of/path`."""
    if width <= 0:
        return ""
    return text if len(text) <= width else "~" + text[len(text) - width + 1 :]
