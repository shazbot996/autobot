"""The Agent routing screen, opened from the main menu of the real `App` on fakes."""

from pathlib import Path

import pytest
from tui_fakes import CONFIG_TEXT, ENTER, ESC, UP, Key, make_harness, typed

from autobot.tui.app import MIN_HEIGHT, MIN_WIDTH

# Up from the first entry wraps to Quit; then Google Cloud, then Agent routing.
OPEN: list[Key] = [UP, UP, UP, ENTER]
CLEAR = 21  # Ctrl-U empties a prompt's field.
ROUTES = """
[[routes]]
# The word that runs the echo.
command = "echo"
agent = "echo"
"""


@pytest.mark.parametrize(("rows", "cols"), [(MIN_HEIGHT, MIN_WIDTH), (40, 120)])
def test_the_screen_lists_the_rules_in_the_order_they_are_tried(
    tmp_path: Path, rows: int, cols: int
) -> None:
    harness = make_harness(tmp_path, [*OPEN, -1, ESC, "q"], rows=rows, cols=cols)
    assert harness.run() == 0
    screen = harness.screen
    for text in (
        "═ Agent routing ",
        "┌─ How a command is routed ",
        '"autobot": wakes autobot, then it listens',
        "the listener's text_match transcripts",
        '1  Rule 1: "echo" -> echo agent',
        "2  Try a sentence...",
        "The first rule whose command is in the sentence wins.",
    ):
        assert screen.saw(text), text
    assert all(
        len(row) <= cols for frame in screen.frames for row in frame.splitlines()
    )
    # Nothing was changed, so nothing was written.
    assert harness.config_text() == CONFIG_TEXT


def test_a_sentence_is_tried_against_the_rules(tmp_path: Path) -> None:
    keys = [
        *OPEN,
        "2",
        *typed("could you echo that"),
        ENTER,
        -1,
        "2",
        CLEAR,
        *typed("what time is it"),
        ENTER,
        -1,
        ESC,
        "q",
    ]
    harness = make_harness(tmp_path, keys)
    assert harness.run() == 0
    screen = harness.screen
    assert screen.saw('Tried: "could you echo that"')
    assert screen.saw('rule 1: "echo" -> echo agent: runs the agent')
    assert screen.saw('Tried: "what time is it"')
    assert screen.saw("no rule matches: autobot keeps listening")
    assert harness.config_text() == CONFIG_TEXT


def test_a_rules_command_is_written_to_the_config_file(tmp_path: Path) -> None:
    keys = [*OPEN, "1", CLEAR, *typed("say it  back"), ENTER, -1, ESC, "q"]
    harness = make_harness(tmp_path, keys, config_text=CONFIG_TEXT + ROUTES)
    assert harness.run() == 0
    text = harness.config_text()
    assert 'command = "say it back"' in text
    # The comments survive the write.
    assert "# The word that runs the echo." in text
    assert harness.app.config.routes[0].command == "say it back"
    assert harness.screen.saw('Rule 1: "say it back" -> echo agent')


def test_an_empty_command_is_refused(tmp_path: Path) -> None:
    keys: list[Key] = [*OPEN, "1", CLEAR, ENTER, -1, ESC, -1, ESC, "q"]
    harness = make_harness(tmp_path, keys, config_text=CONFIG_TEXT + ROUTES)
    assert harness.run() == 0
    assert harness.screen.saw("type a word for the command")
    assert harness.config_text() == CONFIG_TEXT + ROUTES


def test_a_file_without_routes_says_why_it_is_not_saved(tmp_path: Path) -> None:
    keys = [*OPEN, "1", CLEAR, *typed("repeat"), ENTER, -1, ESC, "q"]
    harness = make_harness(tmp_path, keys)
    assert harness.run() == 0
    assert harness.screen.saw("Not saved: no [[routes]] entry #1 in the config file")
    assert harness.config_text() == CONFIG_TEXT


def test_without_a_text_match_phrase_the_screen_warns(tmp_path: Path) -> None:
    text = CONFIG_TEXT.replace('kind = "text_match"', 'kind = "sherpa_kws"')
    harness = make_harness(tmp_path, [*OPEN, -1, ESC, "q"], config_text=text)
    assert harness.run() == 0
    assert harness.screen.saw("nowhere: no wake phrase is text_match")
