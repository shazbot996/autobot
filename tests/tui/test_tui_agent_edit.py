"""The Agent editor's model: the selection, the fields and the text area. No terminal."""

import tomllib

import pytest
from tui_fakes import CONFIG_TEXT

from autobot.agents.registry import AGENTS
from autobot.agents.tree import build_tree
from autobot.config import Config, parse_config
from autobot.tui.agent_edit import (
    COMMANDS_NOTE,
    MULTI_LINE,
    READ_ONLY,
    SECRET,
    SECRET_NOTE,
    TEXT,
    TOGGLE,
    Field,
    Selection,
    TextArea,
    commands_text,
    fields,
    limit_complaint,
    table_index,
    zip_code_complaint,
)
from autobot.tui.config_edit import Change

# The file defines three agents; the others are the defaults.
AGENTS_TEXT = """
[[agents]]
name = "echo"

[[agents]]
name = "question"

[[agents]]
name = "weather"
zip_code = "43004"
"""


def config() -> Config:
    return parse_config(tomllib.loads(CONFIG_TEXT + AGENTS_TEXT))


def fields_of(name: str, index: int = 0) -> list[Field]:
    settings = config()
    return fields(AGENTS[name], settings.agent(name), index, settings.routes)


def labels(name: str) -> list[str]:
    return [field.label for field in fields_of(name)]


def field(name: str, label: str, index: int = 0) -> Field:
    return next(field for field in fields_of(name, index) if field.label == label)


# --- the selection --------------------------------------------------------------


def test_the_selection_walks_from_the_root_along_the_agents_and_back() -> None:
    selection = Selection(build_tree(config()))
    walked = [selection.current.name]
    selection.down()
    walked.append(selection.current.name)
    for _ in range(5):
        selection.right()
        walked.append(selection.current.name)
    selection.up()
    walked.append(selection.current.name)
    assert walked == [
        "autobot",
        "question",
        "weather",
        "time",
        "camera",
        "introduce",
        "echo",
        "autobot",
    ]
    assert selection.current.root


def test_the_selection_stops_at_the_ends_of_a_level_and_of_the_tree() -> None:
    selection = Selection(build_tree(config()))
    selection.up()
    selection.left()
    selection.right()
    assert selection.current.name == "autobot"
    selection.down()
    selection.down()
    selection.left()
    assert selection.current.name == "question"
    for _ in range(9):
        selection.right()
    assert selection.current.name == "echo"
    selection.left()
    assert selection.current.name == "introduce"


def test_a_selection_keeps_its_place_on_a_tree_built_again() -> None:
    tree = build_tree(config())
    selection = Selection(tree)
    selection.down()
    selection.right()
    again = Selection(tree, selection.level, selection.column)
    assert again.current.name == "weather"
    # A place the tree no longer has is the nearest it does have.
    assert Selection(tree, 7, 99).current.name == "echo"


# --- the fields -----------------------------------------------------------------


def test_the_weather_agents_fields_end_in_its_own_settings() -> None:
    assert labels("weather") == [
        "Name",
        "Description",
        "Commands",
        "Language model",
        "Confirm on the way in",
        "Question",
        "Confirm before a request",
        "Question",
        "Listening",
        "zip_code",
        "daily_limit",
        "minute_limit",
        "API key",
    ]


def test_only_an_agent_with_a_model_has_a_prompt_and_introduce_has_a_script() -> None:
    assert "Prompt" not in labels("time") and "Script" not in labels("time")
    assert "Prompt" in labels("question")
    assert "Script" in labels("introduce") and "Prompt" not in labels("introduce")
    assert field("question", "Prompt").kind == MULTI_LINE
    assert field("introduce", "Script").key == "prompt"
    assert field("question", "Language model").value == "yes"
    assert field("introduce", "Language model").value == "no"


def test_the_request_confirmation_is_offered_only_to_an_agent_that_sends_requests() -> (
    None
):
    assert "Confirm before a request" in labels("question")
    assert "Confirm before a request" not in labels("time")
    assert labels("time") == [
        "Name",
        "Description",
        "Commands",
        "Language model",
        "Confirm on the way in",
        "Question",
        "Listening",
    ]
    assert labels("camera")[-2:] == ["folder", "commands"]
    assert field("camera", "commands").kind == MULTI_LINE


def test_the_name_the_commands_and_the_model_are_read_only() -> None:
    name = field("weather", "Name")
    assert (name.kind, name.value, name.editable) == (READ_ONLY, "weather", False)
    commands = field("weather", "Commands")
    assert commands.kind == READ_ONLY
    # The mode a rule carries stands in brackets after its command.
    assert commands.value == "weather, forecast (forecast)"
    assert commands.note == COMMANDS_NOTE == "edited on the Agent routing screen"
    assert commands.hint == COMMANDS_NOTE
    assert field("camera", "Commands").value == "camera, cameras"
    assert commands_text("weather", ()) == "(none)"
    with pytest.raises(ValueError, match="Commands is not written"):
        commands.change("x")


def test_the_api_key_field_holds_the_secrets_name_and_writes_nothing() -> None:
    key = field("weather", "API key")
    assert key.kind == SECRET
    assert key.value == "autobot-weather-api-key"
    assert key.note == SECRET_NOTE == "stored in Secret Manager; never shown"
    assert not key.editable
    with pytest.raises(ValueError):
        key.change("AIza")


def test_each_field_knows_its_change_to_the_agents_table() -> None:
    assert field("weather", "zip_code", 2).change(" 43215 ") == Change(
        "agents", "zip_code", "43215", 2
    )
    # A limit is written as a whole number, not as text.
    assert field("weather", "daily_limit", 2).change("50") == Change(
        "agents", "daily_limit", 50, 2
    )
    assert field("weather", "Description", 2).change("Says the weather.") == Change(
        "agents", "description", "Says the weather.", 2
    )
    # A toggle flips what the definition holds.
    assert field("weather", "Confirm on the way in", 2).change() == Change(
        "agents", "confirm_entry", True, 2
    )
    assert field("weather", "Listening", 2).change() == Change(
        "agents", "listening", False, 2
    )
    # A multi-line text is written as it stands.
    assert field("question", "Prompt", 1).change("One.\nTwo.\n") == Change(
        "agents", "prompt", "One.\nTwo.\n", 1
    )


def test_a_bad_value_gets_a_one_line_complaint() -> None:
    zip_code = field("weather", "zip_code")
    assert zip_code.kind == TEXT
    assert zip_code.value == "43004"
    assert zip_code.complaint("4321") == "a zip code is five digits"
    assert zip_code.complaint("4321a") == "a zip code is five digits"
    assert zip_code.complaint("43215") is None
    # Empty is the zip code not set yet, as in the config file.
    assert zip_code_complaint("") is None
    limit = field("weather", "minute_limit")
    for bad in ("", "0", "-3", "ten", "1.5"):
        assert limit.complaint(bad) == "a limit is a positive whole number"
    assert limit_complaint(" 12 ") is None
    question = next(row for row in fields_of("time") if row.label == "Question")
    assert question.complaint("  ") == "it must not be empty"
    assert question.complaint("Sure?") is None
    assert field("camera", "folder").complaint("") == "it must not be empty"
    assert field("weather", "Description").complaint("") is None


def test_a_field_is_shown_on_one_line_with_a_hint_of_what_enter_does() -> None:
    toggle = field("weather", "Confirm on the way in")
    assert (toggle.kind, toggle.shown) == (TOGGLE, "off")
    assert toggle.hint == "Enter turns [[agents]] confirm_entry on, written at once"
    assert field("weather", "Listening").hint == (
        "Enter turns [[agents]] listening off, written at once"
    )
    assert field("weather", "zip_code").hint == (
        "Enter changes [[agents]] zip_code, written at once"
    )
    # Every hint fits the message bar of the smallest terminal.
    for name in ("question", "weather", "camera", "introduce"):
        assert all(len(row.hint) <= 70 for row in fields_of(name)), name
    assert field("weather", "Listening").shown == "on"
    assert field("weather", "daily_limit").shown == "100"
    prompt = field("question", "Prompt")
    assert prompt.shown.startswith("8 lines: You are autobot, a voice assistant")
    assert "Esc closes it and writes [[agents]] prompt" in prompt.hint
    assert Field("Prompt", MULTI_LINE, "").shown == "(empty)"
    assert Field("Prompt", MULTI_LINE, "One.\n").shown == "1 line: One."
    assert "Secret Manager only" in field("weather", "API key").hint
    assert "not edited here" in field("weather", "Name").hint


def test_the_tables_number_is_read_from_the_file_by_the_agents_name() -> None:
    text = CONFIG_TEXT + AGENTS_TEXT
    assert table_index(text, "echo") == 0
    assert table_index(text, "weather") == 2
    # An agent the file does not define has no table to write to.
    assert table_index(text, "camera") is None
    assert table_index(CONFIG_TEXT, "weather") is None
    with pytest.raises(ValueError):
        table_index("[[agents]\n", "weather")


# --- the text area ----------------------------------------------------------------


def test_the_text_area_edits_three_lines() -> None:
    area = TextArea()
    assert not area.typing
    area.open("One.\nTwo.\nThree.")
    assert area.typing
    assert area.lines == ["One.", "Two.", "Three."]
    assert (area.row, area.col) == (0, 0)

    # `T` is a letter here, as every printable key is.
    area.insert("T")
    assert area.text() == "TOne.\nTwo.\nThree."
    area.backspace()
    assert area.text() == "One.\nTwo.\nThree."

    # Down keeps the column; End goes to the line's end.
    area.move_down()
    area.end()
    area.insert(" Too")
    assert area.lines[1] == "Two. Too"
    assert (area.row, area.col) == (1, 8)

    # A newline breaks the line at the cursor.
    for _ in range(4):
        area.move_left()
    area.newline()
    assert area.lines == ["One.", "Two.", " Too", "Three."]
    assert (area.row, area.col) == (2, 0)

    # A backspace at a line's start joins it to the one above.
    area.backspace()
    assert area.lines == ["One.", "Two. Too", "Three."]
    assert (area.row, area.col) == (1, 4)

    # Delete takes the character at the cursor, and at a line's end the break.
    area.delete()
    assert area.lines[1] == "Two.Too"
    area.end()
    area.delete()
    assert area.lines == ["One.", "Two.TooThree."]

    area.close()
    assert not area.typing
    assert area.text() == "One.\nTwo.TooThree."


def test_the_arrows_move_the_cursor_within_the_text() -> None:
    area = TextArea()
    area.open("abc\n\nxy")
    area.move_up()
    area.move_left()
    assert (area.row, area.col) == (0, 0)
    area.end()
    # Right at a line's end goes to the start of the next; left comes back.
    area.move_right()
    assert (area.row, area.col) == (1, 0)
    area.move_left()
    assert (area.row, area.col) == (0, 3)
    # Down onto a shorter line brings the cursor to its end.
    area.move_down()
    assert (area.row, area.col) == (1, 0)
    area.move_down()
    area.end()
    area.move_right()
    area.move_down()
    assert (area.row, area.col) == (2, 2)
    area.home()
    assert (area.row, area.col) == (2, 0)


def test_a_text_that_ends_in_a_newline_comes_back_as_it_was() -> None:
    area = TextArea()
    area.open("make fourway-server\nmake fourway\n")
    assert area.lines == ["make fourway-server", "make fourway", ""]
    assert area.text() == "make fourway-server\nmake fourway\n"
    area.open("")
    assert area.lines == [""]
    assert area.text() == ""


def test_the_window_follows_the_cursor() -> None:
    area = TextArea()
    area.open("\n".join(f"line {number} " + "x" * 30 for number in range(10)))
    assert area.window(3, 12) == ["line 0 xxxxx", "line 1 xxxxx", "line 2 xxxxx"]
    for _ in range(4):
        area.move_down()
    assert area.window(3, 12)[-1] == "line 4 xxxxx"
    assert area.top == 2
    area.end()
    shown = area.window(3, 12)
    # The cursor stands after the last character, inside the window.
    assert area.left == area.col - 11
    assert shown[-1] == "x" * 11
    area.home()
    for _ in range(4):
        area.move_up()
    assert area.window(3, 12)[0] == "line 0 xxxxx"
    assert (area.top, area.left) == (0, 0)
