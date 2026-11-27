"""`autobot.tui.config_edit`: writing the config file without losing anything."""

import tomllib
from pathlib import Path

import pytest
from tui_fakes import CONFIG_TEXT

from autobot.config import Config, ConfigError, load_config, parse_config, repo_root
from autobot.tui.config_edit import (
    Change,
    ConfigEditError,
    ConfigStore,
    apply_changes,
    format_value,
    set_value,
)


def comments(text: str) -> list[str]:
    return [line.partition("#")[2] for line in text.splitlines() if "#" in line]


def test_changing_a_value_touches_only_that_line() -> None:
    new = apply_changes(CONFIG_TEXT, [Change("wake_phrases", "threshold", 0.65, 0)])
    before, after = CONFIG_TEXT.splitlines(), new.splitlines()
    assert len(before) == len(after)
    changed = [(old, now) for old, now in zip(before, after, strict=True) if old != now]
    assert changed == [("threshold = 0.5", "threshold = 0.65")]


def test_a_trailing_comment_survives() -> None:
    new = apply_changes(CONFIG_TEXT, [Change("audio", "output_device", "default")])
    assert 'output_device = "default"  # the speaker' in new
    assert comments(new) == comments(CONFIG_TEXT)


def test_a_device_index_is_written_as_a_number() -> None:
    new = apply_changes(CONFIG_TEXT, [Change("audio", "input_device", 3)])
    assert tomllib.loads(new)["audio"]["input_device"] == 3


def test_a_string_is_escaped() -> None:
    assert format_value('say "hi"\\now') == '"say \\"hi\\"\\\\now"'
    new = apply_changes(
        CONFIG_TEXT, [Change("ack", "text", 'He said "yes" # not a comment')]
    )
    assert tomllib.loads(new)["ack"]["text"] == 'He said "yes" # not a comment'


def test_a_value_holding_a_hash_is_replaced_whole() -> None:
    text = CONFIG_TEXT.replace('text = "Yes?"', 'text = "Yes # really"  # the reply')
    new = apply_changes(text, [Change("ack", "text", "Hello")])
    assert 'text = "Hello"  # the reply' in new


def test_several_changes_are_one_write() -> None:
    new = apply_changes(
        CONFIG_TEXT,
        [
            Change("wake_phrases", "model", "models/wakeword/autobot.onnx", 0),
            Change("wake_phrases", "kind", "openwakeword", 0),
        ],
    )
    phrase = tomllib.loads(new)["wake_phrases"][0]
    assert (phrase["kind"], phrase["model"]) == (
        "openwakeword",
        "models/wakeword/autobot.onnx",
    )


def test_a_missing_key_is_added_inside_its_table() -> None:
    text = CONFIG_TEXT.replace('nuc_hostname = "nuc"\n', "")
    new = apply_changes(text, [Change("host", "nuc_hostname", "branch")])
    data = tomllib.loads(new)
    assert data["host"] == {"nuc_hostname": "branch"}
    assert data["audio"] == tomllib.loads(CONFIG_TEXT)["audio"]


def test_a_missing_table_is_added() -> None:
    text = CONFIG_TEXT.replace(
        '[host]\n# Hostname of the Intel NUC.\nnuc_hostname = "nuc"\n', ""
    )
    assert "host" not in tomllib.loads(text)
    new = apply_changes(text, [Change("host", "nuc_hostname", "branch")])
    assert tomllib.loads(new)["host"] == {"nuc_hostname": "branch"}


def test_the_right_entry_of_an_array_of_tables_is_edited() -> None:
    text = CONFIG_TEXT.replace(
        "[ack]",
        '[[wake_phrases]]\nphrase = "second"\nkind = "text_match"\n'
        'threshold = 0.4\ntarget = "ack"\n\n[ack]',
    )
    new = apply_changes(text, [Change("wake_phrases", "threshold", 0.9, 1)])
    phrases = tomllib.loads(new)["wake_phrases"]
    assert [phrase["threshold"] for phrase in phrases] == [0.5, 0.9]


def test_a_missing_array_entry_is_refused() -> None:
    with pytest.raises(ConfigEditError, match="entry #3"):
        apply_changes(CONFIG_TEXT, [Change("wake_phrases", "threshold", 0.9, 2)])


def test_an_invalid_result_is_refused() -> None:
    with pytest.raises(ConfigError, match="threshold"):
        apply_changes(CONFIG_TEXT, [Change("wake_phrases", "threshold", 1.5, 0)])
    with pytest.raises(ConfigError, match="model"):
        apply_changes(CONFIG_TEXT, [Change("wake_phrases", "kind", "openwakeword", 0)])


def test_a_value_the_editor_cannot_rewrite_is_refused() -> None:
    text = CONFIG_TEXT.replace('text = "Yes?"', "text = '''Yes?'''")
    with pytest.raises(ConfigEditError, match="edit the config file by hand"):
        apply_changes(text, [Change("ack", "text", "Hello")])


def test_an_edit_that_would_disturb_other_data_is_refused() -> None:
    # A line separator inside a comment makes the rest of it look like a key.
    text = CONFIG_TEXT.replace("[ack]\n", '[ack]\n# note voice = "decoy"\n')
    assert tomllib.loads(text)["ack"]["voice"] == "en_US-joe-medium"
    with pytest.raises(ConfigEditError, match="refusing to write"):
        apply_changes(text, [Change("ack", "voice", "other")])


def test_a_key_line_inside_another_keys_multi_line_string_is_skipped() -> None:
    text = CONFIG_TEXT.replace(
        'text = "Yes?"', 'text = """\nvoice = "decoy"\n"""  # the reply'
    )
    new = apply_changes(text, [Change("ack", "voice", "other")])
    assert tomllib.loads(new)["ack"] == {"text": 'voice = "decoy"\n', "voice": "other"}
    assert new == text.replace('voice = "en_US-joe-medium"', 'voice = "other"')


def test_set_value_keeps_crlf_line_endings() -> None:
    text = '[host]\r\nnuc_hostname = "a"\r\n'
    assert (
        set_value(text, Change("host", "nuc_hostname", "b"))
        == '[host]\r\nnuc_hostname = "b"\r\n'
    )


def test_the_store_writes_the_file_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    path = tmp_path / "autobot.toml"
    path.write_text(CONFIG_TEXT, encoding="utf-8")
    path.chmod(0o640)
    config = ConfigStore(path).update(Change("host", "nuc_hostname", "branch"))
    assert config.host.nuc_hostname == "branch"
    assert load_config(path).host.nuc_hostname == "branch"
    assert comments(path.read_text(encoding="utf-8")) == comments(CONFIG_TEXT)
    assert [entry.name for entry in tmp_path.iterdir()] == ["autobot.toml"]
    assert path.stat().st_mode & 0o777 == 0o640


def test_a_refused_change_leaves_the_file_untouched(tmp_path: Path) -> None:
    path = tmp_path / "autobot.toml"
    path.write_text(CONFIG_TEXT, encoding="utf-8")
    with pytest.raises(ConfigError):
        ConfigStore(path).update(Change("wake_phrases", "threshold", 7, 0))
    assert path.read_text(encoding="utf-8") == CONFIG_TEXT
    assert [entry.name for entry in tmp_path.iterdir()] == ["autobot.toml"]


def test_the_store_re_reads_the_file_before_each_write(tmp_path: Path) -> None:
    path = tmp_path / "autobot.toml"
    path.write_text(CONFIG_TEXT, encoding="utf-8")
    store = ConfigStore(path)
    store.load()
    # Someone edits the file by hand while the interface is open.
    path.write_text(CONFIG_TEXT.replace('"Yes?"', '"Hello"'), encoding="utf-8")
    config = store.update(Change("host", "nuc_hostname", "branch"))
    assert (config.ack.text, config.host.nuc_hostname) == ("Hello", "branch")


def test_a_missing_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        ConfigStore(tmp_path / "absent.toml").update(
            Change("host", "nuc_hostname", "x")
        )


def test_the_theme_is_written_into_its_table(tmp_path: Path) -> None:
    path = tmp_path / "autobot.toml"
    path.write_text(CONFIG_TEXT, encoding="utf-8")
    config = ConfigStore(path).update(Change("tui", "theme", "dark"))
    assert config.tui.theme == "dark"
    text = path.read_text(encoding="utf-8")
    assert text == CONFIG_TEXT.replace('theme = "light"', 'theme = "dark"')
    with pytest.raises(ConfigError, match=r"\[tui\] theme = 'neon'"):
        ConfigStore(path).update(Change("tui", "theme", "neon"))
    assert path.read_text(encoding="utf-8") == text


def test_the_theme_table_is_added_to_a_file_without_one(tmp_path: Path) -> None:
    # A config file written before the themes existed has no [tui] table.
    old = CONFIG_TEXT[: CONFIG_TEXT.index("[tui]")]
    path = tmp_path / "autobot.toml"
    path.write_text(old, encoding="utf-8")
    assert ConfigStore(path).load().tui.theme == "light"
    assert ConfigStore(path).update(Change("tui", "theme", "sun")).tui.theme == "sun"
    text = path.read_text(encoding="utf-8")
    assert text.startswith(old.rstrip("\n"))
    assert text.endswith('[tui]\ntheme = "sun"\n')
    assert comments(text) == comments(old)


def test_the_real_config_file_round_trips() -> None:
    text = (repo_root() / "config" / "autobot.toml").read_text(encoding="utf-8")
    new = apply_changes(
        text,
        [
            Change("host", "nuc_hostname", "elsewhere"),
            Change("audio", "input_device", "default"),
            Change("wake_phrases", "threshold", 0.7, 0),
            Change("tui", "theme", "sun"),
        ],
    )
    assert comments(new) == comments(text)
    assert len(new.splitlines()) == len(text.splitlines())
    data = tomllib.loads(new)
    assert data["host"]["nuc_hostname"] == "elsewhere"
    assert data["audio"]["input_device"] == "default"
    assert data["wake_phrases"][0]["threshold"] == 0.7
    assert data["tui"]["theme"] == "sun"


# --- agent definitions: multi-line strings, arrays, array entries ------------

AGENTS = """
[words]
# The words that answer yes.
yes = ["yes", "sure"]  # a short list
no = ["no"]

[[agents]]
name = "echo"

[[agents]]
name = "question"
# The prompt wrapped around the question.
prompt = "Answer."  # one line for now
listening = true

[[agents]]
name = "weather"
# Off until the owner turns it on.
confirm_entry = false
zip_code = ""
"""
PROMPT = "You are autobot.\nAnswer in two parts.\nReply with JSON."


def agents_text() -> str:
    return CONFIG_TEXT + AGENTS


def config_of(text: str) -> Config:
    return parse_config(tomllib.loads(text))


def test_a_prompt_of_three_lines_is_written_as_a_multi_line_string() -> None:
    text = agents_text()
    new = apply_changes(text, [Change("agents", "prompt", PROMPT, 1)])
    written = (
        'prompt = """\n'
        "You are autobot.\n"
        "Answer in two parts.\n"
        "Reply with JSON.\\\n"
        '"""  # one line for now\n'
    )
    assert written in new
    assert tomllib.loads(new)["agents"][1]["prompt"] == PROMPT
    assert comments(new) == comments(text)
    assert config_of(new).agent("question").prompt == PROMPT


def test_a_value_ending_in_a_newline_closes_on_a_line_of_its_own() -> None:
    new = apply_changes(agents_text(), [Change("agents", "prompt", PROMPT + "\n", 1)])
    assert 'Reply with JSON.\n"""  # one line for now\n' in new
    assert tomllib.loads(new)["agents"][1]["prompt"] == PROMPT + "\n"


def test_a_multi_line_value_is_replaced_by_a_one_line_one_and_back() -> None:
    text = agents_text()
    multi = apply_changes(text, [Change("agents", "prompt", PROMPT, 1)])
    one = apply_changes(multi, [Change("agents", "prompt", "Answer.", 1)])
    assert one == text
    assert apply_changes(one, [Change("agents", "prompt", PROMPT, 1)]) == multi
    # A multi-line value is replaced by another as one block.
    other = apply_changes(multi, [Change("agents", "prompt", "One.\nTwo.", 1)])
    assert tomllib.loads(other)["agents"][1]["prompt"] == "One.\nTwo."
    assert "Reply with JSON" not in other
    assert comments(other) == comments(text)


def test_a_multi_line_string_closed_on_its_first_line_is_replaced() -> None:
    text = CONFIG_TEXT.replace('text = "Yes?"', 'text = """Yes?"""  # the reply')
    new = apply_changes(text, [Change("ack", "text", "Hello")])
    assert 'text = "Hello"  # the reply' in new


@pytest.mark.parametrize(
    "value",
    [
        'say """hi""" and "" too\nand """"" five',
        'ends in quotes\n""',
        'ends in a quote\n"',
        "a backslash \\ and \\n\nand a tab\there",
        "\nstarts with a newline\n\n",
        "a carriage return\r\nand a bell \x07\n",
        "ends in a backslash\n\\",
    ],
)
def test_a_multi_line_string_round_trips(value: str) -> None:
    written = format_value(value)
    assert written.startswith('"""\n') and written.endswith('"""')
    assert tomllib.loads(f"v = {written}\n")["v"] == value
    new = apply_changes(agents_text(), [Change("agents", "prompt", value, 1)])
    assert tomllib.loads(new)["agents"][1]["prompt"] == value


def test_a_list_is_written_as_a_one_line_array() -> None:
    assert format_value(["yes", "yeah"]) == '["yes", "yeah"]'
    assert format_value([]) == "[]"
    assert format_value(['say "hi"', "don't"]) == '["say \\"hi\\"", "don\'t"]'
    text = agents_text()
    new = apply_changes(text, [Change("words", "yes", ["yes", "yeah"])])
    assert 'yes = ["yes", "yeah"]  # a short list\n' in new
    assert comments(new) == comments(text)
    assert config_of(new).words.yes == ("yes", "yeah")


def test_a_list_is_added_to_a_file_without_its_table() -> None:
    new = apply_changes(CONFIG_TEXT, [Change("words", "actions", ["cancel", "quit"])])
    assert new.endswith('[words]\nactions = ["cancel", "quit"]\n')
    assert config_of(new).words.actions == ("cancel", "quit")


def test_a_bool_is_toggled_in_the_third_agents_entry() -> None:
    text = agents_text()
    new = apply_changes(text, [Change("agents", "confirm_entry", True, 2)])
    changed = [
        (old, now)
        for old, now in zip(text.splitlines(), new.splitlines(), strict=True)
        if old != now
    ]
    assert changed == [("confirm_entry = false", "confirm_entry = true")]
    assert config_of(new).agent("weather").confirm_entry


def test_a_missing_key_is_added_after_a_multi_line_string() -> None:
    text = apply_changes(agents_text(), [Change("agents", "prompt", PROMPT, 1)])
    text = text.replace("listening = true\n", "")
    new = apply_changes(text, [Change("agents", "listening", False, 1)])
    assert '"""  # one line for now\nlistening = false\n' in new
    assert not config_of(new).agent("question").listening


def test_a_change_with_a_multi_line_value_is_described_on_one_line() -> None:
    change = Change("agents", "prompt", "one\ntwo", 1)
    assert change.describe() == '[agents #2] prompt = "one\\ntwo"'


def test_the_real_config_files_agents_and_words_round_trip() -> None:
    text = (repo_root() / "config" / "autobot.toml").read_text(encoding="utf-8")
    new = apply_changes(
        text,
        [
            Change("agents", "prompt", PROMPT, 1),
            Change("agents", "zip_code", "43215", 2),
            Change("agents", "commands", "make one\nmake two\n", 4),
            Change("words", "yes", ["yes", "yeah", "roger"]),
        ],
    )
    assert comments(new) == comments(text)
    config = config_of(new)
    assert config.agent("question").prompt == PROMPT
    assert config.agent("weather").zip_code == "43215"
    assert config.agent("camera").commands == "make one\nmake two\n"
    assert config.words.yes == ("yes", "yeah", "roger")
    assert config.agent("introduce") == config_of(text).agent("introduce")
