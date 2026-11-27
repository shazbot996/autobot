"""Drawing primitives: the theme's roles, the line-drawing glyphs, the canvas.

Everything the interface draws goes through `Canvas.put`, which clips to the
terminal and never raises, so a resize or a tiny terminal cannot crash a
screen. `Canvas` only needs `getmaxyx`, `addstr`, `erase` and `refresh` from
the screen it is given (the application also sets its background with `bkgd`), which is what lets tests drive the whole interface
with a fake screen instead of a terminal.
"""

from __future__ import annotations

import curses
import textwrap
from collections.abc import Callable, Sequence
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autobot.tui.themes import Palette


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


# Builds one curses colour pair: (pair number, foreground, background).
type InitPair = Callable[[int, int, int], None]
# The attribute of a colour pair, by number.
type ColourPair = Callable[[int], int]


@dataclass(frozen=True)
class Theme:
    """Curses attributes, by role.

    A screen asks for a role, never for a colour, so a theme is only a
    palette (`autobot.tui.themes`): `from_palette` turns one into attributes.
    """

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
    # The banner's block letters, and the line beside them.
    banner: int
    accent: int
    # The single-line frames of the panels inside a screen.
    panel: int
    # The text a recording session asks for, in its box.
    prompt: int
    # The listener's state, as a filled badge.
    badge_ok: int
    badge_warning: int
    badge_error: int
    badge_info: int
    # The palette's name; empty for the mono theme.
    name: str = ""

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
            banner=bold,
            accent=curses.A_NORMAL,
            panel=curses.A_NORMAL,
            prompt=reverse | bold,
            badge_ok=reverse,
            badge_warning=reverse,
            badge_error=reverse | bold,
            badge_info=reverse,
        )

    @classmethod
    def from_palette(
        cls,
        palette: Palette,
        init_pair: InitPair | None = None,
        colour_pair: ColourPair | None = None,
    ) -> Theme:
        """The attributes of `palette`, after initialising its colour pairs.

        Pairs are numbered from 1 in the order the roles first use them, so
        switching palettes redefines the same few pairs. Needs curses started
        with colours, unless `init_pair` and `colour_pair` stand in for it
        (tests do that).
        """
        init_pair = init_pair or curses.init_pair
        colour_pair = colour_pair or curses.color_pair
        numbers: dict[tuple[int, int], int] = {}
        attributes: dict[str, int] = {}
        for role in theme_roles():
            ink = palette.inks[role]
            colours = (ink.foreground, ink.background)
            if colours not in numbers:
                numbers[colours] = len(numbers) + 1
                init_pair(numbers[colours], *colours)
            attributes[role] = colour_pair(numbers[colours]) | (
                curses.A_BOLD if ink.bold else 0
            )
        return cls(name=palette.name, **attributes)


def theme_roles() -> tuple[str, ...]:
    """The roles a palette must colour: every `Theme` field but its name."""
    return tuple(field.name for field in fields(Theme) if field.name != "name")


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
    # The single-line frame of a panel inside a screen; its edge is `rule`.
    panel_top_left: str = "+"
    panel_top_right: str = "+"
    panel_bottom_left: str = "+"
    panel_bottom_right: str = "+"
    panel_vertical: str = "|"
    # Whether block letters (the banner) can be drawn.
    block_art: bool = False

    @classmethod
    def unicode(cls) -> Glyphs:
        return cls(
            *("╔", "╗", "╚", "╝", "═", "║", "─", "█", "░", "▌", "►", "●", "↑↓"),
            *("┌", "┐", "└", "┘", "│"),
            block_art=True,
        )

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
        glyphs = self.glyphs
        self._frame(
            rect,
            title,
            attr,
            (glyphs.top_left, glyphs.top_right),
            (glyphs.bottom_left, glyphs.bottom_right),
            glyphs.horizontal,
            glyphs.vertical,
        )

    def panel(
        self,
        rect: Rect,
        title: str = "",
        attr: int | None = None,
        title_attr: int | None = None,
    ) -> Rect:
        """A single-line frame around `rect`, for a panel inside a screen.

        Returns the inside, one column in from the frame on each side.
        """
        glyphs = self.glyphs
        self._frame(
            rect,
            title,
            self.theme.panel if attr is None else attr,
            (glyphs.panel_top_left, glyphs.panel_top_right),
            (glyphs.panel_bottom_left, glyphs.panel_bottom_right),
            glyphs.rule,
            glyphs.panel_vertical,
            title_attr,
        )
        return rect.inset(1, 2)

    def _frame(
        self,
        rect: Rect,
        title: str,
        attr: int | None,
        top: tuple[str, str],
        bottom: tuple[str, str],
        horizontal: str,
        vertical: str,
        title_attr: int | None = None,
    ) -> None:
        if rect.height < 2 or rect.width < 2:
            return
        inner = rect.width - 2
        self.put(rect.y, rect.x, top[0] + horizontal * inner + top[1], attr)
        for row in range(rect.y + 1, rect.bottom - 1):
            self.put(row, rect.x, vertical, attr)
            self.put(row, rect.right - 1, vertical, attr)
        self.put(
            rect.bottom - 1, rect.x, bottom[0] + horizontal * inner + bottom[1], attr
        )
        if title and inner > 4:
            self.put(
                rect.y,
                rect.x + 2,
                f" {title[: inner - 4]} ",
                attr if title_attr is None else title_attr,
            )


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
