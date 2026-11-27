"""The Agent editor's two screens, opened from the main menu of the real `App` on fakes."""

import curses
import tomllib
from pathlib import Path

import pytest
from tui_fakes import (
    BACKSPACE,
    CONFIG_TEXT,
    DOWN,
    ENTER,
    ESC,
    F1,
    UP,
    FakeScreen,
    Harness,
    Key,
    make_harness,
    typed,
)

from autobot.config import GcpConfig
from autobot.tui.app import MIN_HEIGHT, MIN_WIDTH
from gcp.gcp_fakes import ready_cloud

LEFT = curses.KEY_LEFT
RIGHT = curses.KEY_RIGHT

# `0` is the main menu's tenth entry, the Agent editor.
OPEN: list[Key] = ["0"]
# From the tree: Down to the agents' level, then along it.
QUESTION: list[Key] = [*OPEN, DOWN, ENTER]
WEATHER: list[Key] = [*OPEN, DOWN, RIGHT, ENTER]
# From an agent's view: back to the tree, to the menu, and out.
LEAVE: list[Key] = [ESC, ESC, "q"]

SECRET = "autobot-weather-api-key"
SECRET_VALUE = "AIza-s3cr3t-Token"

# The routes, the word lists and the six definitions, in the shape of
# `config/autobot.toml`: the third `[[agents]]` table is the weather agent's.
AGENTS_TEXT = """
[[routes]]
command = "question"
agent = "question"

[[routes]]
command = "weather"
agent = "weather"

[[routes]]
command = "forecast"
agent = "weather"
mode = "forecast"

[[routes]]
command = "time"
agent = "time"

[[routes]]
command = "echo"
agent = "echo"

# The shared word lists.
[words]
actions = ["cancel"]

# The agents' definitions, one [[agents]] table each.
[[agents]]
name = "echo"
description = "Records five seconds of your voice and plays it back."
confirm_entry = false

[[agents]]
name = "question"
description = "Asks a language model your question."
# The prompt asks for the two parts of the answer.
prompt = \"\"\"
You are autobot, a voice assistant.
Reply with one JSON object and nothing else.
\"\"\"
confirm_entry = false
listening = true

[[agents]]
name = "weather"
description = "Says today's weather for your zip code."
confirm_entry = false  # asks before it starts
confirm_entry_question = "Are you sure you want to start this?"
listening = true
# The location: a five-digit zip code. Empty until it is set.
zip_code = ""
# The agent's own cap on its lookups.
daily_limit = 100
minute_limit = 10
# Only the secret's name is configuration: the key is never written here.
secret = "autobot-weather-api-key"

[[agents]]
name = "time"
description = "Says the time from this computer's clock."
"""
TEXT = CONFIG_TEXT + AGENTS_TEXT


def harness_with(tmp_path: Path, keys: list[Key], **options: object) -> Harness:
    options.setdefault("config_text", TEXT)
    return make_harness(tmp_path, keys, **options)  # type: ignore[arg-type]


def no_frame_shows(frames: list[str], *texts: str) -> bool:
    return not any(text in frame for frame in frames for text in texts)


def files_under(folder: Path) -> list[Path]:
    return [path for path in folder.rglob("*") if path.is_file()]


def agents_table(text: str, number: int) -> dict[str, object]:
    """The `[[agents]]` table `number` of config text, counted from one, parsed."""
    table = tomllib.loads(text)["agents"][number - 1]
    assert isinstance(table, dict)
    return table


def reversed_text(screen: FakeScreen) -> list[str]:
    """What the screen shows in reverse video, row by row: the mono theme's selection."""
    found = []
    for y, row in enumerate(screen.grid[2:-3], start=2):
        cells = [
            char
            for x, char in enumerate(row)
            if screen.attr_at(y, x) == curses.A_REVERSE
        ]
        if cells:
            found.append("".join(cells).strip())
    return found


class Probe:
    """A scripted key that presses nothing and keeps what is selected on the screen."""

    def __init__(self) -> None:
        self.screen: FakeScreen | None = None
        self.seen: list[list[str]] = []

    def __call__(self) -> None:
        assert self.screen is not None
        self.seen.append(reversed_text(self.screen))


# --- the tree screen --------------------------------------------------------------


@pytest.mark.parametrize(("rows", "cols"), [(MIN_HEIGHT, MIN_WIDTH), (40, 120)])
def test_zero_from_the_main_menu_shows_the_tree_with_autobot_selected(
    tmp_path: Path, rows: int, cols: int
) -> None:
    probe = Probe()
    harness = harness_with(tmp_path, [*OPEN, probe, ESC, "q"], rows=rows, cols=cols)
    probe.screen = harness.screen
    assert harness.run() == 0
    screen = harness.screen
    for text in (
        "═ Agent editor ",
        "│ autobot │",
        # With the core agent the tree is wider than the smallest terminal:
        # the boxes under the selected root are the ones in view.
        "│ time │",
        "forecast (forecast)",
        "autobot: the wake phrase",
        "Heard at every level: cancel",
        "The arrows walk the tree; Down goes to the agents",
    ):
        assert screen.saw(text), text
    # The keywords stand beside the levels where the terminal is wide enough.
    assert screen.saw("[cancel]") == (cols == 120)
    # The root's box, frame and all, is the selection.
    assert probe.seen == [["┌─────────┐", "│ autobot │", "└────┬────┘"]]
    assert all(
        len(row) <= cols for frame in screen.frames for row in frame.splitlines()
    )
    # Nothing was changed, so nothing was written.
    assert harness.config_text() == TEXT


def test_the_arrows_walk_the_tree_and_the_selection_stays_on_the_screen(
    tmp_path: Path,
) -> None:
    probe = Probe()
    keys: list[Key] = [*OPEN, DOWN, probe, RIGHT, probe]
    keys += [RIGHT, RIGHT, probe, LEFT, probe, UP, probe, ESC, "q"]
    # The tree is wider than the minimum terminal: it scrolls to the selection.
    harness = harness_with(tmp_path, keys, rows=MIN_HEIGHT, cols=MIN_WIDTH)
    probe.screen = harness.screen
    assert harness.run() == 0
    assert [seen[1] for seen in probe.seen] == [
        "│ question │",
        "│ weather │",
        "│ echo │",
        "│ time │",
        "│ autobot │",
    ]
    screen = harness.screen
    assert screen.saw("weather agent: weather, forecast (forecast)")
    assert screen.saw("Says today's weather for your zip code.")
    assert screen.saw("Enter opens the weather agent's editor")
    assert all(
        len(row) <= MIN_WIDTH for frame in screen.frames for row in frame.splitlines()
    )


def test_enter_on_the_root_says_it_is_the_wake_phrase(tmp_path: Path) -> None:
    harness = harness_with(tmp_path, [*OPEN, ENTER, -1, ESC, "q"])
    assert harness.run() == 0
    screen = harness.screen
    assert screen.saw("The root is the wake phrase: the Wake phrase screen edits it")
    # No view was opened: the tree is still the screen.
    assert no_frame_shows(screen.frames, "═ Agent editor: ")


def test_f1_lists_the_trees_keys(tmp_path: Path) -> None:
    harness = harness_with(tmp_path, [*OPEN, F1, ENTER, ESC, "q"])
    assert harness.run() == 0
    screen = harness.screen
    assert screen.saw("Up, Down       move the selection a level of the tree")
    assert screen.saw("Left, Right    move it between the agents of a level")
    assert screen.saw("Enter          open the selected agent's editor")


# --- the editor view --------------------------------------------------------------


@pytest.mark.parametrize(("rows", "cols"), [(MIN_HEIGHT, MIN_WIDTH), (40, 120)])
def test_down_right_enter_opens_the_weather_agent(
    tmp_path: Path, rows: int, cols: int
) -> None:
    # Up from the first field wraps to the last, so every field is drawn.
    keys: list[Key] = [*WEATHER, -1, UP, -1, *LEAVE]
    harness = harness_with(tmp_path, keys, rows=rows, cols=cols)
    assert harness.run() == 0
    screen = harness.screen
    for text in (
        "═ Agent editor: weather ",
        "The weather agent: what its code does",
        "Looks up the weather for the zip code below with Google's Weather",
        "Its definition: Enter changes a field",
        "► Name                      weather",
        "Description               Says today's weather for your zip code.",
        "Commands                  weather, forecast (forecast)",
        "edited on the Agent routing screen",
        "Language model            no",
        "Confirm on the way in     off",
        "Question                  Are you sure you want to start this?",
        "Confirm before a request  off",
        "Listening                 on",
        "zip_code",
        "daily_limit               100",
        "minute_limit              10",
        "► API key                   autobot-weather-api-key",
        "stored in Secret Manager; never shown",
        "Enter opens a masked field: paste the key",
    ):
        assert screen.saw(text), text
    assert all(
        len(row) <= cols for frame in screen.frames for row in frame.splitlines()
    )
    assert harness.config_text() == TEXT


def test_a_zip_code_typed_into_its_field_is_written_to_the_third_agents_table(
    tmp_path: Path,
) -> None:
    # Up wraps to the API key; zip_code is three fields above it.
    keys: list[Key] = [*WEATHER, UP, UP, UP, UP, ENTER]
    keys += [*typed("4321"), ENTER, -1, *typed("5"), ENTER, -1, *LEAVE]
    harness = harness_with(tmp_path, keys)
    assert harness.run() == 0
    screen = harness.screen
    assert screen.saw("weather: zip_code")
    assert screen.saw("[[agents]] zip_code of the weather agent:")
    # Four digits are refused in the field, in one line.
    assert screen.saw("a zip code is five digits")
    assert screen.saw('Saved: [agents #3] zip_code = "43215"')
    assert screen.saw("► zip_code                  43215")
    # One line of the file changed, and every comment is still there.
    assert harness.config_text() == TEXT.replace('zip_code = ""', 'zip_code = "43215"')
    assert agents_table(harness.config_text(), 3)["zip_code"] == "43215"
    assert harness.app.config.agent("weather").zip_code == "43215"


def test_a_limit_is_written_as_a_whole_number(tmp_path: Path) -> None:
    keys: list[Key] = [*WEATHER, UP, UP, UP, ENTER, BACKSPACE, BACKSPACE, BACKSPACE]
    keys += [ENTER, -1, *typed("50"), ENTER, -1, *LEAVE]
    harness = harness_with(tmp_path, keys)
    assert harness.run() == 0
    assert harness.screen.saw("a limit is a positive whole number")
    assert harness.config_text() == TEXT.replace(
        "daily_limit = 100", "daily_limit = 50"
    )


def test_toggling_the_confirmation_on_the_way_in_writes_it_and_marks_the_tree(
    tmp_path: Path,
) -> None:
    keys: list[Key] = [*WEATHER, *[DOWN] * 8, ENTER, -1, ESC, -1, ESC, "q"]
    harness = harness_with(tmp_path, keys)
    assert harness.run() == 0
    screen = harness.screen
    assert screen.saw("Enter turns [[agents]] confirm_entry on, written at once")
    assert screen.saw("Saved: [agents #3] confirm_entry = true")
    assert screen.saw("► Confirm on the way in     on")
    # The comment beside the value is kept.
    assert harness.config_text() == TEXT.replace(
        "confirm_entry = false  # asks before it starts",
        "confirm_entry = true  # asks before it starts",
    )
    assert agents_table(harness.config_text(), 3)["confirm_entry"] is True
    # Back on the tree, the weather agent's box carries the mark (AG-13).
    assert "?─┐" in screen.frames[-2]
    assert no_frame_shows(screen.frames[:3], "?─┐")


def test_a_key_absent_from_the_table_is_added_to_it(tmp_path: Path) -> None:
    # The file's weather table has no `confirm_request`: its toggle adds it.
    keys: list[Key] = [*WEATHER, *[DOWN] * 10, ENTER, -1, *LEAVE]
    harness = harness_with(tmp_path, keys)
    assert harness.run() == 0
    assert harness.screen.saw("Saved: [agents #3] confirm_request = true")
    table = agents_table(harness.config_text(), 3)
    assert table["confirm_request"] is True
    assert table["name"] == "weather"
    assert "# Only the secret's name is configuration" in harness.config_text()


def test_editing_the_question_agents_prompt_writes_a_multi_line_string(
    tmp_path: Path,
) -> None:
    # Prompt is the fifth field. `T` and `q` are letters in the open text.
    keys: list[Key] = [*QUESTION, *[DOWN] * 8, -1, ENTER, -1]
    keys += [*typed("Tq "), -1, DOWN, DOWN, *typed("Be brief."), ENTER, -1]
    keys += [ESC, -1, *LEAVE]
    harness = harness_with(tmp_path, keys, theme="light")
    assert harness.run() == 0
    screen = harness.screen
    assert screen.saw("► Prompt                    2 lines: You are autobot, a voice")
    assert screen.saw("Prompt of the question agent")
    assert screen.saw("Type the text; Esc closes it and saves it when it changed")
    assert screen.saw("Tq You are autobot, a voice assistant.")
    assert screen.saw("line 1 of 3, column 4")
    assert screen.saw("Save and close")
    # `T` typed a letter: the theme was not switched.
    assert 'theme = "light"' in harness.config_text()
    assert no_frame_shows(screen.frames, "Theme: ")
    assert harness.config_text() == TEXT.replace(
        'prompt = """\n'
        "You are autobot, a voice assistant.\n"
        "Reply with one JSON object and nothing else.\n"
        '"""\n',
        'prompt = """\n'
        "Tq You are autobot, a voice assistant.\n"
        "Reply with one JSON object and nothing else.\n"
        "Be brief.\n"
        '"""\n',
    )
    assert agents_table(harness.config_text(), 2)["prompt"] == (
        "Tq You are autobot, a voice assistant.\n"
        "Reply with one JSON object and nothing else.\n"
        "Be brief.\n"
    )
    # Esc closed the text, not the view: the fields are back.
    assert (
        "► Prompt                    3 lines: Tq You are autobot" in screen.frames[-4]
    )


def test_a_text_closed_unchanged_writes_nothing(tmp_path: Path) -> None:
    keys: list[Key] = [*QUESTION, *[DOWN] * 8, ENTER]
    keys += ["x", BACKSPACE, ESC, -1, *LEAVE]
    harness = harness_with(tmp_path, keys)
    assert harness.run() == 0
    assert harness.screen.saw("Prompt is unchanged")
    assert harness.config_text() == TEXT


def test_a_pasted_api_key_goes_to_secret_manager_and_nowhere_else(
    tmp_path: Path,
) -> None:
    cloud = ready_cloud(GcpConfig())
    # Up from the first field wraps to the last: the API key.
    keys: list[Key] = [*WEATHER, UP, -1, ENTER, *typed(SECRET_VALUE), -1, ENTER, -1]
    harness = harness_with(tmp_path, [*keys, *LEAVE], cloud=cloud)
    assert harness.run() == 0
    screen = harness.screen
    assert screen.saw(f"Paste {SECRET}")
    assert screen.saw("The value goes to Secret Manager and is kept nowhere else:")
    assert screen.saw("*" * len(SECRET_VALUE))
    # One version was added, with the value, through the fake service account.
    assert cloud.made("add_secret_version") == [("add_secret_version", SECRET)]
    assert cloud.stored == {SECRET: [SECRET_VALUE.encode()]}
    assert harness.connects == [tmp_path]
    assert screen.saw(f"Stored: {SECRET} has a new version in Secret Manager")
    # The value was on no frame...
    for cut in range(len(SECRET_VALUE) - 3):
        assert no_frame_shows(screen.frames, SECRET_VALUE[cut : cut + 4])
    # ...and is in no file under the temporary directory.
    for path in files_under(tmp_path):
        assert SECRET_VALUE.encode() not in path.read_bytes(), path
    assert harness.config_text() == TEXT
    assert harness.runner.calls == []


def test_an_api_key_that_cannot_be_stored_says_why_in_one_line(tmp_path: Path) -> None:
    # No key file, so no connection: the masked field is never opened.
    harness = harness_with(tmp_path, [*WEATHER, UP, ENTER, -1, *LEAVE])
    assert harness.run() == 0
    screen = harness.screen
    assert screen.saw(f"{SECRET} not stored: refused: no key")
    assert no_frame_shows(screen.frames, f"Paste {SECRET}")
    assert harness.config_text() == TEXT


def test_a_cancelled_paste_stores_nothing(tmp_path: Path) -> None:
    cloud = ready_cloud(GcpConfig())
    keys: list[Key] = [*WEATHER, UP, ENTER, *typed("AIza"), ESC, -1, *LEAVE]
    harness = harness_with(tmp_path, keys, cloud=cloud)
    assert harness.run() == 0
    assert harness.screen.saw(f"Cancelled: {SECRET} is unchanged")
    assert cloud.made("add_secret_version") == []


def test_an_agent_the_file_does_not_define_is_shown_and_refuses_a_change(
    tmp_path: Path,
) -> None:
    # The harness's own config has no [[agents]] table: every definition is a default.
    keys: list[Key] = [*WEATHER, *[DOWN] * 8, ENTER, -1, *LEAVE]
    harness = harness_with(tmp_path, keys, config_text=CONFIG_TEXT)
    assert harness.run() == 0
    screen = harness.screen
    assert screen.saw("Confirm on the way in     off")
    assert screen.saw("Not saved: no [[agents]] table named weather in the config file")
    assert harness.config_text() == CONFIG_TEXT


def test_a_read_only_field_says_where_it_is_edited(tmp_path: Path) -> None:
    keys: list[Key] = [*WEATHER, ENTER, -1, DOWN, DOWN, ENTER, -1, F1, ENTER, *LEAVE]
    harness = harness_with(tmp_path, keys)
    assert harness.run() == 0
    screen = harness.screen
    assert screen.saw("Name: part of the agent's code: it is not edited here")
    assert screen.saw("Commands: edited on the Agent routing screen")
    assert screen.saw("Up, Down       move between the fields")
    assert harness.config_text() == TEXT
