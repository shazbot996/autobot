"""The colour themes: *light*, *dark* and *sun*. No drawing.

A theme is only a palette: for every role a screen can ask for
(`autobot.tui.ui.Theme`), a foreground, a background and whether it is bold.
`Theme.from_palette` turns one into curses attributes. A new theme is one more
entry in `PALETTES` and one more name in `autobot.config.TUI_THEMES`.

Only the eight standard curses colours are used, so every theme works on the
Linux console and over SSH as well as in a terminal emulator. Bold is the
bright version of the foreground there, which is why dark text on a light
background is never bold.

- *light*: black on a light grey page, blue bars, magenta panels.
- *dark*: the mid-90s setup-utility look: white on deep blue, cyan bars,
  yellow headings.
- *sun*: for a bright room. Black page, bold white and bold yellow text and
  nothing dim: the highest contrast the eight colours give.
"""

from __future__ import annotations

import curses
from collections.abc import Mapping
from dataclasses import dataclass

from autobot.config import TUI_THEMES

BLACK = curses.COLOR_BLACK
RED = curses.COLOR_RED
GREEN = curses.COLOR_GREEN
YELLOW = curses.COLOR_YELLOW
BLUE = curses.COLOR_BLUE
MAGENTA = curses.COLOR_MAGENTA
CYAN = curses.COLOR_CYAN
WHITE = curses.COLOR_WHITE


@dataclass(frozen=True)
class Ink:
    """How one role is drawn: two of the eight colours, and bold or not."""

    foreground: int
    background: int
    bold: bool = False


@dataclass(frozen=True)
class Palette:
    name: str
    # An `Ink` for every role in `autobot.tui.ui.theme_roles()`.
    inks: Mapping[str, Ink]

    def pairs(self) -> int:
        """How many curses colour pairs the palette needs."""
        return len({(ink.foreground, ink.background) for ink in self.inks.values()})


LIGHT = Palette(
    "light",
    {
        "normal": Ink(BLACK, WHITE),
        "title": Ink(WHITE, BLUE, True),
        "key": Ink(WHITE, BLUE, True),
        "key_label": Ink(BLACK, CYAN),
        "selected": Ink(WHITE, BLUE, True),
        "frame": Ink(BLUE, WHITE),
        "heading": Ink(MAGENTA, WHITE),
        "value": Ink(BLUE, WHITE),
        # Bold black is dark grey where bold brightens, and plain bold elsewhere.
        "dim": Ink(BLACK, WHITE, True),
        "warning": Ink(RED, WHITE),
        "error": Ink(WHITE, RED, True),
        "ok": Ink(GREEN, WHITE),
        "dialog": Ink(BLACK, CYAN),
        "dialog_selected": Ink(WHITE, BLUE, True),
        "dialog_error": Ink(WHITE, RED, True),
        "field": Ink(BLACK, WHITE),
        "shadow": Ink(WHITE, BLACK),
        "meter_low": Ink(GREEN, WHITE),
        "meter_mid": Ink(YELLOW, WHITE),
        "meter_high": Ink(RED, WHITE),
        "banner": Ink(BLUE, WHITE),
        "accent": Ink(RED, WHITE),
        "panel": Ink(MAGENTA, WHITE),
        "prompt": Ink(WHITE, BLUE, True),
        "badge_ok": Ink(BLACK, GREEN),
        "badge_warning": Ink(BLACK, YELLOW),
        "badge_error": Ink(WHITE, RED, True),
        "badge_info": Ink(BLACK, CYAN),
        "wake": Ink(RED, WHITE, True),
    },
)

DARK = Palette(
    "dark",
    {
        "normal": Ink(WHITE, BLUE),
        "title": Ink(BLACK, CYAN),
        "key": Ink(WHITE, BLACK, True),
        "key_label": Ink(BLACK, CYAN),
        "selected": Ink(BLACK, CYAN),
        "frame": Ink(CYAN, BLUE, True),
        "heading": Ink(YELLOW, BLUE, True),
        "value": Ink(WHITE, BLUE, True),
        "dim": Ink(CYAN, BLUE),
        "warning": Ink(YELLOW, BLUE, True),
        "error": Ink(WHITE, RED, True),
        "ok": Ink(GREEN, BLUE, True),
        "dialog": Ink(BLACK, WHITE),
        "dialog_selected": Ink(WHITE, BLACK, True),
        "dialog_error": Ink(RED, WHITE),
        "field": Ink(WHITE, BLACK, True),
        "shadow": Ink(WHITE, BLACK),
        "meter_low": Ink(GREEN, BLUE, True),
        "meter_mid": Ink(YELLOW, BLUE, True),
        "meter_high": Ink(RED, BLUE, True),
        "banner": Ink(YELLOW, BLUE, True),
        "accent": Ink(CYAN, BLUE, True),
        "panel": Ink(MAGENTA, BLUE, True),
        "prompt": Ink(WHITE, MAGENTA, True),
        "badge_ok": Ink(BLACK, GREEN),
        "badge_warning": Ink(BLACK, YELLOW),
        "badge_error": Ink(WHITE, RED, True),
        "badge_info": Ink(BLACK, CYAN),
        "wake": Ink(RED, BLUE, True),
    },
)

SUN = Palette(
    "sun",
    {
        "normal": Ink(WHITE, BLACK, True),
        "title": Ink(BLACK, YELLOW),
        "key": Ink(YELLOW, BLACK, True),
        "key_label": Ink(BLACK, WHITE),
        "selected": Ink(BLACK, YELLOW),
        "frame": Ink(YELLOW, BLACK, True),
        "heading": Ink(YELLOW, BLACK, True),
        "value": Ink(CYAN, BLACK, True),
        # Nothing is dim in a bright room: plain white is the faintest ink.
        "dim": Ink(WHITE, BLACK),
        "warning": Ink(BLACK, YELLOW),
        "error": Ink(WHITE, RED, True),
        "ok": Ink(GREEN, BLACK, True),
        "dialog": Ink(BLACK, WHITE),
        "dialog_selected": Ink(BLACK, YELLOW),
        "dialog_error": Ink(WHITE, RED, True),
        "field": Ink(WHITE, BLACK, True),
        "shadow": Ink(BLACK, YELLOW),
        "meter_low": Ink(GREEN, BLACK, True),
        "meter_mid": Ink(YELLOW, BLACK, True),
        "meter_high": Ink(RED, BLACK, True),
        "banner": Ink(YELLOW, BLACK, True),
        "accent": Ink(WHITE, BLACK, True),
        "panel": Ink(WHITE, BLACK, True),
        "prompt": Ink(BLACK, YELLOW),
        "badge_ok": Ink(BLACK, GREEN),
        "badge_warning": Ink(BLACK, YELLOW),
        "badge_error": Ink(WHITE, RED, True),
        "badge_info": Ink(BLACK, WHITE),
        "wake": Ink(RED, BLACK, True),
    },
)

# In the order `T` cycles them, which is `TUI_THEMES`'.
PALETTES: dict[str, Palette] = {palette.name: palette for palette in (LIGHT, DARK, SUN)}
assert tuple(PALETTES) == TUI_THEMES

# The most colour pairs any palette needs: what the terminal must offer.
PAIRS_NEEDED = max(palette.pairs() for palette in PALETTES.values())


def next_theme(name: str) -> str:
    """The theme after `name`, wrapping from the last to the first.

    An unknown name gives the first theme.
    """
    names = list(PALETTES)
    if name not in names:
        return names[0]
    return names[(names.index(name) + 1) % len(names)]
