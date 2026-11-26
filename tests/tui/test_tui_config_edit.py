"""`autobot.tui.config_edit`: writing the config file without losing anything."""

import tomllib
from pathlib import Path

import pytest
from tui_fakes import CONFIG_TEXT

from autobot.config import ConfigError, load_config, repo_root
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
    text = CONFIG_TEXT.replace('text = "Yes?"', 'text = """Yes?"""')
    with pytest.raises(ConfigEditError):
        apply_changes(text, [Change("ack", "text", "Hello")])


def test_an_edit_that_would_disturb_other_data_is_refused() -> None:
    # A key line hidden inside a multi-line string looks like the real one.
    text = CONFIG_TEXT.replace('text = "Yes?"', 'text = """\nvoice = "decoy"\n"""')
    with pytest.raises(ConfigEditError, match="refusing to write"):
        apply_changes(text, [Change("ack", "voice", "other")])


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


def test_the_real_config_file_round_trips() -> None:
    text = (repo_root() / "config" / "autobot.toml").read_text(encoding="utf-8")
    new = apply_changes(
        text,
        [
            Change("host", "nuc_hostname", "elsewhere"),
            Change("audio", "input_device", "default"),
            Change("wake_phrases", "threshold", 0.7, 0),
        ],
    )
    assert comments(new) == comments(text)
    assert len(new.splitlines()) == len(text.splitlines())
    data = tomllib.loads(new)
    assert data["host"]["nuc_hostname"] == "elsewhere"
    assert data["audio"]["input_device"] == "default"
    assert data["wake_phrases"][0]["threshold"] == 0.7
