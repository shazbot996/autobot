"""The Word lists screen, opened from the main menu of the real `App` on fakes."""

from pathlib import Path

import pytest
from tui_fakes import CONFIG_TEXT, ENTER, ESC, UP, Key, make_harness, typed

from autobot.config import WordsConfig
from autobot.tui.app import MIN_HEIGHT, MIN_WIDTH
from autobot.tui.screens.agent_words import (
    EMPTY,
    NO_MATCH,
    complaint,
    describe,
    split_words,
)

# Up from the first entry wraps to Quit; then Google Cloud, then Word lists.
OPEN: list[Key] = [UP, UP, UP, ENTER]
CLEAR = 21  # Ctrl-U empties a prompt's field.
WORDS = """
[words]
# The words that mean yes.
yes = ["yes", "sure"]
no = ["no", "nah"]  # the words that mean no
actions = ["cancel"]
"""
DEFAULTS = WordsConfig()


@pytest.mark.parametrize(("rows", "cols"), [(MIN_HEIGHT, MIN_WIDTH), (40, 120)])
def test_the_screen_shows_the_three_lists_with_their_defaults(
    tmp_path: Path, rows: int, cols: int
) -> None:
    harness = make_harness(tmp_path, [*OPEN, -1, ESC, "q"], rows=rows, cols=cols)
    assert harness.run() == 0
    screen = harness.screen
    for text in (
        "═ Word lists ",
        "┌─ What each list is for ",
        "answer a confirmation yes",
        "answer a confirmation no",
        "act at every level, tried before the rules",
        "stands in one list only",
        "1  Yes words: yes, yeah, yep, yup, sure, okay, ok,",
        "2  No words: no, nope, nah, no way, negative, stop, don't, nev",
        "3  Global action keywords: cancel",
        "4  Try a word...",
        "5  Back",
        "Action keywords are tried first, then no words, then yes words.",
        "(type a sentence to see which list it hits)",
    ):
        assert screen.saw(text), text
    # A list longer than the row is cut: the yes words always, the no words
    # in the narrow terminal.
    assert screen.saw(
        "1  Yes words: yes, yeah, yep, yup, sure, okay, ok, affirmative~"
    ) == (cols == MIN_WIDTH)
    assert not screen.saw("please do")
    assert screen.saw("stop, don't, never mind") == (cols == 120)
    assert all(
        len(row) <= cols for frame in screen.frames for row in frame.splitlines()
    )
    # Nothing was changed, so nothing was written.
    assert harness.config_text() == CONFIG_TEXT


def test_a_typed_list_is_written_to_the_config_file(tmp_path: Path) -> None:
    keys = [*OPEN, "1", CLEAR, *typed("yes, yeah, roger"), ENTER, -1, ESC, "q"]
    harness = make_harness(tmp_path, keys, config_text=CONFIG_TEXT + WORDS)
    assert harness.run() == 0
    text = harness.config_text()
    assert 'yes = ["yes", "yeah", "roger"]' in text
    # The comments and the other lists survive the write.
    assert "# The words that mean yes." in text
    assert 'no = ["no", "nah"]  # the words that mean no' in text
    assert text.startswith(CONFIG_TEXT)
    assert harness.app.config.words.yes == ("yes", "yeah", "roger")
    assert harness.screen.saw("1  Yes words: yes, yeah, roger")


def test_a_file_without_the_table_gets_one(tmp_path: Path) -> None:
    keys = [*OPEN, "1", CLEAR, *typed("yes, yeah, roger"), ENTER, -1, ESC, "q"]
    harness = make_harness(tmp_path, keys)
    assert harness.run() == 0
    text = harness.config_text()
    assert "[words]" in text
    assert 'yes = ["yes", "yeah", "roger"]' in text
    assert text.startswith(CONFIG_TEXT)
    words = harness.app.config.words
    assert words.yes == ("yes", "yeah", "roger")
    # The lists the file does not give keep their defaults.
    assert (words.no, words.actions) == (DEFAULTS.no, DEFAULTS.actions)


def test_a_typed_list_is_trimmed_lower_cased_and_freed_of_duplicates(
    tmp_path: Path,
) -> None:
    keys = [
        *OPEN,
        "3",
        CLEAR,
        *typed(" Cancel ,  Go  Back, cancel,, "),
        ENTER,
        -1,
        ESC,
        "q",
    ]
    harness = make_harness(tmp_path, keys)
    assert harness.run() == 0
    assert 'actions = ["cancel", "go back"]' in harness.config_text()
    assert harness.screen.saw("3  Global action keywords: cancel, go back")


def test_a_word_of_another_list_is_refused_in_one_line(tmp_path: Path) -> None:
    keys: list[Key] = [
        *OPEN,
        "1",
        CLEAR,
        *typed("yes, cancel"),
        ENTER,
        -1,
        ESC,
        -1,
        ESC,
        "q",
    ]
    harness = make_harness(tmp_path, keys)
    assert harness.run() == 0
    assert harness.screen.saw('"cancel" is already a global action keyword')
    assert harness.config_text() == CONFIG_TEXT
    assert harness.app.config.words == DEFAULTS


def test_an_empty_list_is_refused(tmp_path: Path) -> None:
    keys: list[Key] = [*OPEN, "2", CLEAR, *typed(" , "), ENTER, -1, ESC, -1, ESC, "q"]
    harness = make_harness(tmp_path, keys)
    assert harness.run() == 0
    assert harness.screen.saw(EMPTY)
    assert harness.config_text() == CONFIG_TEXT


def test_a_sentence_is_tried_against_the_lists(tmp_path: Path) -> None:
    keys: list[Key] = [*OPEN]
    for sentence in ("you betcha", "nah", "please cancel", "maybe"):
        keys += ["4", CLEAR, *typed(sentence), ENTER, -1]
    keys += [ESC, "q"]
    harness = make_harness(tmp_path, keys)
    assert harness.run() == 0
    screen = harness.screen
    for sentence, result in (
        ("you betcha", 'yes word "you betcha": answers yes'),
        ("nah", 'no word "nah": answers no'),
        ("please cancel", 'global action keyword "cancel": leaves what runs'),
        ("maybe", NO_MATCH),
    ):
        assert screen.saw(f'Tried: "{sentence}"'), sentence
        assert screen.saw(result), result
    assert harness.config_text() == CONFIG_TEXT


def test_back_returns_to_the_main_menu(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [*OPEN, -1, "5", -1, "q"])
    assert harness.run() == 0
    assert "Word lists: " not in harness.screen.frames[-1]
    assert "What each list is for" not in harness.screen.frames[-1]


# --- the functions that do no drawing -------------------------------------------


def test_split_words_trims_lowers_and_drops_duplicates() -> None:
    assert split_words("yes, yeah, roger") == ["yes", "yeah", "roger"]
    assert split_words("  You   Betcha ,YES,yes,, sure ") == [
        "you betcha",
        "yes",
        "sure",
    ]
    assert split_words(" , ") == []


def test_complaint_names_the_list_that_already_has_the_word() -> None:
    assert complaint("yes", "yes, yeah, roger", DEFAULTS) is None
    assert complaint("yes", "", DEFAULTS) == EMPTY
    assert (
        complaint("yes", "yes, Cancel", DEFAULTS)
        == '"cancel" is already a global action keyword'
    )
    assert complaint("yes", "nah", DEFAULTS) == '"nah" is already a no word'
    assert complaint("actions", "cancel, sure", DEFAULTS) == (
        '"sure" is already a yes word'
    )
    # A word of the list being edited is no clash with itself.
    assert complaint("no", "no, nope", DEFAULTS) is None
    for line in (complaint("yes", "cancel", DEFAULTS), EMPTY):
        assert line is not None and "\n" not in line


def test_describe_says_which_list_a_sentence_hits() -> None:
    assert describe("you betcha", DEFAULTS).startswith('yes word "you betcha"')
    assert describe("Nah.", DEFAULTS).startswith('no word "nah"')
    assert describe("please cancel that", DEFAULTS).startswith(
        'global action keyword "cancel"'
    )
    # A no word beats a yes word in the same sentence, as in a confirmation.
    assert describe("yes, no", DEFAULTS).startswith('no word "no"')
    assert describe("maybe", DEFAULTS) == NO_MATCH
    assert describe("cancellation", DEFAULTS) == NO_MATCH
