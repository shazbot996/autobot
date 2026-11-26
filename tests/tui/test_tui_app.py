"""The whole interface, driven by scripted keys on a fake screen.

These run the real `App`, views and dialogs; only the terminal, the audio
hardware and the commands are fakes.
"""

import curses
import sys
import time
import tomllib
import wave
from pathlib import Path

import pytest
from tui_fakes import (
    BACKSPACE,
    CONFIG_TEXT,
    DOWN,
    ENTER,
    ESC,
    F1,
    F10,
    UP,
    Harness,
    Key,
    ScriptExhausted,
    make_harness,
    typed,
)

from autobot.tui.app import MIN_HEIGHT, MIN_WIDTH
from autobot.tui.runner import CommandResult
from autobot.tui.screens.wake_phrase import (
    DEFAULT_MODEL,
    parse_threshold,
    step_threshold,
)


def config(harness: Harness) -> dict[str, object]:
    return tomllib.loads(harness.config_text())


def phrase(harness: Harness) -> dict[str, object]:
    phrases = config(harness)["wake_phrases"]
    assert isinstance(phrases, list)
    entry = phrases[0]
    assert isinstance(entry, dict)
    return entry


def comments(text: str) -> list[str]:
    return [line.partition("#")[2] for line in text.splitlines() if "#" in line]


def test_every_screen_opens_and_the_interface_quits(tmp_path: Path) -> None:
    keys: list[Key] = [
        ENTER,
        ESC,
        "2",
        ESC,
        "3",
        ESC,
        "4",
        ESC,
        "5",
        ESC,
        "6",
        ESC,
        "q",
    ]
    harness = make_harness(tmp_path, keys)
    assert harness.run() == 0
    screen = harness.screen
    for title in ("Main Menu", "═ Audio ", "═ Wake phrase ", "═ Service ", "═ Host "):
        assert screen.saw(title), title
    assert screen.saw("═ Cloud deployment ")
    assert screen.saw("═ Identity and access management ")
    assert screen.saw("The Google Cloud project does not exist yet.")
    assert screen.saw("F10 Quit")
    # Nothing was changed, so nothing was written.
    assert harness.config_text() == CONFIG_TEXT


def test_the_menu_moves_with_the_arrows_and_quit_is_an_entry(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [UP, ENTER])
    assert harness.run() == 0
    assert harness.screen.saw("► 7  Quit")


def test_f10_quits_from_inside_a_screen_and_closes_the_stream(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [ENTER, F10])
    assert harness.run() == 0
    assert harness.audio.streams and all(
        stream.closed for stream in harness.audio.streams
    )


def test_help_opens_and_closes(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [F1, ENTER, "q"])
    harness.run()
    assert harness.screen.saw("═ Help ")
    assert harness.screen.saw("Every change is written to the config file at once.")


def test_the_script_running_out_is_an_error_not_a_hang(tmp_path: Path) -> None:
    with pytest.raises(ScriptExhausted):
        make_harness(tmp_path, [ENTER]).run()


# --- wake phrase ----------------------------------------------------------------


def test_the_threshold_is_typed_and_written_back(tmp_path: Path) -> None:
    keys: list[Key] = [
        "2",
        ENTER,
        BACKSPACE,
        BACKSPACE,
        BACKSPACE,
        *typed("0.65"),
        ENTER,
        ESC,
        "q",
    ]
    harness = make_harness(tmp_path, keys)
    harness.run()
    assert phrase(harness)["threshold"] == 0.65
    assert comments(harness.config_text()) == comments(CONFIG_TEXT)
    assert harness.screen.saw("Saved: [wake_phrases #1] threshold = 0.65")
    assert harness.screen.saw("Threshold        0.65")


def test_a_bad_threshold_is_refused_in_the_dialog(tmp_path: Path) -> None:
    keys: list[Key] = ["2", ENTER, *typed("x"), ENTER, ESC, ESC, "q"]
    harness = make_harness(tmp_path, keys)
    harness.run()
    assert harness.screen.saw("type a number, for example 0.5")
    assert harness.config_text() == CONFIG_TEXT


def test_plus_and_minus_move_the_threshold(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, ["2", "+", "+", "-", ESC, "q"])
    harness.run()
    assert phrase(harness)["threshold"] == 0.55


def test_choosing_openwakeword_writes_a_default_model_and_warns(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, ["2", "2", DOWN, ENTER, ESC, "q"])
    harness.run()
    assert phrase(harness)["kind"] == "openwakeword"
    assert phrase(harness)["model"] == DEFAULT_MODEL
    assert harness.screen.saw(f"the model file {DEFAULT_MODEL} does not exist yet")
    assert harness.screen.saw("(file does not exist)")


def test_no_warning_when_the_model_file_exists(tmp_path: Path) -> None:
    model = tmp_path / DEFAULT_MODEL
    model.parent.mkdir(parents=True)
    model.write_bytes(b"onnx")
    harness = make_harness(tmp_path, ["2", "2", DOWN, ENTER, ESC, "q"])
    harness.run()
    assert phrase(harness)["kind"] == "openwakeword"
    assert not harness.screen.saw("does not exist")


def test_cancelling_the_kind_dialog_changes_nothing(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, ["2", "2", DOWN, ESC, ESC, "q"])
    harness.run()
    assert harness.config_text() == CONFIG_TEXT


def test_the_model_cannot_be_emptied_while_openwakeword_needs_it(
    tmp_path: Path,
) -> None:
    text = CONFIG_TEXT.replace('kind = "text_match"', 'kind = "openwakeword"').replace(
        'model = ""', 'model = "m.onnx"'
    )
    keys: list[Key] = ["2", "3", *[BACKSPACE] * 6, ENTER, ESC, ESC, "q"]
    harness = make_harness(tmp_path, keys, config_text=text)
    harness.run()
    assert harness.screen.saw("openwakeword needs a model file path")
    assert harness.config_text() == text


def test_the_acknowledgement_runs_the_listener_command(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, ["2", "5", ESC, "q"])
    harness.run()
    assert harness.runner.calls == [[sys.executable, "-m", "autobot.listener.ack"]]
    assert harness.screen.saw("Acknowledgement played ('Yes?').")


def test_the_acknowledgement_says_so_when_the_listener_is_not_built(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path, ["2", "5", ESC, "q"], available=False)
    harness.run()
    assert harness.runner.calls == []
    assert harness.screen.saw(
        "The acknowledgement is not built yet (autobot.listener.ack is missing)."
    )


def test_a_failing_acknowledgement_shows_its_last_line(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, ["2", "5", ENTER, ESC, "q"])
    long_line = (
        "the acknowledgement runs on the NUC only; " + "this host is elsewhere " * 4
    )
    harness.runner.result = CommandResult(1, f"first line\n{long_line}\n")
    harness.run()
    assert harness.screen.saw("═ Acknowledgement failed ")
    assert harness.screen.saw("first line")
    # A line too long for the dialog is folded, not cut off.
    assert harness.screen.saw("    this host is elsewhere")
    assert harness.screen.saw(
        "Acknowledgement failed: the acknowledgement runs on the NUC only;"
    )


def test_parse_and_step_threshold() -> None:
    assert parse_threshold(" 0.7 ") == 0.7
    assert parse_threshold("1") == 1.0
    for bad in ("", "abc", "0", "-0.1", "1.01", "nan"):
        with pytest.raises(ValueError, match="number|threshold"):
            parse_threshold(bad)
    assert step_threshold(0.5, 1) == 0.55
    assert step_threshold(0.98, 1) == 1.0
    assert step_threshold(0.05, -1) == 0.05
    assert step_threshold(0.3, -2) == 0.2


# --- training ---------------------------------------------------------------------


def wait_for_job(harness: Harness, running: bool) -> Key:
    def wait() -> None:
        deadline = time.monotonic() + 10
        while harness.app.services.train_job.running() != running:
            assert time.monotonic() < deadline
            time.sleep(0.02)

    return wait


def test_training_output_is_shown_and_its_failure_reported(tmp_path: Path) -> None:
    code = "print('not built yet: see the plan step 6'); raise SystemExit(2)"
    harness = make_harness(tmp_path, [], train_argv=[sys.executable, "-c", code])
    harness.screen.keys = [
        "2",
        "4",
        "s",
        wait_for_job(harness, False),
        -1,
        ESC,
        ESC,
        "q",
    ]
    harness.run()
    assert harness.screen.saw("═ Train the wake-word model ")
    assert harness.screen.saw("(no output yet: press S to start the training)")
    assert harness.screen.saw("not built yet: see the plan step 6")
    assert harness.screen.saw("stopped with exit status 2")


def test_training_can_be_left_running_and_then_cancelled(tmp_path: Path) -> None:
    code = "import time; print('epoch 1', flush=True); time.sleep(60)"
    harness = make_harness(tmp_path, [], train_argv=[sys.executable, "-c", code])
    job = harness.app.services.train_job
    seen: list[bool] = []
    harness.screen.keys = [
        "2",
        "4",
        "s",
        ESC,  # leave the pane: the job keeps running
        lambda: seen.append(job.running()),
        "4",
        "c",
        "y",
        wait_for_job(harness, False),
        ESC,
        ESC,
        "q",
    ]
    harness.run()
    assert seen == [True]
    assert harness.screen.saw("Training         running (pid")
    assert harness.screen.saw("Asked the training to stop.")
    assert not job.running()


def test_quitting_while_training_asks_what_to_do(tmp_path: Path) -> None:
    code = "import time; time.sleep(60)"
    harness = make_harness(tmp_path, [], train_argv=[sys.executable, "-c", code])
    job = harness.app.services.train_job
    # First answer "Do not quit", then quit again and stop the training.
    harness.screen.keys = ["2", "4", "s", F10, DOWN, DOWN, ENTER, F10, DOWN, ENTER]
    try:
        assert harness.run() == 0
        assert harness.screen.saw("═ Training is still running ")
        deadline = time.monotonic() + 10
        while job.running():
            assert time.monotonic() < deadline
            time.sleep(0.02)
    finally:
        job.cancel()


def test_a_training_command_that_is_missing_is_one_line(tmp_path: Path) -> None:
    harness = make_harness(
        tmp_path, ["2", "4", "s", ESC, ESC, "q"], train_argv=["autobot-no-such-make"]
    )
    harness.run()
    assert harness.screen.saw(
        "Cannot start the training: command not found: autobot-no-such-make"
    )


# --- host -------------------------------------------------------------------------


def test_host_sets_nuc_hostname_to_this_machine(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, ["4", ENTER, ESC, "q"], hostname="testbox")
    harness.run()
    assert config(harness)["host"] == {"nuc_hostname": "testbox"}
    assert comments(harness.config_text()) == comments(CONFIG_TEXT)
    assert harness.screen.saw("This machine is not the configured NUC.")
    assert harness.screen.saw("This machine is the NUC.")


# --- service ----------------------------------------------------------------------


def test_service_entries_run_the_listener_control_command(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, ["3", "1", "2", "3", "4", "5", ESC, "q"])
    harness.runner.result = CommandResult(
        0, "autobot-listener.service: active (running)\n"
    )
    harness.run()
    assert [call[-1] for call in harness.runner.calls] == [
        "install",
        "enable",
        "up",
        "down",
        "status",
    ]
    assert all(
        call[:3] == [sys.executable, "-m", "autobot.listener.control"]
        for call in harness.runner.calls
    )
    assert harness.screen.saw("Output of 'status'")
    assert harness.screen.saw("autobot-listener.service: active (running)")


def test_a_failing_service_command_is_shown(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, ["3", "3", ESC, "q"])
    harness.runner.result = CommandResult(1, "the unit is not installed\n")
    harness.run()
    assert harness.screen.saw("Start failed: the unit is not installed")


def test_uninstall_asks_first(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, ["3", "7", "n", "7", "y", ESC, "q"])
    harness.run()
    assert [call[-1] for call in harness.runner.calls] == ["uninstall"]


def test_service_says_so_when_the_listener_is_not_built(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, ["3", "5", ESC, "q"], available=False)
    harness.run()
    assert harness.runner.calls == []
    assert harness.screen.saw(
        "The listener is not built yet (autobot.listener.control is missing)."
    )


# --- audio ------------------------------------------------------------------------


def test_the_audio_screen_shows_devices_and_pipewire_defaults(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, ["1", ESC, ESC, "q"])
    harness.run()
    screen = harness.screen
    assert screen.saw("Input device         'pipewire'  (#3 pipewire)")
    assert screen.saw("PipeWire microphone  AIRHUG 21 Analog Stereo")
    assert screen.saw("PipeWire speaker     Built-in Audio Analog Stereo")
    assert harness.audio.opened[0] == 3


def test_picking_an_input_device_offers_only_usable_ones_and_writes_it(
    tmp_path: Path,
) -> None:
    # The picker opens on the current device (pipewire); Down moves to "default".
    harness = make_harness(tmp_path, ["1", "1", DOWN, ENTER, ESC, "q"])
    harness.run()
    audio = config(harness)["audio"]
    assert isinstance(audio, dict)
    assert (audio["input_device"], audio["output_device"]) == ("default", "pipewire")
    assert comments(harness.config_text()) == comments(CONFIG_TEXT)
    screen = harness.screen
    assert screen.saw("═ Input device ")
    assert screen.saw("2 hidden: they do not open at 16000 Hz mono")
    assert not screen.saw("AIRHUG 21: USB Audio")
    # The stream was reopened on the new device.
    assert harness.audio.opened == [3, 4]
    assert all(stream.closed for stream in harness.audio.streams)


def test_picking_an_output_device(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, ["1", "2", UP, ENTER, ESC, "q"])
    harness.run()
    audio = config(harness)["audio"]
    assert isinstance(audio, dict)
    assert audio["output_device"] == "sysdefault"
    assert harness.screen.saw("PipeWire speaker     (not used by this output device)")


def test_setting_the_pipewire_default_source(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, ["1", "3", DOWN, ENTER, ESC, "q"])
    harness.run()
    assert ["wpctl", "set-default", "49"] in harness.wpctl_calls
    assert harness.screen.saw("*  33  AIRHUG 21 Analog Stereo")
    assert harness.screen.saw(
        "PipeWire default source is now Built-in Audio Analog Stereo"
    )
    # Nothing about PipeWire is stored in the config file.
    assert harness.config_text() == CONFIG_TEXT


def test_cancelling_the_pipewire_dialog_sets_nothing(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, ["1", "4", ESC, ESC, "q"])
    harness.run()
    assert all(call[1] == "status" for call in harness.wpctl_calls)


def test_the_meter_follows_the_input(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [])
    audio = harness.audio
    harness.screen.keys = [
        "1",
        lambda: audio.feed(3277),
        -1,
        lambda: audio.feed(0),
        -1,
        ESC,
        "q",
    ]
    harness.run()
    assert harness.screen.saw("-20.0 dBFS")
    assert harness.screen.saw("-inf dBFS")
    assert harness.screen.saw("Peak  █")


def test_the_meter_can_be_turned_off(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, ["1", "8", -1, "8", ESC, "q"])
    harness.run()
    assert harness.screen.saw("Level meter: off")
    assert harness.screen.saw("Level  (off)")
    assert len(harness.audio.streams) == 2


def record_keys(harness: Harness) -> list[Key]:
    audio = harness.audio
    return ["1", "5", lambda: audio.feed(1000), lambda: audio.feed(-2000), "5", -1]


def test_record_play_and_save_a_clip(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [])
    harness.screen.keys = [
        *record_keys(harness),
        "6",
        "7",
        *typed("../Hey Autobot"),
        ENTER,
        ESC,
        "q",
    ]
    harness.run()
    screen = harness.screen
    assert screen.saw("REC")
    assert screen.saw("Recorded 0.2 s")
    assert screen.saw("Saved fixtures/Hey-Autobot.wav (0.2 s)")
    clip, index = harness.audio.played[0]
    assert (index, len(clip.samples)) == (3, 2560)
    saved = harness.fixture_dir / "Hey-Autobot.wav"
    assert [entry.name for entry in harness.fixture_dir.iterdir()] == [
        "Hey-Autobot.wav"
    ]
    with wave.open(str(saved), "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (
            1,
            2,
            16000,
        )
        assert wav.getnframes() == 2560


def test_saving_over_a_file_needs_confirmation(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [])
    harness.fixture_dir.mkdir()
    existing = harness.fixture_dir / "clip.wav"
    existing.write_bytes(b"original")
    save: list[Key] = ["7", *typed("clip"), ENTER]
    harness.screen.keys = [*record_keys(harness), *save, "n", -1, *save, "y", ESC, "q"]
    seen: list[bytes] = []
    harness.screen.keys.insert(
        len(record_keys(harness)) + len(save) + 2,
        lambda: seen.append(existing.read_bytes()),
    )
    harness.run()
    assert seen == [b"original"]
    assert harness.screen.saw("clip.wav already exists.")
    assert harness.screen.saw("Not saved: clip.wav was left as it is")
    assert existing.read_bytes().startswith(b"RIFF")


def test_a_blank_file_name_is_refused_in_the_dialog(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [])
    harness.screen.keys = [
        *record_keys(harness),
        "7",
        *typed("..."),
        ENTER,
        ESC,
        ESC,
        "q",
    ]
    harness.run()
    assert harness.screen.saw("the file name needs at least one letter or digit")
    assert not harness.fixture_dir.exists()


def test_play_and_save_are_disabled_without_a_clip(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, ["1", "6", "7", ESC, "q"])
    harness.run()
    assert harness.audio.played == []
    assert not harness.screen.saw("═ Save the clip ")


def test_an_input_device_that_does_not_resolve_is_one_line(tmp_path: Path) -> None:
    text = CONFIG_TEXT.replace(
        'input_device = "pipewire"', 'input_device = "no such mic"'
    )
    harness = make_harness(tmp_path, ["1", "5", ESC, "q"], config_text=text)
    harness.run()
    assert harness.screen.saw("no input device matches 'no such mic'")
    assert harness.screen.saw("Level  (no input stream)")
    assert harness.audio.streams == []


# --- terminal size ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rows", "cols"), [(5, 20), (1, 1), (MIN_HEIGHT - 1, 80), (24, MIN_WIDTH - 1)]
)
def test_a_terminal_that_is_too_small_says_so_and_still_quits(
    tmp_path: Path, rows: int, cols: int
) -> None:
    # Keys other than F10 are ignored, so nothing is changed blind.
    harness = make_harness(tmp_path, [ENTER, "4", ENTER, F10], rows=rows, cols=cols)
    assert harness.run() == 0
    assert harness.config_text() == CONFIG_TEXT
    if rows >= 2 and cols >= 20:
        assert harness.screen.saw("too small")


@pytest.mark.parametrize(
    ("rows", "cols"), [(MIN_HEIGHT, MIN_WIDTH), (24, 80), (50, 200)]
)
def test_every_screen_draws_at_any_supported_size(
    tmp_path: Path, rows: int, cols: int
) -> None:
    keys: list[Key] = [
        ENTER,
        "1",
        ESC,
        ESC,
        "2",
        ENTER,
        ESC,
        "4",
        ESC,
        ESC,
        "3",
        ESC,
        "4",
        ESC,
        F1,
        ESC,
        "q",
    ]
    harness = make_harness(tmp_path, keys, rows=rows, cols=cols)
    assert harness.run() == 0
    assert not harness.screen.saw("too small")
    assert harness.screen.saw("Level meter: on")


def test_resizing_while_open_redraws_without_crashing(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [])
    screen = harness.screen
    screen.keys = [
        ENTER,
        lambda: screen.resize(10, 30),
        curses.KEY_RESIZE,
        -1,
        lambda: screen.resize(40, 120),
        curses.KEY_RESIZE,
        -1,
        ESC,
        "q",
    ]
    assert harness.run() == 0
    assert screen.saw("Need 72x22, have 30x10.")
    assert len(screen.frames[-1].splitlines()[0]) > 80
