"""`autobot.tui.themes`, the `T` key, and every screen drawn in every theme."""

import curses
from pathlib import Path

import pytest
from tui_fakes import (
    CONFIG_TEXT,
    ENTER,
    ESC,
    F1,
    Key,
    fake_colour_theme,
    make_harness,
    typed,
)

from autobot.config import TUI_THEMES, load_config
from autobot.tui.app import MIN_HEIGHT, MIN_WIDTH
from autobot.tui.screens.main import BANNER, BANNER_PLAIN, MENU_WIDTH, TAGLINE
from autobot.tui.themes import PAIRS_NEEDED, PALETTES, Ink, next_theme
from autobot.tui.ui import Glyphs, Theme, theme_roles

STANDARD_COLOURS = range(8)


def theme_in_file(path: Path) -> str:
    return load_config(path).tui.theme


def test_the_three_themes_in_the_order_t_cycles_them() -> None:
    assert tuple(PALETTES) == TUI_THEMES == ("light", "dark", "sun")
    assert [palette.name for palette in PALETTES.values()] == list(TUI_THEMES)


def test_next_theme_cycles_and_wraps() -> None:
    assert next_theme("light") == "dark"
    assert next_theme("dark") == "sun"
    assert next_theme("sun") == "light"
    assert next_theme("no such theme") == "light"


@pytest.mark.parametrize("name", TUI_THEMES)
def test_a_palette_colours_every_role_with_the_eight_standard_colours(
    name: str,
) -> None:
    palette = PALETTES[name]
    assert set(palette.inks) == set(theme_roles())
    for role, ink in palette.inks.items():
        assert isinstance(ink, Ink), role
        assert ink.foreground in STANDARD_COLOURS, role
        assert ink.background in STANDARD_COLOURS, role
        # Text must stand off its background.
        if role != "shadow":
            assert ink.foreground != ink.background, role
    # Few enough pairs for the Linux console (64) and an 8-colour xterm.
    assert palette.pairs() <= PAIRS_NEEDED < 64


def test_the_themes_differ_where_it_shows() -> None:
    bodies = {name: palette.inks["normal"] for name, palette in PALETTES.items()}
    assert len({ink.background for ink in bodies.values()}) == 3
    assert bodies["light"].background == curses.COLOR_WHITE
    assert bodies["dark"].background == curses.COLOR_BLUE
    assert bodies["sun"].background == curses.COLOR_BLACK
    # sun is the high-contrast one: bold white or yellow on black, nothing dim.
    sun = PALETTES["sun"].inks
    assert sun["normal"] == Ink(curses.COLOR_WHITE, curses.COLOR_BLACK, True)
    assert sun["dim"].foreground == curses.COLOR_WHITE
    # Dark text on the light page is never bold: bold is the bright colour.
    light = PALETTES["light"].inks
    for role in ("frame", "heading", "value", "warning", "ok", "banner", "panel"):
        assert not light[role].bold, role


def test_every_palette_colours_the_wake_pop_bold_red_on_its_page() -> None:
    for name, palette in PALETTES.items():
        wake = palette.inks["wake"]
        page = palette.inks["normal"].background
        assert wake == Ink(curses.COLOR_RED, page, True), name
        # The pair is one the palette already had.
        others = {
            (ink.foreground, ink.background)
            for role, ink in palette.inks.items()
            if role != "wake"
        }
        assert (wake.foreground, wake.background) in others, name
    # So the terminal needs no more colour pairs than before.
    assert PAIRS_NEEDED == 14
    assert Theme.mono().wake == curses.A_REVERSE | curses.A_BOLD


def test_from_palette_numbers_the_pairs_and_sets_bold() -> None:
    initialised: list[tuple[int, int, int]] = []
    theme = Theme.from_palette(
        PALETTES["dark"],
        lambda number, foreground, background: initialised.append(
            (number, foreground, background)
        ),
        lambda number: number << 8,
    )
    assert theme.name == "dark"
    numbers = [number for number, _, _ in initialised]
    assert numbers == list(range(1, PALETTES["dark"].pairs() + 1))
    assert initialised[0] == (1, curses.COLOR_WHITE, curses.COLOR_BLUE)
    assert theme.normal == 1 << 8
    assert theme.value == (1 << 8) | curses.A_BOLD
    # Roles drawn alike share a pair.
    assert theme.title == theme.key_label == theme.selected


def test_the_mono_theme_has_every_role_and_no_colour() -> None:
    theme = Theme.mono()
    assert theme.name == ""
    for role in theme_roles():
        assert getattr(theme, role) >> 8 == 0 or getattr(theme, role) in (
            curses.A_BOLD,
            curses.A_REVERSE,
            curses.A_DIM,
            curses.A_UNDERLINE,
            curses.A_REVERSE | curses.A_BOLD,
        ), role


# --- the T key ------------------------------------------------------------------


def test_t_cycles_light_dark_sun_light_in_the_config_file(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [], theme="light")
    seen: list[tuple[str, str, int]] = []

    def note() -> None:
        seen.append(
            (
                theme_in_file(harness.config_file),
                harness.app.canvas.theme.name,
                harness.screen.background,
            )
        )

    harness.screen.keys = ["T", note, "t", note, "T", note, "q"]
    assert harness.run() == 0
    assert [(in_file, on_screen) for in_file, on_screen, _ in seen] == [
        ("dark", "dark"),
        ("sun", "sun"),
        ("light", "light"),
    ]
    # The page itself changed colour each time.
    # (Without bold: curses would add it to every character on the page.)
    assert [background for _, _, background in seen] == [
        fake_colour_theme(name).normal & ~curses.A_BOLD
        for name in ("dark", "sun", "light")
    ]
    assert harness.screen.saw("Theme: dark.")
    assert harness.screen.saw("Theme   sun")
    # Only the theme line changed, and the comments survived.
    assert harness.config_text() == CONFIG_TEXT


def test_t_changes_the_colours_at_once(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [], theme="light")
    screen = harness.screen
    seen: list[int] = []
    # The title bar's first cell, drawn in the theme's title colours.
    screen.keys = [
        -1,
        lambda: seen.append(screen.attr_at(0, 0)),
        "T",
        -1,
        lambda: seen.append(screen.attr_at(0, 0)),
        "q",
    ]
    harness.run()
    assert seen == [fake_colour_theme("light").title, fake_colour_theme("dark").title]
    assert seen[0] != seen[1]


def test_t_works_inside_a_screen_and_inside_a_dialog(tmp_path: Path) -> None:
    # On the Wake phrase screen, then inside its kind picker.
    harness = make_harness(tmp_path, ["7", "t", "2", "t", ESC, ESC, "q"], theme="light")
    harness.run()
    assert theme_in_file(harness.config_file) == "sun"
    assert harness.screen.saw("═ Kind of detector ")


def test_t_types_a_letter_in_the_speakers_name(tmp_path: Path) -> None:
    keys: list[Key] = ["1", *typed("Tate T"), ENTER, ESC, "q"]
    harness = make_harness(tmp_path, keys, theme="light", hostname="nuc")
    harness.run()
    assert harness.screen.saw("Tate T_")
    assert harness.screen.saw("Speaker Tate T")
    # No theme was switched while the name was typed.
    assert theme_in_file(harness.config_file) == "light"
    assert harness.config_text() == CONFIG_TEXT


def test_t_types_a_letter_in_any_text_field(tmp_path: Path) -> None:
    # The model path of the Wake phrase screen.
    keys: list[Key] = ["7", "3", *typed("t"), ESC, ESC, "q"]
    harness = make_harness(tmp_path, keys, theme="dark")
    harness.run()
    assert harness.screen.saw("models/wakeword/autobot.onnxt_")
    assert theme_in_file(harness.config_file) == "light"


def test_without_colours_t_says_so_and_writes_nothing(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, ["T", -1, "q"])
    harness.run()
    assert harness.screen.saw("this terminal has no colours")
    assert harness.screen.saw("Theme   no colours")
    assert harness.config_text() == CONFIG_TEXT


def test_the_interface_opens_in_the_saved_theme(tmp_path: Path) -> None:
    text = CONFIG_TEXT.replace('theme = "light"', 'theme = "sun"')
    harness = make_harness(tmp_path, ["T", "q"], config_text=text, theme="sun")
    harness.run()
    assert harness.screen.saw("Theme   sun")
    assert theme_in_file(harness.config_file) == "light"


def test_a_config_file_without_a_tui_table_gets_one(tmp_path: Path) -> None:
    text = CONFIG_TEXT[: CONFIG_TEXT.index("[tui]")]
    harness = make_harness(tmp_path, ["T", "q"], config_text=text, theme="light")
    harness.run()
    assert theme_in_file(harness.config_file) == "dark"
    assert harness.config_text().startswith(text.rstrip())


def test_the_key_bar_shows_t_theme(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, ["q"], rows=MIN_HEIGHT, cols=MIN_WIDTH)
    harness.run()
    assert harness.screen.frames[-1].splitlines()[-1].endswith("T Theme")


# --- the look ---------------------------------------------------------------------


def test_the_banner_fits_beside_the_menu_in_the_smallest_terminal() -> None:
    inside = MIN_WIDTH - 2 - MENU_WIDTH - 4
    assert all(len(line) <= inside for line in (*BANNER, *BANNER_PLAIN, TAGLINE))
    assert len(BANNER[0]) == len(BANNER[1])


@pytest.mark.parametrize(("rows", "cols"), [(MIN_HEIGHT, MIN_WIDTH), (40, 120)])
def test_the_main_menu_has_a_framed_banner_and_a_state_badge(
    tmp_path: Path, rows: int, cols: int
) -> None:
    harness = make_harness(tmp_path, [-1, "q"], rows=rows, cols=cols, theme="dark")
    harness.run()
    screen = harness.screen
    frame = screen.frames[0].splitlines()
    for line in BANNER:
        assert screen.saw(line)
    assert screen.saw(TAGLINE)
    # The banner sits in a single-line frame inside the double-line one.
    top = next(row for row in frame if "┌" in row)
    assert top.rstrip().endswith("┐║")
    assert screen.saw("► 1  Recording session")
    assert screen.saw("State   UNTRAINED  no trained model is installed.")
    # Nothing is cut off at the right edge.
    assert all(len(row) <= cols for row in frame)


def test_the_state_badge_is_drawn_in_its_own_colours(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [], theme="dark")
    screen = harness.screen
    found: list[int] = []

    def look() -> None:
        row = next(
            index
            for index, line in enumerate(screen.text().splitlines())
            if "State   UNTRAINED" in line
        )
        column = screen.text().splitlines()[row].index("UNTRAINED")
        found.append(screen.attr_at(row, column))

    screen.keys = [-1, look, "q"]
    harness.run()
    theme = fake_colour_theme("dark")
    assert found == [theme.badge_warning]
    assert theme.badge_warning != theme.normal


def test_ascii_mode_draws_the_banner_as_plain_text(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [-1, "q"], glyphs=Glyphs.ascii())
    harness.run()
    screen = harness.screen
    assert screen.saw("a u t o b o t")
    assert not screen.saw(BANNER[0])
    assert screen.saw("+-")
    assert screen.saw("> 1  Recording session")
    assert all(ord(character) < 128 for character in screen.frames[0])


def every_screen_keys() -> list[Key]:
    """Open every screen, a dialog on most of them, and the help."""
    return [
        "1",  # the session: its name prompt, then its screen
        *typed("Sam"),
        ENTER,
        -1,
        ESC,
        "2",  # the training pane
        -1,
        ESC,
        "3",  # the candidate: there is none yet
        -1,
        ESC,
        "4",  # the live monitor: its name prompt, then its screen
        *typed("Sam"),
        ENTER,
        -1,
        ESC,
        "5",  # the dataset
        -1,
        ESC,
        "6",  # audio, and its device picker
        "1",
        ESC,
        ESC,
        "7",  # wake phrase, its threshold prompt, and the training pane
        ENTER,
        ESC,
        "4",
        ESC,
        ESC,
        "8",  # service, and its confirmation
        "7",
        ESC,
        ESC,
        "9",  # host
        ESC,
        F1,
        ESC,
        "q",
    ]


@pytest.mark.parametrize("theme", [None, *TUI_THEMES])
@pytest.mark.parametrize(
    ("rows", "cols"), [(MIN_HEIGHT, MIN_WIDTH), (24, 80), (50, 200)]
)
def test_every_screen_draws_in_every_theme_at_every_size(
    tmp_path: Path, theme: str | None, rows: int, cols: int
) -> None:
    harness = make_harness(
        tmp_path, every_screen_keys(), rows=rows, cols=cols, theme=theme, hostname="nuc"
    )
    assert harness.run() == 0
    screen = harness.screen
    assert not screen.saw("Terminal too small")
    for title in (
        "═ Recording session ",
        "═ Train the wake-word model ",
        "═ Candidate ",
        "═ Live monitor ",
        "┌─ Listener ",
        "═ Dataset ",
        "═ Audio ",
        "═ Input device ",
        "═ Wake phrase ",
        "═ Threshold ",
        "═ Service ",
        "═ Uninstall the unit ",
        "═ Host ",
        "═ Help ",
    ):
        assert screen.saw(title), title
    assert screen.saw("A U T O B O T")
    assert screen.saw("Level meter: on")
    # The fake screen raises, as curses does, on anything drawn off the screen;
    # and no frame is wider than the terminal.
    assert all(
        len(row) <= cols for frame in screen.frames for row in frame.splitlines()
    )
    assert harness.config_text() == CONFIG_TEXT


def test_the_facts_are_framed_when_there_is_room_and_ruled_when_not(
    tmp_path: Path,
) -> None:
    def audio_frame(rows: int) -> str:
        harness = make_harness(tmp_path, ["6", -1, ESC, "q"], rows=rows, cols=80)
        harness.run()
        return next(frame for frame in harness.screen.frames if "═ Audio " in frame)

    roomy = audio_frame(24)
    assert "┌─ Devices " in roomy
    assert "Clip   (none recorded yet)" in roomy
    # At the minimum height the frame gives its row to the level meter's panel.
    tight = audio_frame(MIN_HEIGHT)
    assert "┌─ Devices " not in tight
    assert "Input device         'pipewire'  (#3 pipewire)" in tight
    assert "Clip   (none recorded yet)" in tight
