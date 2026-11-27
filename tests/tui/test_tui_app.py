"""The whole interface, driven by scripted keys on a fake screen.

These run the real `App`, views and dialogs; only the terminal, the audio
hardware and the commands are fakes.
"""

import curses
import dataclasses
import json
import re
import sys
import time
import tomllib
import wave
from datetime import datetime
from pathlib import Path

import numpy as np
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

from autobot.config import Config
from autobot.tui.app import (
    LISTENER_POLL_SECONDS,
    MIN_HEIGHT,
    MIN_WIDTH,
    MODELS_ROOT_ENV_VAR,
    Services,
    default_services,
)
from autobot.tui.audio import Clip
from autobot.tui.monitor import EventStream
from autobot.tui.runner import CommandResult
from autobot.tui.screens.candidate import (
    INSTALL_QUESTION,
    NO_CANDIDATE,
    REVERT_QUESTION,
)
from autobot.tui.screens.dataset import DELETE_WARNING
from autobot.tui.screens.wake_phrase import (
    CANDIDATE_QUESTION,
    DEFAULT_MODEL,
    parse_threshold,
    step_threshold,
)
from autobot.tui.state import State
from autobot.wakeword.dataset import (
    FIXTURE_NAMES,
    LABEL_AMBIENT,
    LABEL_NEGATIVE,
    LABEL_POSITIVE,
    MANIFEST,
    ClipRecord,
    HeldoutSize,
    VoiceDataset,
    voiced_span,
)
from autobot.wakeword.evaluate import (
    MIN_RATE_MINUTES,
    TOO_SMALL,
    EvaluateError,
    KindScores,
    RealScores,
)
from autobot.wakeword.script import Prompt

# The main menu's entries, by the digit that chooses them.
SESSION, TRAIN, CANDIDATE, MONITOR, DATASET = "1", "2", "3", "4", "5"
AUDIO, WAKE, SERVICE, HOST, FACE = "6", "7", "8", "9", "0"


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
        TRAIN,
        ESC,
        CANDIDATE,
        ESC,
        MONITOR,
        *typed("Sam"),
        ENTER,
        ESC,
        DATASET,
        ESC,
        AUDIO,
        ESC,
        WAKE,
        ESC,
        SERVICE,
        ESC,
        HOST,
        ESC,
        "q",
    ]
    harness = make_harness(tmp_path, keys, hostname="nuc")
    assert harness.run() == 0
    screen = harness.screen
    for title in (
        "Main Menu",
        "═ Train the wake-word model ",
        "═ Candidate ",
        "┌─ Models ",
        "═ Live monitor ",
        "┌─ Listener ",
        "═ Dataset ",
        "═ Audio ",
        "═ Wake phrase ",
        "═ Service ",
        "═ Host ",
    ):
        assert screen.saw(title), title
    # The cloud placeholders are gone; the studio leads the menu.
    assert not screen.saw("Cloud deployment")
    assert not screen.saw("Identity and access management")
    assert screen.saw("► 1  Recording session")
    assert screen.saw("F10 Quit")
    # Nothing was changed, so nothing was written.
    assert harness.config_text() == CONFIG_TEXT


def test_the_menu_moves_with_the_arrows_and_quit_is_an_entry(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [UP, ENTER])
    assert harness.run() == 0
    # Quit is the thirteenth entry, after Google Cloud: it has no number.
    assert harness.screen.saw("►    Quit")


def test_zero_chooses_the_tenth_entry(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [FACE, -1, F10])
    assert harness.run() == 0
    assert harness.screen.saw("═ Face ")


def test_f10_quits_from_inside_a_screen_and_closes_the_stream(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [AUDIO, F10])
    assert harness.run() == 0
    assert harness.audio.streams and all(
        stream.closed for stream in harness.audio.streams
    )


def test_help_opens_and_closes(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [F1, ENTER, "q"])
    harness.run()
    assert harness.screen.saw("═ Help ")
    assert harness.screen.saw("Every change is written to the config file at once.")


@pytest.mark.parametrize(("rows", "cols"), [(24, 80), (MIN_HEIGHT, MIN_WIDTH)])
def test_help_names_the_keys_of_the_screen_it_is_opened_on(
    tmp_path: Path, rows: int, cols: int
) -> None:
    place_candidate(tmp_path)
    keys: list[Key] = []
    # Session (as Charles), Train, Candidate, Monitor (as Charles), Dataset, Wake.
    for opening in (
        ["1", *"Charles", ENTER],
        ["2"],
        [CANDIDATE, -1],
        ["4", *"Charles", ENTER],
        ["5"],
        [WAKE],
    ):
        keys += [*opening, -1, F1, -1, ENTER, ESC]
    keys += [F1, -1, ENTER, "q"]
    harness = make_harness(tmp_path, keys, hostname="nuc", rows=rows, cols=cols)
    assert harness.run() == 0
    helps = [frame for frame in harness.screen.frames if "═ Help " in frame]
    own = {
        "Recording session": ["R              record it again", "N, B  ", "Enter: OK"],
        "Train the wake-word model": ["Enter, S       start the training", "C     "],
        "Candidate": ["PgUp, PgDn     scroll the figures", "1 to 9, 0 "],
        "Live monitor": ["M              a wake was missed", "S              start"],
        "Dataset": ["E              export it as a test fixture", "D        "],
        "Wake phrase": ["+, -           move the threshold", "Up, Down  "],
        "Main Menu": ["1 to 9, 0      choose an entry by its number"],
    }
    for title, wanted in own.items():
        frame = next(frame for frame in helps if f"═ {title} " in frame)
        for text in [*wanted, "F1             this help", "T              the next"]:
            assert text in frame, (title, text)
    # A screen that is not a menu does not claim the menu's keys.
    for title in ("Recording session", "Live monitor", "Train the wake-word model"):
        frame = next(frame for frame in helps if f"═ {title} " in frame)
        assert "choose an entry by its number" not in frame
        assert "move the selection" not in frame
    # No line of any help is cut short, at either size.
    for frame in helps:
        rows_shown = frame.splitlines()
        top = next(index for index, row in enumerate(rows_shown) if "═ Help " in row)
        left = rows_shown[top].index("╔═ Help ")
        right = rows_shown[top].index("╗", left)
        inside = [row[left : right + 1] for row in rows_shown[top + 1 :]]
        last = next(index for index, row in enumerate(inside) if "Enter: OK" in row)
        assert not any("~" in row for row in inside[:last]), frame
    assert all(
        len(row) <= cols
        for frame in harness.screen.frames
        for row in frame.splitlines()
    )


def test_a_screen_opens_on_the_config_file_as_it_is_now(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [])

    def edit_by_hand() -> None:
        text = harness.config_text().replace("threshold = 0.5", "threshold = 0.3")
        assert text != harness.config_text()
        harness.config_file.write_text(text, encoding="utf-8")

    def break_it() -> None:
        harness.config_file.write_text(harness.config_text() + "[oops\n")

    harness.screen.keys = [
        edit_by_hand,
        WAKE,
        "+",
        -1,
        ESC,
        break_it,
        WAKE,
        -1,
        ESC,
        "q",
    ]
    assert harness.run() == 0
    screen = harness.screen
    # The step is taken from the value in the file, not from the one read at start.
    assert screen.saw("Threshold        0.3")
    assert screen.saw("Saved: [wake_phrases #1] threshold = 0.35")
    # A file that no longer parses is one line; the screen opens on what it had.
    assert screen.saw("Not read again: config file autobot.toml is not valid TOML")
    assert "threshold = 0.35" in harness.config_text()


def test_the_script_running_out_is_an_error_not_a_hang(tmp_path: Path) -> None:
    with pytest.raises(ScriptExhausted):
        make_harness(tmp_path, [AUDIO]).run()


# --- wake phrase ----------------------------------------------------------------


def test_the_threshold_is_typed_and_written_back(tmp_path: Path) -> None:
    keys: list[Key] = [
        WAKE,
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
    keys: list[Key] = [WAKE, ENTER, *typed("x"), ENTER, ESC, ESC, "q"]
    harness = make_harness(tmp_path, keys)
    harness.run()
    assert harness.screen.saw("type a number, for example 0.5")
    assert harness.config_text() == CONFIG_TEXT


def test_plus_and_minus_move_the_threshold(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [WAKE, "+", "+", "-", ESC, "q"])
    harness.run()
    assert phrase(harness)["threshold"] == 0.55


def test_choosing_openwakeword_writes_a_default_model_and_warns(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [WAKE, "2", DOWN, ENTER, ESC, "q"])
    harness.run()
    assert phrase(harness)["kind"] == "openwakeword"
    assert phrase(harness)["model"] == DEFAULT_MODEL
    assert harness.screen.saw(f"the model file {DEFAULT_MODEL} does not exist yet")
    assert harness.screen.saw("(file does not exist)")


def test_no_warning_when_the_model_file_exists(tmp_path: Path) -> None:
    model = tmp_path / DEFAULT_MODEL
    model.parent.mkdir(parents=True)
    model.write_bytes(b"onnx")
    harness = make_harness(tmp_path, [WAKE, "2", DOWN, ENTER, ESC, "q"])
    harness.run()
    assert phrase(harness)["kind"] == "openwakeword"
    assert not harness.screen.saw("does not exist")


def test_cancelling_the_kind_dialog_changes_nothing(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [WAKE, "2", DOWN, ESC, ESC, "q"])
    harness.run()
    assert harness.config_text() == CONFIG_TEXT


def test_the_model_cannot_be_emptied_while_openwakeword_needs_it(
    tmp_path: Path,
) -> None:
    text = CONFIG_TEXT.replace('kind = "text_match"', 'kind = "openwakeword"').replace(
        'model = ""', 'model = "m.onnx"'
    )
    keys: list[Key] = [WAKE, "3", *[BACKSPACE] * 6, ENTER, ESC, ESC, "q"]
    harness = make_harness(tmp_path, keys, config_text=text)
    harness.run()
    assert harness.screen.saw("openwakeword needs a model file path")
    assert harness.config_text() == text


def test_the_acknowledgement_runs_the_listener_command(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [WAKE, "5", ESC, "q"])
    harness.run()
    assert harness.runner.calls == [[sys.executable, "-m", "autobot.listener.ack"]]
    assert harness.screen.saw("Acknowledgement played ('Yes?').")


def test_the_acknowledgement_says_so_when_the_listener_is_not_built(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path, [WAKE, "5", ESC, "q"], available=False)
    harness.run()
    assert harness.runner.calls == []
    assert harness.screen.saw(
        "The acknowledgement is not built yet (autobot.listener.ack is missing)."
    )


def test_a_failing_acknowledgement_shows_its_last_line(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [WAKE, "5", ENTER, ESC, "q"])
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
        WAKE,
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
        WAKE,
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
    harness.screen.keys = [WAKE, "4", "s", F10, DOWN, DOWN, ENTER, F10, DOWN, ENTER]
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
        tmp_path, [WAKE, "4", "s", ESC, ESC, "q"], train_argv=["autobot-no-such-make"]
    )
    harness.run()
    assert harness.screen.saw(
        "Cannot start the training: command not found: autobot-no-such-make"
    )


# --- host -------------------------------------------------------------------------


def test_host_sets_nuc_hostname_to_this_machine(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [HOST, ENTER, ESC, "q"], hostname="testbox")
    harness.run()
    assert config(harness)["host"] == {"nuc_hostname": "testbox"}
    assert comments(harness.config_text()) == comments(CONFIG_TEXT)
    assert harness.screen.saw("This machine is not the configured NUC.")
    assert harness.screen.saw("This machine is the NUC.")


# --- service ----------------------------------------------------------------------


def test_service_entries_run_the_listener_control_command(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [SERVICE, "1", "2", "3", "4", "5", ESC, "q"])
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
    harness = make_harness(tmp_path, [SERVICE, "3", ESC, "q"])
    harness.runner.result = CommandResult(1, "the unit is not installed\n")
    harness.run()
    assert harness.screen.saw("Start failed: the unit is not installed")


def test_uninstall_asks_first(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [SERVICE, "7", "n", "7", "y", ESC, "q"])
    harness.run()
    assert [call[-1] for call in harness.runner.calls] == ["uninstall"]


def test_service_says_so_when_the_listener_is_not_built(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [SERVICE, "5", ESC, "q"], available=False)
    harness.run()
    assert harness.runner.calls == []
    assert harness.screen.saw(
        "The listener is not built yet (autobot.listener.control is missing)."
    )


# --- audio ------------------------------------------------------------------------


def test_the_audio_screen_shows_devices_and_pipewire_defaults(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [AUDIO, ESC, ESC, "q"])
    harness.run()
    screen = harness.screen
    assert screen.saw("Input device         'pipewire'  (#3 pipewire)")
    assert screen.saw("PipeWire microphone  AIRHUG 21 Analog Stereo")
    assert screen.saw("PipeWire speaker     Built-in Audio Analog Stereo")
    assert harness.audio.opened[0] == 3


def test_the_audio_screen_says_when_the_microphone_delivers_silence(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path, [])

    def muted() -> None:
        for _ in range(30):
            harness.audio.feed(0)

    def speaking() -> None:
        harness.audio.feed(3000)

    harness.screen.keys = [AUDIO, -1, muted, -1, -1, speaking, -1, -1, ESC, "q"]
    harness.run()
    screen = harness.screen
    assert screen.saw(
        "NO SIGNAL  the microphone delivers silence: check its mute button"
    )
    assert screen.saw(
        "No signal: the microphone delivers silence: check its mute button."
    )
    assert screen.saw("The microphone is delivering a signal again.")
    # Before the silence, and after it, the RMS meter is where the warning was.
    first = next(frame for frame in screen.frames if "═ Audio " in frame)
    assert "NO SIGNAL" not in first and "RMS" in first


def test_picking_an_input_device_offers_only_usable_ones_and_writes_it(
    tmp_path: Path,
) -> None:
    # The picker opens on the current device (pipewire); Down moves to "default".
    harness = make_harness(tmp_path, [AUDIO, "1", DOWN, ENTER, ESC, "q"])
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
    harness = make_harness(tmp_path, [AUDIO, "2", UP, ENTER, ESC, "q"])
    harness.run()
    audio = config(harness)["audio"]
    assert isinstance(audio, dict)
    assert audio["output_device"] == "sysdefault"
    assert harness.screen.saw("PipeWire speaker     (not used by this output device)")


def test_setting_the_pipewire_default_source(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [AUDIO, "3", DOWN, ENTER, ESC, "q"])
    harness.run()
    assert ["wpctl", "set-default", "49"] in harness.wpctl_calls
    assert harness.screen.saw("*  33  AIRHUG 21 Analog Stereo")
    assert harness.screen.saw(
        "PipeWire default source is now Built-in Audio Analog Stereo"
    )
    # Nothing about PipeWire is stored in the config file.
    assert harness.config_text() == CONFIG_TEXT


def test_cancelling_the_pipewire_dialog_sets_nothing(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [AUDIO, "4", ESC, ESC, "q"])
    harness.run()
    assert all(call[1] == "status" for call in harness.wpctl_calls)


def test_the_meter_follows_the_input(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [])
    audio = harness.audio
    harness.screen.keys = [
        AUDIO,
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
    harness = make_harness(tmp_path, [AUDIO, "8", -1, "8", ESC, "q"])
    harness.run()
    assert harness.screen.saw("Level meter: off")
    assert harness.screen.saw("Level  (off)")
    assert len(harness.audio.streams) == 2


def record_keys(harness: Harness) -> list[Key]:
    audio = harness.audio
    return [AUDIO, "5", lambda: audio.feed(1000), lambda: audio.feed(-2000), "5", -1]


def test_record_play_and_save_a_clip(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [])
    harness.screen.keys = [
        *record_keys(harness),
        "6",
        "7",
        ENTER,
        *typed("../Charles S"),
        ENTER,
        ESC,
        "q",
    ]
    harness.run()
    screen = harness.screen
    assert screen.saw("REC")
    assert screen.saw("Recorded 0.2 s")
    assert screen.saw("Saves the last recording into data/voice/")
    assert screen.saw('The phrase "autobot" (positive)')
    clip, index = harness.audio.played[0]
    assert (index, len(clip.samples)) == (3, 2560)

    # The clip is in the voice dataset: one WAV and one manifest line.
    voice = tmp_path / "data" / "voice"
    assert harness.dataset.root == voice
    (line,) = harness.manifest()
    name = line["file"]
    assert re.fullmatch(r"Charles-S-session-\d{8}-\d{6}\.wav", name)
    assert sorted(entry.name for entry in voice.iterdir()) == sorted(
        [name, "manifest.jsonl"]
    )
    assert screen.saw(f"Saved data/voice/{name} (0.2 s, positive)")
    recorded_at = line.pop("recorded_at")
    assert datetime.fromisoformat(recorded_at).tzinfo is not None
    assert line == {
        "file": name,
        "phrase": "autobot",
        "prompt": "audio screen",
        "prompt_id": "",
        "label": "positive",
        "source": "session",
        "speaker": "../Charles S",
        "device": "pipewire",
    }
    with wave.open(str(voice / name), "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (
            1,
            2,
            16000,
        )
        assert wav.getnframes() == 2560
    # A committed fixture is never a side effect of testing the microphone.
    assert not harness.fixture_dir.exists()


def test_a_second_save_never_overwrites_and_remembers_the_speaker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Both saves happen in the same second, so they ask for the same file name.
    monkeypatch.setattr(
        "autobot.tui.screens.audio.recorded_now", lambda: "2026-11-26T17:40:03-05:00"
    )
    harness = make_harness(tmp_path, [])
    harness.screen.keys = [
        *record_keys(harness),
        "7",
        ENTER,
        *typed("Sam"),
        ENTER,
        "7",
        DOWN,
        DOWN,
        ENTER,
        # The speaker's name is offered again: Enter accepts it.
        ENTER,
        ESC,
        "q",
    ]
    harness.run()
    first, second = harness.manifest()
    assert (first["file"], second["file"]) == (
        "Sam-session-20261126-174003.wav",
        "Sam-session-20261126-174003-2.wav",
    )
    assert (first["label"], second["label"]) == ("positive", "ambient")
    assert (first["speaker"], second["speaker"]) == ("Sam", "Sam")
    assert [record.file for record in harness.dataset.records()] == [
        first["file"],
        second["file"],
    ]
    assert not harness.fixture_dir.exists()


def test_a_blank_speaker_is_refused_and_cancelling_saves_nothing(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path, [])
    harness.screen.keys = [
        *record_keys(harness),
        # Cancel at the label, then at the speaker's name.
        "7",
        ESC,
        "7",
        DOWN,
        ENTER,
        *typed("..."),
        ENTER,
        ESC,
        ESC,
        "q",
    ]
    harness.run()
    assert harness.screen.saw("What is in the clip?")
    assert harness.screen.saw("the name needs at least one letter or digit")
    assert not harness.dataset.root.exists()
    assert not harness.fixture_dir.exists()


def test_services_default_to_the_dataset_in_the_repository(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [])
    given = harness.app.services
    services = Services(
        **{
            field.name: getattr(given, field.name)
            for field in dataclasses.fields(given)
            if field.name != "dataset"
        }
    )
    assert services.dataset.root == tmp_path / "data" / "voice"
    assert services.dataset.manifest == tmp_path / MANIFEST


def test_services_default_to_the_listeners_socket_and_the_repository_models(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path, [])
    services = dataclasses.replace(
        harness.app.services,
        events=None,  # type: ignore[arg-type]
        models_root=None,  # type: ignore[arg-type]
    )
    assert isinstance(services.events, EventStream)
    assert services.events.path == tmp_path / "run" / "listener.sock"
    assert not services.events.connected
    assert services.models_root == tmp_path


def test_the_models_root_can_be_pointed_elsewhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(MODELS_ROOT_ENV_VAR, raising=False)
    assert default_services().models_root == default_services().repo
    monkeypatch.setenv(MODELS_ROOT_ENV_VAR, str(tmp_path))
    services = default_services()
    assert services.models_root == tmp_path
    # Only the model files move: the listener's run directory does not.
    assert isinstance(services.events, EventStream)
    assert services.events.path == services.repo / "run" / "listener.sock"


def test_play_and_save_are_disabled_without_a_clip(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [AUDIO, "6", "7", ESC, "q"])
    harness.run()
    assert harness.audio.played == []
    assert not harness.screen.saw("═ Save the clip ")


def test_an_input_device_that_does_not_resolve_is_one_line(tmp_path: Path) -> None:
    text = CONFIG_TEXT.replace(
        'input_device = "pipewire"', 'input_device = "no such mic"'
    )
    harness = make_harness(tmp_path, [AUDIO, "5", ESC, "q"], config_text=text)
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
    harness = make_harness(tmp_path, [ENTER, HOST, ENTER, F10], rows=rows, cols=cols)
    assert harness.run() == 0
    assert harness.config_text() == CONFIG_TEXT
    if rows >= 2 and cols >= 20:
        assert harness.screen.saw("too small")


# Every screen at every supported size, in every theme: tests/tui/test_tui_themes.py.


def test_resizing_while_open_redraws_without_crashing(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [])
    screen = harness.screen
    screen.keys = [
        AUDIO,
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


# --- the main menu: the studio's road ---------------------------------------------

OPENWAKEWORD = CONFIG_TEXT.replace(
    'kind = "text_match"', 'kind = "openwakeword"'
).replace('model = ""', f'model = "{DEFAULT_MODEL}"')
RATE = 16000


def place(tmp_path: Path, relative: str) -> Path:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"onnx")
    return path


def add_clip(
    harness: Harness,
    label: str = LABEL_POSITIVE,
    *,
    seconds: float = 0.5,
    prompt: str = "autobot",
    prompt_id: str = "bare-01",
    speaker: str = "Charles",
    samples: "np.ndarray[tuple[int], np.dtype[np.int16]] | None" = None,
    at: str = "2026-11-26T17:40:03-05:00",
) -> ClipRecord:
    """Put one clip into the harness's voice dataset, as a session would."""
    if samples is None:
        samples = np.full(int(seconds * RATE), 3000, dtype=np.int16)
    return harness.dataset.add(
        Clip(samples, RATE),
        ClipRecord(
            file="",
            phrase="autobot",
            prompt=prompt,
            prompt_id=prompt_id,
            label=label,
            source="session",
            speaker=speaker,
            device="pipewire",
            recorded_at=at,
        ),
    )


def test_the_main_menu_lists_the_studio_first(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [-1, "q"])
    harness.run()
    frame = harness.screen.frames[0]
    labels = [
        "1  Recording session",
        "2  Train the model",
        "3  Candidate",
        "4  Live monitor",
        "5  Dataset",
        "6  Audio",
        "7  Wake phrase",
        "8  Service",
        "9  Host",
        "0  Face",
        "   Agent routing",
        "   Google Cloud",
        "   Quit",
    ]
    positions = [frame.index(label) for label in labels]
    assert positions == sorted(positions)


def test_a_fresh_checkout_is_untrained_with_an_empty_dataset(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [-1, "q"])
    harness.run()
    screen = harness.screen
    assert screen.saw("State   UNTRAINED  no trained model is installed.")
    assert not screen.saw("is kept, not counted as installed")
    assert screen.saw("Dataset: 0 positives, 0 negatives, 0 min ambient")
    assert screen.saw("Training is offered once a session is complete")
    # The state is in the title bar too, on every screen.
    assert screen.frames[0].splitlines()[0].startswith(" autobot ")
    assert "UNTRAINED" in screen.frames[0].splitlines()[0]
    # Looking at the menu wrote nothing.
    assert not harness.dataset.root.exists()


def test_a_leftover_model_is_named_as_kept_and_the_state_stays_untrained(
    tmp_path: Path,
) -> None:
    place(tmp_path, DEFAULT_MODEL)
    harness = make_harness(tmp_path, [-1, "q"], rows=MIN_HEIGHT, cols=MIN_WIDTH)
    harness.run()
    screen = harness.screen
    assert screen.saw("State   UNTRAINED  no trained model is installed.")
    # Whole, at the minimum width.
    assert screen.saw("models/wakeword/autobot.onnx is kept, not counted as installed.")
    assert not screen.saw("INSTALLED ")


def test_the_dataset_line_counts_the_clips_and_the_ambient_minutes(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path, [-1, "q"])
    add_clip(harness)
    add_clip(harness, prompt_id="hey-01")
    add_clip(harness, LABEL_NEGATIVE, prompt_id="hard-01")
    add_clip(harness, LABEL_AMBIENT, prompt_id="ambient-01", seconds=90)
    harness.run()
    assert harness.screen.saw("Dataset: 2 positives, 1 negatives, 1.5 min ambient")
    # A handful of clips is not a session.
    assert harness.screen.saw("Training is offered once a session is complete")


def test_a_damaged_manifest_is_one_line_on_the_main_menu(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [-1, "q"])
    harness.dataset.root.mkdir(parents=True)
    harness.dataset.manifest.write_text("{not json\n", encoding="utf-8")
    assert harness.run() == 0
    assert harness.screen.saw("Dataset: ")
    assert harness.screen.saw("line 1 is not valid JSON")


def test_a_candidate_file_makes_the_state_candidate_ready(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [])

    def train() -> None:
        place(tmp_path, "models/wakeword/candidate.onnx")

    harness.screen.keys = [-1, train, -1, "q"]
    harness.run()
    screen = harness.screen
    assert "UNTRAINED" in screen.frames[0]
    assert screen.saw("State   CANDIDATE READY  a trained model awaits your decision")
    assert "CANDIDATE READY" in screen.frames[-1].splitlines()[0]


def test_an_installed_model_with_its_listener_running_is_installed(
    tmp_path: Path,
) -> None:
    place(tmp_path, DEFAULT_MODEL)
    harness = make_harness(tmp_path, [-1, -1, "q"], config_text=OPENWAKEWORD)
    harness.run()
    assert harness.screen.saw(
        "State   INSTALLED  the trained model is installed and the listener runs."
    )
    assert not harness.screen.saw("LISTENER DOWN")
    assert not harness.screen.saw("is kept, not counted")


def test_an_installed_model_without_its_listener_is_listener_down(
    tmp_path: Path,
) -> None:
    place(tmp_path, DEFAULT_MODEL)
    harness = make_harness(tmp_path, [-1, -1, "q"], config_text=OPENWAKEWORD)
    harness.runner.status = CommandResult(
        0, "listener: not running\nunit:     not installed (foreground mode)\n"
    )
    harness.run()
    assert harness.screen.saw("State   LISTENER DOWN  a model is installed but the")
    assert "LISTENER DOWN" in harness.screen.frames[-1].splitlines()[0]


def test_untrained_does_not_turn_into_listener_down(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [-1, -1, "q"])
    harness.runner.status = CommandResult(0, "listener: not running\n")
    harness.run()
    assert harness.screen.saw("State   UNTRAINED")
    assert not harness.screen.saw("LISTENER DOWN")


def test_the_listener_is_asked_on_entry_on_return_and_every_ten_seconds(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path, [])
    now = [100.0]
    harness.app.services.clock = lambda: now[0]
    polls: list[int] = []

    def count() -> None:
        polls.append(len(harness.runner.polls))

    def later(seconds: float) -> Key:
        def advance() -> None:
            now[0] += seconds

        return advance

    harness.screen.keys = [
        -1,
        count,  # asked once, on entry
        -1,
        -1,
        count,  # not again while nothing changed
        later(LISTENER_POLL_SECONDS - 1),
        -1,
        count,
        later(1),
        -1,
        -1,
        count,  # ten seconds on: asked again
        HOST,
        later(60),
        -1,
        -1,
        count,  # not while another screen is on top
        ESC,
        -1,
        count,  # back on the main menu: asked at once
        "q",
    ]
    harness.run()
    assert polls == [1, 1, 1, 2, 2, 3]
    assert harness.runner.polls[0] == [
        sys.executable,
        "-m",
        "autobot.listener.control",
        "status",
        "--sample",
        "0.1",
    ]
    # The question is not one of the commands a screen ran for the owner.
    assert harness.runner.calls == []


def test_the_listener_is_not_asked_when_it_is_not_built(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [-1, -1, "q"], available=False)
    harness.run()
    assert harness.runner.polls == []
    assert harness.screen.saw("State   UNTRAINED")


def test_require_nuc_gives_the_listeners_one_line(tmp_path: Path) -> None:
    there = make_harness(tmp_path, [], hostname="testbox")
    assert there.app.require_nuc("installing a model") is False
    assert (
        there.app.status
        == "installing a model runs on the NUC only; this host is testbox"
    )
    assert there.app.status_role == "error"
    here = make_harness(tmp_path, [], hostname="Nuc")
    assert here.app.require_nuc("the live monitor") is True
    assert here.app.status == ""


# --- training, from the main menu -------------------------------------------------


def test_train_the_model_opens_the_training_pane_directly(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [TRAIN, -1, ESC, "q"])
    harness.run()
    screen = harness.screen
    assert screen.saw("═ Train the wake-word model ")
    assert screen.saw(
        "The run writes a candidate, installs nothing. Esc leaves it running."
    )
    # It went straight there, not through the Wake phrase screen.
    assert not screen.saw("═ Wake phrase ")


def test_training_is_never_refused_on_an_empty_dataset(tmp_path: Path) -> None:
    harness = make_harness(
        tmp_path, [], train_argv=[sys.executable, "-c", "print('ran')"]
    )
    harness.screen.keys = [TRAIN, "s", wait_for_job(harness, False), -1, ESC, "q"]
    harness.run()
    assert harness.screen.saw("Training is offered once a session is complete")
    assert harness.screen.saw("Training started. Esc leaves it running.")
    assert harness.screen.saw("ran")


TRAIN_A_CANDIDATE = (
    "import pathlib; p = pathlib.Path('models/wakeword'); "
    "p.mkdir(parents=True, exist_ok=True); "
    "(p / 'candidate.onnx').write_bytes(b'onnx'); print('candidate ready')"
)


def test_a_training_that_ends_well_offers_the_candidate_screen(tmp_path: Path) -> None:
    harness = make_harness(
        tmp_path, [], train_argv=[sys.executable, "-c", TRAIN_A_CANDIDATE]
    )
    harness.screen.keys = [
        TRAIN,
        "s",
        wait_for_job(harness, False),
        -1,
        # The dialog: Enter is yes, and the Candidate screen opens.
        -1,
        ENTER,
        -1,
        ESC,
        ESC,
        -1,
        "q",
    ]
    harness.run()
    screen = harness.screen
    assert screen.saw("State  finished")
    assert screen.saw("═ Training finished ")
    assert screen.saw(CANDIDATE_QUESTION)
    assert CANDIDATE_QUESTION == "Candidate ready: compare and install?"
    assert screen.saw("═ Candidate ")
    assert screen.saw("Candidate  models/wakeword/candidate.onnx")
    assert screen.saw("► 1  Install the candidate")
    assert "State   CANDIDATE READY" in screen.frames[-1]
    assert "3  Candidate (ready)" in screen.frames[-1]
    # Nothing was installed: the config file is as it was.
    assert harness.config_text() == CONFIG_TEXT


def test_declining_the_hand_off_says_where_the_candidate_is(tmp_path: Path) -> None:
    harness = make_harness(
        tmp_path, [], train_argv=[sys.executable, "-c", TRAIN_A_CANDIDATE]
    )
    harness.screen.keys = [
        TRAIN,
        "s",
        wait_for_job(harness, False),
        -1,
        -1,
        "n",
        -1,
        ESC,
        "q",
    ]
    harness.run()
    screen = harness.screen
    assert screen.saw(CANDIDATE_QUESTION)
    assert screen.saw(
        "Training finished. The candidate is ready: see Candidate, main menu."
    )
    assert not screen.saw("═ Candidate ")


def test_a_training_that_ends_well_without_a_candidate_says_so(tmp_path: Path) -> None:
    harness = make_harness(
        tmp_path, [], train_argv=[sys.executable, "-c", "print('nothing written')"]
    )
    harness.screen.keys = [TRAIN, "s", wait_for_job(harness, False), -1, -1, ESC, "q"]
    harness.run()
    assert harness.screen.saw(
        "Training finished, but it left no candidate: see the log."
    )
    assert not harness.screen.saw(CANDIDATE_QUESTION)


def test_a_training_that_fails_points_nowhere(tmp_path: Path) -> None:
    harness = make_harness(
        tmp_path, [], train_argv=[sys.executable, "-c", "raise SystemExit(3)"]
    )
    harness.screen.keys = [TRAIN, "s", wait_for_job(harness, False), -1, -1, ESC, "q"]
    harness.run()
    assert harness.screen.saw("stopped with exit status 3")
    assert not harness.screen.saw("Training finished")
    assert not harness.screen.saw(CANDIDATE_QUESTION)


# --- the dataset screen -------------------------------------------------------------


def spoken_samples(
    lead: float, speech: float, tail: float
) -> "np.ndarray[tuple[int], np.dtype[np.int16]]":
    rng = np.random.default_rng(3)
    samples = rng.integers(-20, 21, round((lead + speech + tail) * RATE)).astype(
        np.int16
    )
    first, count = round(lead * RATE), round(speech * RATE)
    samples[first : first + count] = (
        6000 * np.sin(2 * np.pi * 220 * np.arange(count) / RATE)
    ).astype(np.int16)
    return samples


def test_the_dataset_screen_lists_the_clips_with_their_split(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [DATASET, -1, ESC, "q"], cols=100)
    # bare-01 is trained on and bare-11 held out (tests/wakeword/test_dataset.py).
    add_clip(harness, prompt_id="bare-01", speaker="charles")
    add_clip(harness, prompt_id="bare-11", speaker="charles", seconds=1.25)
    add_clip(
        harness,
        LABEL_NEGATIVE,
        prompt="auto bought",
        prompt_id="hard-01",
        speaker="charles",
    )
    add_clip(
        harness,
        LABEL_AMBIENT,
        prompt="say nothing",
        prompt_id="ambient-01",
        seconds=60,
        speaker="charles",
    )
    harness.run()
    screen = harness.screen
    frame = next(f for f in screen.frames if "═ Dataset " in f)
    rows = [row.strip("║► ") for row in frame.splitlines()]
    assert any(
        row.split() == ["1", "charles", "positive", "0.5", "autobot", "train"]
        for row in rows
    )
    assert any(
        row.split() == ["2", "charles", "positive", "1.2", "autobot", "held", "out"]
        for row in rows
    )
    assert any(
        row.startswith("3 charles    negative    0.5  auto bought") for row in rows
    )
    # An ambient clip is split by time, not as a whole.
    assert any(
        row.startswith("4 charles    ambient    1:00  say nothing")
        and row.endswith("last fifth held out")
        for row in rows
    )
    assert "┌─ Voice dataset: data/voice/ " in frame
    assert "Clips    2 positives, 1 negatives, 1 ambient (1 min)" in frame
    # One held-out positive, no held-out negative, the last fifth of a minute.
    assert (
        "Held out 1 of 20 positives, 0 of 20 negatives, 0.2 of 12 min ambient" in frame
    )
    assert "too small yet to tell two models apart" in frame
    assert "↑↓ Move   P Play   D Delete   E Export   Esc Back   F10 Quit" in frame


def test_an_empty_dataset_says_so(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [DATASET, "p", "d", "e", -1, ESC, "q"])
    harness.run()
    assert harness.screen.saw("(no clips yet: the Recording session records them)")
    assert harness.screen.saw("Clips    0 positives, 0 negatives, 0 ambient (0 min)")
    assert harness.audio.played == []
    assert not harness.dataset.root.exists()
    assert not harness.fixture_dir.exists()


def test_the_dataset_list_scrolls_with_many_clips(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [], rows=MIN_HEIGHT, cols=MIN_WIDTH)
    for number in range(1, 41):
        add_clip(
            harness, prompt=f"clip number {number}", prompt_id=f"bare-{number:02d}"
        )
    shots: list[str] = []

    def shot() -> None:
        shots.append(harness.screen.frames[-1])

    harness.screen.keys = [
        DATASET,
        -1,
        shot,
        *[DOWN] * 14,
        -1,
        shot,
        curses.KEY_NPAGE,
        -1,
        shot,
        curses.KEY_END,
        -1,
        shot,
        curses.KEY_PPAGE,
        curses.KEY_HOME,
        -1,
        shot,
        "p",
        ESC,
        "q",
    ]
    harness.run()
    top, scrolled, paged, end, home = shots

    def numbers(frame: str) -> list[int]:
        return [
            int(match.group(1))
            for match in re.finditer(r"^║►?\s+(\d+) Charles", frame, flags=re.MULTILINE)
        ]

    # Eleven rows of list fit the smallest terminal.
    assert numbers(top) == list(range(1, 12))
    assert " 1 of 40 " in top
    assert numbers(scrolled) == list(range(5, 16))
    assert " 15 of 40 " in scrolled
    assert numbers(paged)[-1] == 26
    assert numbers(end) == list(range(30, 41))
    assert " 40 of 40 " in end
    assert numbers(home) == list(range(1, 12))
    # P played the selected clip: the first one again.
    assert len(harness.audio.played) == 1
    assert harness.screen.saw("Playing Charles-bare-01-")


def test_p_plays_the_selected_clip_on_the_output_device(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [DATASET, DOWN, "p", -1, ESC, "q"])
    add_clip(harness, seconds=0.5)
    second = add_clip(harness, prompt_id="bare-02", seconds=0.25)
    harness.run()
    ((clip, index),) = harness.audio.played
    assert (len(clip.samples), index) == (4000, 3)
    assert harness.screen.saw(f"Playing {second.file} (0.2 s).")
    # Leaving the screen stops the playback.
    assert harness.audio.stops >= 1


def test_d_deletes_a_clip_after_saying_what_that_costs(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [])
    first = add_clip(harness)
    second = add_clip(harness, prompt_id="bare-02")
    kept: list[int] = []
    harness.screen.keys = [
        DATASET,
        # "No" deletes nothing.
        "d",
        "n",
        lambda: kept.append(len(harness.manifest())),
        "d",
        "y",
        -1,
        ESC,
        "q",
    ]
    harness.run()
    screen = harness.screen
    assert screen.saw("═ Delete the clip? ")
    assert DELETE_WARNING == (
        "Deleting a clip changes the held-out set and makes",
        "earlier evaluations incomparable.",
    )
    for line in (first.file, *DELETE_WARNING, "Delete it?"):
        assert screen.saw(line), line
    assert kept == [2]
    assert [line["file"] for line in harness.manifest()] == [second.file]
    assert not harness.dataset.path(first).exists()
    assert screen.saw(f"Deleted {first.file}.")
    assert screen.saw("Clips    1 positives")


def test_e_exports_a_clip_under_a_fixture_name_with_lead_in_and_tail(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path, [])
    # Cut close: 0.1 s before the speech and 0.2 s after it.
    stored = add_clip(harness, samples=spoken_samples(0.1, 0.6, 0.2))
    before = harness.dataset.path(stored).read_bytes()
    during: list[bool] = []
    harness.screen.keys = [
        DATASET,
        "e",
        # The picker lists the README's fixture names; the first is autobot.wav.
        lambda: during.append(harness.fixture_dir.exists()),
        ENTER,
        lambda: during.append(harness.fixture_dir.exists()),
        "y",
        -1,
        ESC,
        "q",
    ]
    harness.run()
    screen = harness.screen
    assert screen.saw("═ Export as which fixture? ")
    for name in FIXTURE_NAMES:
        assert screen.saw(name), name
    assert screen.saw("Into fixtures/, which is committed")
    assert screen.saw("═ Export the clip? ")
    assert screen.saw("A fixture is committed: this recording becomes public.")
    assert screen.saw("Speech gets 0.5 s of quiet before, 1 s after.")
    assert not screen.saw("It replaces the fixture that is there now.")
    # Nothing was written before the confirmation.
    assert during == [False, False]
    assert screen.saw("Exported to fixtures/autobot.wav.")

    target = harness.fixture_dir / "autobot.wav"
    assert [path.name for path in harness.fixture_dir.iterdir()] == ["autobot.wav"]
    with wave.open(str(target), "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (
            1,
            2,
            RATE,
        )
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(
            np.int16
        )
    span = voiced_span(samples, RATE)
    assert span is not None
    assert span[0] / RATE >= 0.5 - 0.02
    assert (len(samples) - span[1]) / RATE >= 1.0 - 0.02
    # The recording itself stays in the dataset, unchanged.
    assert harness.dataset.path(stored).read_bytes() == before
    assert len(harness.manifest()) == 1


def test_an_export_is_never_written_without_the_confirmation(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [])
    add_clip(harness, samples=spoken_samples(0.1, 0.6, 0.2))
    old = harness.fixture_dir / "hey_autobot.wav"
    old.parent.mkdir()
    old.write_bytes(b"the committed fixture")
    harness.screen.keys = [
        DATASET,
        # Cancel at the picker.
        "e",
        ESC,
        # Pick hey_autobot.wav, which exists, and say no; then Esc; then Enter
        # on the default answer, which is no.
        "e",
        DOWN,
        ENTER,
        "n",
        "e",
        DOWN,
        ENTER,
        ESC,
        "e",
        DOWN,
        ENTER,
        ENTER,
        -1,
        ESC,
        "q",
    ]
    harness.run()
    assert harness.screen.saw("It replaces the fixture that is there now.")
    assert harness.screen.saw("Nothing was exported.")
    assert old.read_bytes() == b"the committed fixture"
    assert [path.name for path in harness.fixture_dir.iterdir()] == ["hey_autobot.wav"]


def test_an_export_can_replace_a_fixture_when_confirmed(tmp_path: Path) -> None:
    harness = make_harness(
        tmp_path, [DATASET, "e", DOWN, DOWN, DOWN, ENTER, "y", ESC, "q"]
    )
    stored = add_clip(harness, LABEL_AMBIENT, prompt_id="ambient-01", seconds=1.0)
    old = harness.fixture_dir / "room_noise.wav"
    old.parent.mkdir()
    old.write_bytes(b"the committed fixture")
    harness.run()
    # An ambient clip is exported as it is.
    assert old.read_bytes() == harness.dataset.path(stored).read_bytes()
    assert harness.screen.saw("Exported to fixtures/room_noise.wav.")


def test_the_audio_screens_clip_shows_on_the_dataset_screen(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [], cols=100)
    harness.screen.keys = [
        *record_keys(harness),
        "7",
        ENTER,
        *typed("Sam"),
        ENTER,
        ESC,
        DATASET,
        -1,
        ESC,
        "q",
    ]
    harness.run()
    assert harness.screen.saw("1 Sam        positive    0.2  audio screen")
    assert harness.screen.saw("Dataset: 1 positives, 0 negatives, 0 min ambient")


def test_a_short_script_can_stand_in_for_the_real_one(tmp_path: Path) -> None:
    script = [Prompt("bare-01", "autobot", LABEL_POSITIVE, "Say it.")]
    harness = make_harness(tmp_path, [-1, "q"], hostname="nuc", script=script)
    add_clip(harness)
    harness.run()
    # One prompt, one clip: that speaker's session is complete.
    assert harness.screen.saw(
        "A session is complete (Charles): Train the model is next"
    )


# --- the candidate screen: compare, install, revert -----------------------------------

MODELS = Path("models/wakeword")
# A held-out set large enough for a verdict (TS-8), and one that is not.
LARGE = HeldoutSize(positives=25, negatives=25, ambient_minutes=12.0)
SMALL = HeldoutSize(positives=11, negatives=6, ambient_minutes=1.0)
CONTROL = [sys.executable, "-m", "autobot.listener.control"]
NOT_RUNNING = CommandResult(0, "listener: not running\n")


def real_scores(
    model: Path,
    size: HeldoutSize,
    *,
    detected: int,
    false_accepts: int = 0,
    ambient: int = 0,
    threshold: float = 0.6,
) -> RealScores:
    """One model's figures on a held-out set, as `score_real` returns them."""
    rate_known = size.ambient_minutes >= MIN_RATE_MINUTES
    row = {
        "threshold": threshold,
        "detected": detected,
        "recall": detected / size.positives if size.positives else 0.0,
        "false_accepts": false_accepts,
        "fa_rate": false_accepts / size.negatives if size.negatives else 0.0,
        "ambient_false_accepts": ambient,
        "ambient_fa_per_hour": ambient / (size.ambient_minutes / 60)
        if rate_known
        else None,
    }
    return RealScores(
        model=model,
        phrase="autobot",
        heldout_positives=size.positives,
        heldout_negatives=size.negatives,
        heldout_ambient_minutes=size.ambient_minutes,
        rows=(row,),
        recommended=row,
    )


class Scorer:
    """A `score_real` that needs no model: figures by the model file's bytes."""

    def __init__(self, size: HeldoutSize, **detected: int) -> None:
        self.size = size
        # What a model whose file holds these bytes detects of the positives.
        self.detected = detected
        self.calls: list[str] = []

    def __call__(self, model: Path, dataset: VoiceDataset, phrase: str) -> RealScores:
        self.calls.append(model.name)
        content = model.read_bytes().decode()
        return real_scores(
            model,
            self.size,
            detected=self.detected[content],
            threshold=0.6 if content == "candidate" else 0.85,
        )


def place_model(
    tmp_path: Path, name: str, content: str, threshold: float | None
) -> Path:
    """A model file with these bytes, and its evaluation beside it."""
    path = tmp_path / MODELS / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode())
    if threshold is not None:
        evaluation = {
            "phrase": "autobot",
            "recommended_threshold": threshold,
            "trained": "2026-11-26T12:42:22",
            "metrics_at_threshold": {
                "threshold": 0.8,
                "recall": 0.6637,
                "fa_hard_seen": 0.022,
                "fa_hard_unseen": 0.0048,
                "val_fa_per_hour": 0.8414,
            },
        }
        path.with_suffix(".json").write_text(json.dumps(evaluation), encoding="utf-8")
    return path


def place_candidate(tmp_path: Path, threshold: float = 0.6) -> Path:
    return place_model(tmp_path, "candidate.onnx", "candidate", threshold)


def training_leaves_a_candidate(tmp_path: Path) -> Key:
    """A key that places a candidate, as a training run that ended would."""

    def place() -> None:
        place_candidate(tmp_path, 0.6)

    return place


def model_bytes(tmp_path: Path, name: str) -> str:
    return (tmp_path / MODELS / name).read_bytes().decode()


def candidate_frame(harness: Harness, text: str) -> str:
    """The first frame of the Candidate screen that shows `text`."""
    return next(
        frame
        for frame in harness.screen.frames
        if "═ Candidate " in frame and text in frame
    )


def test_with_no_candidate_the_screen_says_so_in_one_line(tmp_path: Path) -> None:
    scorer = Scorer(SMALL)
    harness = make_harness(
        tmp_path, [CANDIDATE, -1, ESC, "q"], hostname="nuc", score_real=scorer
    )
    harness.run()
    screen = harness.screen
    assert NO_CANDIDATE == "no candidate yet: train the model first"
    assert screen.saw(f" {NO_CANDIDATE}")
    assert screen.saw("Candidate  none")
    assert screen.saw("Installed  none")
    assert not screen.saw("Install the candidate")
    assert not screen.saw("Revert to the previous model")
    assert scorer.calls == []


def test_with_nothing_installed_the_candidate_is_offered_and_the_leftover_is_kept(
    tmp_path: Path,
) -> None:
    place_model(tmp_path, "autobot.onnx", "leftover", None)
    place_candidate(tmp_path, 0.6)
    scorer = Scorer(SMALL, candidate=10)
    harness = make_harness(tmp_path, [], hostname="nuc", score_real=scorer)
    harness.runner.status = NOT_RUNNING
    harness.screen.keys = [CANDIDATE, -1, "1", -1, "y", -1, ESC, -1, "q"]
    harness.run()
    screen = harness.screen

    # One column of figures: the leftover is named as kept and is not scored.
    frame = candidate_frame(harness, "positives detected")
    assert (
        "Candidate  models/wakeword/candidate.onnx (trained 2026-11-26 12:42)" in frame
    )
    assert "Installed  none; autobot.onnx is a leftover: kept, not compared" in frame
    assert "nothing is installed: the candidate has nothing to beat" in frame
    assert (
        "held-out real recordings: 11 positives, 6 negatives, 1.0 min ambient" in frame
    )
    assert "candidate" in frame and "   installed" not in frame
    assert "positives detected              10 of 11" in frame
    assert "too little held-out ambient to judge false accepts per hour" in frame
    assert scorer.calls == ["candidate.onnx"]

    # It asked, with the question of the plan, and said what happens.
    assert screen.saw(f"═ {INSTALL_QUESTION} ")
    assert INSTALL_QUESTION == "Install the candidate?"
    assert screen.saw("It becomes models/wakeword/autobot.onnx at threshold 0.6.")
    assert screen.saw("The model there now is kept as autobot.prev.onnx.")

    # Installed: the config lines, the files, and the comments that survive.
    entry = phrase(harness)
    assert (entry["kind"], entry["model"], entry["threshold"]) == (
        "openwakeword",
        "models/wakeword/autobot.onnx",
        0.6,
    )
    assert comments(harness.config_text()) == comments(CONFIG_TEXT)
    assert model_bytes(tmp_path, "autobot.onnx") == "candidate"
    assert model_bytes(tmp_path, "autobot.prev.onnx") == "leftover"
    assert not (tmp_path / MODELS / "candidate.onnx").exists()
    assert not (tmp_path / MODELS / "candidate.json").exists()
    assert screen.saw("Installed the candidate as models/wakeword/autobot.onnx at")
    assert screen.saw("Previous   models/wakeword/autobot.prev.onnx: Revert goes back")
    # The listener was asked about and is down: no restart was offered.
    assert harness.runner.polls[-1] == [*CONTROL, "status", "--sample", "0.1"]
    assert harness.runner.calls == []
    assert not screen.saw("Restart the listener?")
    assert "LISTENER DOWN" in screen.frames[-1]


def test_a_candidate_at_least_as_good_is_installed_beside_two_columns(
    tmp_path: Path,
) -> None:
    place_model(tmp_path, "autobot.onnx", "installed", 0.85)
    place_candidate(tmp_path, 0.6)
    # One fewer detected is within the tolerance: at least as good.
    scorer = Scorer(LARGE, candidate=22, installed=23)
    harness = make_harness(
        tmp_path, [], hostname="nuc", score_real=scorer, config_text=OPENWAKEWORD
    )
    harness.screen.keys = [CANDIDATE, -1, "1", "y", -1, "y", -1, ESC, -1, "q"]
    harness.run()
    screen = harness.screen
    frame = candidate_frame(harness, "positives detected")
    assert "the candidate is at least as good as the installed model" in frame
    assert "   candidate     installed" in frame
    assert "positives detected              22 of 25      23 of 25" in frame
    assert "threshold (recommended)              0.6          0.85" in frame
    assert "false accepts per hour              0.00          0.00" in frame
    assert "► 1  Install the candidate" in frame
    assert sorted(scorer.calls) == ["autobot.onnx", "candidate.onnx"]

    entry = phrase(harness)
    assert (entry["kind"], entry["threshold"]) == ("openwakeword", 0.6)
    assert model_bytes(tmp_path, "autobot.onnx") == "candidate"
    assert model_bytes(tmp_path, "autobot.prev.onnx") == "installed"
    # The previous model keeps its evaluation, for the revert's threshold.
    previous = json.loads((tmp_path / MODELS / "autobot.prev.json").read_text())
    assert previous["recommended_threshold"] == 0.85
    # A running listener was restarted, after asking.
    assert screen.saw("═ Restart the listener? ")
    assert harness.runner.calls == [[*CONTROL, "down"], [*CONTROL, "up"]]
    assert screen.saw("The listener was restarted and runs the model.")
    assert "INSTALLED" in screen.frames[-1]


def test_a_worse_candidate_is_not_offered(tmp_path: Path) -> None:
    place_model(tmp_path, "autobot.onnx", "installed", 0.85)
    place_candidate(tmp_path, 0.6)
    scorer = Scorer(LARGE, candidate=20, installed=23)
    harness = make_harness(
        tmp_path, [], hostname="nuc", score_real=scorer, config_text=OPENWAKEWORD
    )
    # The only entry is Score again; Enter and 1 install nothing.
    harness.screen.keys = [CANDIDATE, -1, ENTER, -1, ESC, "q"]
    harness.run()
    screen = harness.screen
    frame = candidate_frame(harness, "positives detected")
    assert (
        "the candidate is worse than the installed model: it detects 3 fewer" in frame
    )
    assert "Install" not in frame.replace("Installed", "")
    assert "► 1  Score again" in frame
    assert not screen.saw(INSTALL_QUESTION)
    assert harness.config_text() == OPENWAKEWORD
    assert model_bytes(tmp_path, "autobot.onnx") == "installed"
    assert model_bytes(tmp_path, "candidate.onnx") == "candidate"
    # Score again scored both afresh.
    assert sorted(scorer.calls) == ["autobot.onnx"] * 2 + ["candidate.onnx"] * 2


def test_below_the_minimum_it_warns_and_installs_only_after_confirmation(
    tmp_path: Path,
) -> None:
    place_model(tmp_path, "autobot.onnx", "installed", 0.85)
    place_candidate(tmp_path, 0.6)
    scorer = Scorer(SMALL, candidate=4, installed=10)
    harness = make_harness(
        tmp_path, [], hostname="nuc", score_real=scorer, config_text=OPENWAKEWORD
    )
    harness.runner.status = NOT_RUNNING
    unconfirmed: list[str] = []
    harness.screen.keys = [
        CANDIDATE,
        -1,
        # Esc at the question: nothing is installed.
        "1",
        -1,
        ESC,
        lambda: unconfirmed.append(harness.config_text()),
        "1",
        "y",
        -1,
        ESC,
        "q",
    ]
    harness.run()
    screen = harness.screen
    frame = candidate_frame(harness, "positives detected")
    # The figures, no verdict, the warning, and the offer on his own judgment.
    assert TOO_SMALL == "the held-out set is too small to tell the models apart"
    assert TOO_SMALL in frame
    assert "positives detected               4 of 11      10 of 11" in frame
    assert "► 1  Install on my own judgment" in frame
    assert "Install the candidate" not in frame
    assert "at least as good" not in frame and "is worse" not in frame
    # The question shows the warning first.
    assert screen.saw(f"Warning: {TOO_SMALL}.")
    assert screen.saw("There is no verdict: you decide.")
    assert screen.saw("Nothing was installed.")
    assert unconfirmed == [OPENWAKEWORD]
    assert phrase(harness)["threshold"] == 0.6
    assert model_bytes(tmp_path, "autobot.onnx") == "candidate"
    assert model_bytes(tmp_path, "autobot.prev.onnx") == "installed"


def test_revert_restores_the_previous_file_and_threshold(tmp_path: Path) -> None:
    place_model(tmp_path, "autobot.onnx", "installed", 0.85)
    place_candidate(tmp_path, 0.6)
    scorer = Scorer(LARGE, candidate=24, installed=23)
    harness = make_harness(
        tmp_path, [], hostname="nuc", score_real=scorer, config_text=OPENWAKEWORD
    )
    harness.runner.status = NOT_RUNNING
    installed: list[object] = []
    harness.screen.keys = [
        CANDIDATE,
        "1",
        "y",
        lambda: installed.append(phrase(harness)["threshold"]),
        # With the candidate installed the first entry is the revert.
        -1,
        "1",
        -1,
        "y",
        -1,
        ESC,
        "q",
    ]
    harness.run()
    screen = harness.screen
    assert installed == [0.6]
    assert screen.saw("► 1  Revert to the previous model")
    assert screen.saw(f"═ {REVERT_QUESTION} ")
    assert REVERT_QUESTION == "Revert to the previous model?"
    entry = phrase(harness)
    assert (entry["kind"], entry["model"], entry["threshold"]) == (
        "openwakeword",
        "models/wakeword/autobot.onnx",
        0.85,
    )
    assert model_bytes(tmp_path, "autobot.onnx") == "installed"
    assert model_bytes(tmp_path, "autobot.prev.onnx") == "candidate"
    assert screen.saw("Reverted to the previous model as models/wakeword/autobot.onnx")
    assert comments(harness.config_text()) == comments(OPENWAKEWORD)


def test_off_the_nuc_nothing_is_installed_or_reverted(tmp_path: Path) -> None:
    place_model(tmp_path, "autobot.onnx", "installed", 0.85)
    place_model(tmp_path, "autobot.prev.onnx", "older", 0.7)
    place_candidate(tmp_path, 0.6)
    scorer = Scorer(LARGE, candidate=24, installed=23)
    harness = make_harness(
        tmp_path,
        [CANDIDATE, "1", -1, "2", -1, ESC, "q"],
        score_real=scorer,
        config_text=OPENWAKEWORD,
    )
    harness.run()
    screen = harness.screen
    assert screen.saw("installing a model runs on the NUC only; this host is testbox")
    assert not screen.saw(INSTALL_QUESTION)
    assert not screen.saw(REVERT_QUESTION)
    assert harness.config_text() == OPENWAKEWORD
    assert model_bytes(tmp_path, "autobot.onnx") == "installed"
    assert model_bytes(tmp_path, "autobot.prev.onnx") == "older"


def test_with_no_real_recordings_it_shows_the_synthetic_figures_with_a_note(
    tmp_path: Path,
) -> None:
    place_candidate(tmp_path, 0.8)
    # The harness's own scorer: nothing is held out.
    harness = make_harness(tmp_path, [CANDIDATE, -1, ESC, "q"], hostname="nuc")
    harness.run()
    frame = candidate_frame(harness, "synthetic")
    assert "no real recordings are held out" in frame
    assert "synthetic, at threshold 0.8: recall 0.664" in frame
    assert "false accepts: hard negatives 0.022 seen, 0.005 unseen" in frame
    assert "validation stream: 0.84 false accepts per hour" in frame
    # Nothing is installed, so the candidate is still offered.
    assert "► 1  Install the candidate" in frame


def test_a_scoring_that_fails_is_one_line(tmp_path: Path) -> None:
    def broken(model: Path, dataset: VoiceDataset, phrase: str) -> RealScores:
        raise EvaluateError(f"{model.name} is not a usable ONNX model")

    place_model(tmp_path, "autobot.onnx", "installed", 0.85)
    place_candidate(tmp_path)
    harness = make_harness(
        tmp_path,
        [CANDIDATE, -1, ESC, "q"],
        hostname="nuc",
        score_real=broken,
        config_text=OPENWAKEWORD,
    )
    harness.run()
    screen = harness.screen
    assert screen.saw("not scored: candidate.onnx is not a usable ONNX model")
    # Without figures there is nothing to judge an installed model against.
    assert not screen.saw("Install the candidate")
    assert screen.saw("► 1  Score again")


def test_an_install_that_cannot_be_done_is_one_line_and_changes_nothing(
    tmp_path: Path,
) -> None:
    # A candidate with no evaluation has no threshold to be installed with.
    place_model(tmp_path, "candidate.onnx", "candidate", None)
    scorer = Scorer(SMALL, candidate=10)
    harness = make_harness(
        tmp_path, [CANDIDATE, "1", -1, ESC, "q"], hostname="nuc", score_real=scorer
    )
    harness.run()
    assert harness.screen.saw("the candidate has no evaluation:")
    assert not harness.screen.saw(INSTALL_QUESTION)
    assert harness.config_text() == CONFIG_TEXT
    assert model_bytes(tmp_path, "candidate.onnx") == "candidate"


def test_the_scores_are_kept_for_the_visit(tmp_path: Path) -> None:
    place_model(tmp_path, "autobot.onnx", "installed", 0.85)
    place_candidate(tmp_path)
    scorer = Scorer(LARGE, candidate=20, installed=23)
    harness = make_harness(
        tmp_path,
        [CANDIDATE, -1, -1, -1, ESC, "q"],
        hostname="nuc",
        score_real=scorer,
        config_text=OPENWAKEWORD,
    )
    harness.run()
    # Scored once on entry, not once a frame.
    assert sorted(scorer.calls) == ["autobot.onnx", "candidate.onnx"]


def test_the_wake_phrase_screen_says_whether_a_candidate_is_ready(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc")
    harness.screen.keys = [
        WAKE,
        -1,
        training_leaves_a_candidate(tmp_path),
        -1,
        ESC,
        "q",
    ]
    harness.run()
    assert harness.screen.saw("Candidate        none")
    assert harness.screen.saw("Candidate        ready: see Candidate, main menu")


@pytest.mark.parametrize("theme", [None, "light", "dark", "sun"])
@pytest.mark.parametrize(("rows", "cols"), [(MIN_HEIGHT, MIN_WIDTH), (40, 120)])
def test_the_candidate_screen_draws_two_columns_at_every_size(
    tmp_path: Path, theme: str | None, rows: int, cols: int
) -> None:
    place_model(tmp_path, "autobot.onnx", "installed", 0.85)
    place_model(tmp_path, "autobot.prev.onnx", "older", 0.7)
    place_candidate(tmp_path)
    scorer = Scorer(SMALL, candidate=10, installed=9)
    harness = make_harness(
        tmp_path,
        [
            CANDIDATE,
            -1,
            curses.KEY_NPAGE,
            -1,
            curses.KEY_PPAGE,
            "1",
            -1,
            ESC,
            "T",
            ESC,
            "q",
        ],
        hostname="nuc",
        score_real=scorer,
        config_text=OPENWAKEWORD,
        rows=rows,
        cols=cols,
        theme=theme,
    )
    assert harness.run() == 0
    screen = harness.screen
    assert not screen.saw("Terminal too small")
    assert screen.saw("positives detected              10 of 11       9 of 11")
    assert screen.saw("Install on my own judgment")
    assert screen.saw("Revert to the previous model")
    assert screen.saw(f"Warning: {TOO_SMALL}.")
    if rows == MIN_HEIGHT:
        # The figures do not all fit: PgDn shows the rest.
        assert screen.saw(" PgDn: more ")
        assert screen.saw(" PgUp: back ")
        assert screen.saw("false accepts in 1.0 min")
    assert all(
        len(row) <= cols for frame in screen.frames for row in frame.splitlines()
    )
    if theme is not None:
        # T switched the theme from the screen; nothing else was written.
        assert harness.config_text() != OPENWAKEWORD
        assert phrase(harness)["threshold"] == 0.5


# --- the candidate screen: the baseline column and the installed threshold -----------


def baseline_scores(size: HeldoutSize, detected: int) -> KindScores:
    return KindScores(
        kind="sherpa_kws",
        phrase="autobot",
        threshold=0.5,
        heldout_positives=size.positives,
        heldout_negatives=size.negatives,
        heldout_ambient_minutes=size.ambient_minutes,
        detected=detected,
        false_accepts=1,
        ambient_false_accepts=2,
    )


@pytest.mark.parametrize(("rows", "cols"), [(24, 80), (MIN_HEIGHT, MIN_WIDTH)])
def test_the_baseline_is_a_third_column_that_decides_nothing(
    tmp_path: Path, rows: int, cols: int
) -> None:
    place_model(tmp_path, "autobot.onnx", "installed", 0.85)
    place_candidate(tmp_path, 0.6)
    scorer = Scorer(LARGE, candidate=22, installed=23)
    asked: list[tuple[str, str]] = []

    def score_kind(kind: str, config: Config, dataset: VoiceDataset) -> KindScores:
        asked.append((kind, config.wake_phrases[0].phrase))
        # Far better than both models: it still decides nothing.
        return baseline_scores(LARGE, 25)

    harness = make_harness(
        tmp_path,
        [CANDIDATE, -1, curses.KEY_NPAGE, -1, curses.KEY_NPAGE, -1, ESC, "q"],
        hostname="nuc",
        score_real=scorer,
        score_kind=score_kind,
        config_text=OPENWAKEWORD,
        rows=rows,
        cols=cols,
    )
    assert harness.run() == 0
    screen = harness.screen
    assert asked == [("sherpa_kws", "autobot")]
    assert screen.saw("   candidate     installed  baseline 0.5")
    assert screen.saw(
        "positives detected              22 of 25      23 of 25      25 of 25"
    )
    assert screen.saw(
        "false accepts in ambient               0             0             2"
    )
    assert screen.saw("baseline: sherpa_kws, untrained, at its default threshold")
    assert screen.saw("the candidate is at least as good as the installed model")
    assert screen.saw("► 1  Install the candidate")
    assert all(
        len(row) <= cols for frame in screen.frames for row in frame.splitlines()
    )


def test_a_baseline_that_cannot_be_scored_is_one_line(tmp_path: Path) -> None:
    place_model(tmp_path, "autobot.onnx", "installed", 0.85)
    place_candidate(tmp_path, 0.6)
    scorer = Scorer(LARGE, candidate=22, installed=23)

    def score_kind(kind: str, config: Config, dataset: VoiceDataset) -> KindScores:
        raise EvaluateError(
            "sherpa_kws: its model files are missing ('make fetch-models' fetches them)"
        )

    harness = make_harness(
        tmp_path,
        [CANDIDATE, -1, curses.KEY_NPAGE, -1, ESC, "q"],
        hostname="nuc",
        score_real=scorer,
        score_kind=score_kind,
        config_text=OPENWAKEWORD,
    )
    assert harness.run() == 0
    screen = harness.screen
    assert screen.saw(
        "baseline (sherpa_kws) not scored: sherpa_kws: its model files are"
    )
    # The models' figures and the offer are as they would be without it.
    assert screen.saw("   candidate     installed")
    assert not screen.saw("baseline 0.5")
    assert screen.saw("positives detected              22 of 25      23 of 25")
    assert screen.saw("► 1  Install the candidate")


def test_the_install_writes_the_threshold_the_screen_shows(tmp_path: Path) -> None:
    # The training recommended 0.5 on the held-out set as it was; recordings
    # were added since, and scored now the candidate is recommended at 0.6.
    place_model(tmp_path, "autobot.onnx", "installed", 0.85)
    place_candidate(tmp_path, 0.5)
    scorer = Scorer(LARGE, candidate=22, installed=23)
    harness = make_harness(
        tmp_path, [], hostname="nuc", score_real=scorer, config_text=OPENWAKEWORD
    )
    harness.runner.status = NOT_RUNNING
    harness.screen.keys = [CANDIDATE, -1, "1", -1, "y", -1, "1", -1, "y", -1, ESC, "q"]
    assert harness.run() == 0
    screen = harness.screen
    assert screen.saw("threshold (recommended)              0.6          0.85")
    # The dialog names the value it will write, and says where it is from.
    assert screen.saw("It becomes models/wakeword/autobot.onnx at threshold 0.6.")
    assert screen.saw("(0.6 is recommended on the held-out set as it is")
    assert screen.saw("now; its evaluation file, from the training, says 0.5.)")
    assert screen.saw(
        "Installed the candidate as models/wakeword/autobot.onnx at threshold 0.6"
    )
    # Then the revert: back to the model that was installed, at its 0.85.
    assert phrase(harness)["threshold"] == 0.85
    assert model_bytes(tmp_path, "autobot.onnx") == "installed"
    # The candidate, kept as the previous model, remembers where it ran.
    kept = json.loads((tmp_path / MODELS / "autobot.prev.json").read_text())
    assert (kept["recommended_threshold"], kept["installed_threshold"]) == (0.5, 0.6)


def test_with_nothing_held_out_the_install_uses_the_evaluation_files_threshold(
    tmp_path: Path,
) -> None:
    place_candidate(tmp_path, 0.75)
    harness = make_harness(tmp_path, [], hostname="nuc")
    harness.runner.status = NOT_RUNNING
    harness.screen.keys = [CANDIDATE, -1, "1", -1, "y", -1, ESC, "q"]
    assert harness.run() == 0
    assert harness.screen.saw("at threshold 0.75.")
    assert harness.screen.saw("(0.75 is from its evaluation file, from the training.)")
    assert phrase(harness)["threshold"] == 0.75
    installed = json.loads((tmp_path / MODELS / "autobot.json").read_text())
    assert "installed_threshold" not in installed


def test_an_evaluation_that_cannot_be_read_is_refused_before_the_question(
    tmp_path: Path,
) -> None:
    place_model(tmp_path, "autobot.onnx", "installed", 0.85)
    candidate = place_candidate(tmp_path, 0.6)
    candidate.with_suffix(".json").write_text("{ this is not json", encoding="utf-8")
    scorer = Scorer(LARGE, candidate=22, installed=23)
    harness = make_harness(
        tmp_path,
        [CANDIDATE, -1, "1", -1, ESC, "q"],
        hostname="nuc",
        score_real=scorer,
        config_text=OPENWAKEWORD,
    )
    assert harness.run() == 0
    screen = harness.screen
    assert screen.saw("candidate.onnx  (evaluation unreadable)")
    assert screen.saw("Not installed: candidate.json cannot be read. Train again.")
    assert not screen.saw(f"═ {INSTALL_QUESTION} ")
    assert model_bytes(tmp_path, "autobot.onnx") == "installed"
    assert phrase(harness)["threshold"] == 0.5


# --- the studio's road, end to end (plan step 12) -----------------------------------

ROAD_SCRIPT = [
    Prompt("bare-01", "autobot", LABEL_POSITIVE, "Say the wake phrase."),
    Prompt("hard-01", "auto bought", LABEL_NEGATIVE, "Say these words."),
    Prompt("ambient-01", "say nothing", LABEL_AMBIENT, "Say nothing.", seconds=0.4),
]


def test_the_road_from_untrained_to_installed_and_back(tmp_path: Path) -> None:
    """TS-2 stays connected: untrained, session, candidate, install, revert."""
    # A fresh config, and a leftover model from an earlier run at the model path.
    place_model(tmp_path, "autobot.onnx", "leftover", 0.85)
    scorer = Scorer(SMALL, candidate=1)
    harness = make_harness(
        tmp_path, [], hostname="nuc", script=ROAD_SCRIPT, score_real=scorer
    )
    harness.runner.status = NOT_RUNNING
    seen: dict[str, tuple[str, dict[str, object], str, list[str]]] = {}

    def feed(blocks: int) -> Key:
        def deliver() -> None:
            for _ in range(blocks):
                harness.audio.feed(3000)

        return deliver

    def note(name: str) -> Key:
        def remember() -> None:
            seen[name] = (
                harness.screen.text(),
                dict(phrase(harness)),
                model_bytes(tmp_path, "autobot.onnx"),
                sorted(path.name for path in (tmp_path / MODELS).iterdir()),
            )

        return remember

    harness.screen.keys = [
        -1,
        note("fresh"),
        # The three-prompt session.
        SESSION,
        *typed("Charles"),
        ENTER,
        ENTER,
        feed(4),
        ENTER,
        "n",
        ENTER,
        feed(4),
        ENTER,
        "n",
        ENTER,
        feed(5),
        -1,
        ESC,
        -1,
        note("recorded"),
        # A training run leaves a candidate.
        training_leaves_a_candidate(tmp_path),
        -1,
        note("trained"),
        # Install it: there is nothing to beat.
        CANDIDATE,
        -1,
        "1",
        "y",
        -1,
        ESC,
        -1,
        note("installed"),
        # And back: revert to the model it replaced.
        CANDIDATE,
        "1",
        "y",
        -1,
        ESC,
        -1,
        note("reverted"),
        "q",
    ]
    assert harness.run() == 0
    screen = harness.screen

    def at(name: str) -> tuple[str, dict[str, object], str]:
        return seen[name][:3]

    def files(name: str) -> list[str]:
        return seen[name][3]

    # Untrained, with the leftover named as kept.
    frame, entry, model = at("fresh")
    assert "State   UNTRAINED" in frame
    assert "models/wakeword/autobot.onnx is kept, not counted as installed." in frame
    assert "Dataset: 0 positives, 0 negatives, 0 min ambient" in frame
    assert (entry["kind"], entry["model"], entry["threshold"]) == (
        "text_match",
        "",
        0.5,
    )

    # The session recorded its three prompts and is complete.
    frame, _, _ = at("recorded")
    assert screen.saw("RECORDING")
    assert "State   UNTRAINED" in frame
    assert "Dataset: 1 positives, 1 negatives, 0 min ambient" in frame
    assert "A session is complete (Charles): Train the model is next" in frame
    assert [line["prompt_id"] for line in harness.manifest()] == [
        "bare-01",
        "hard-01",
        "ambient-01",
    ]

    # The candidate is ready, and nothing is installed yet.
    frame, entry, model = at("trained")
    assert "State   CANDIDATE READY" in frame
    assert "3  Candidate (ready)" in frame
    assert (entry["kind"], model) == ("text_match", "leftover")

    # Installed with nothing to beat: the leftover is not compared, and is kept.
    assert screen.saw("nothing is installed: the candidate has nothing to beat")
    assert screen.saw("Installed  none; autobot.onnx is a leftover: kept, not compared")
    assert scorer.calls == ["candidate.onnx"]
    frame, entry, model = at("installed")
    assert "State   LISTENER DOWN" in frame
    assert (entry["kind"], entry["model"], entry["threshold"]) == (
        "openwakeword",
        "models/wakeword/autobot.onnx",
        0.6,
    )
    assert model == "candidate"
    # The leftover was kept as the previous model, with its evaluation.
    assert files("trained") == [
        "autobot.json",
        "autobot.onnx",
        "candidate.json",
        "candidate.onnx",
    ]
    assert files("installed") == [
        "autobot.json",
        "autobot.onnx",
        "autobot.prev.json",
        "autobot.prev.onnx",
    ]

    # Reverted: the leftover is back in place with its evaluation's threshold.
    frame, entry, model = at("reverted")
    assert model == "leftover"
    assert model_bytes(tmp_path, "autobot.prev.onnx") == "candidate"
    assert (entry["kind"], entry["model"], entry["threshold"]) == (
        "openwakeword",
        "models/wakeword/autobot.onnx",
        0.85,
    )
    restored = json.loads((tmp_path / MODELS / "autobot.json").read_text())
    assert restored["recommended_threshold"] == 0.85
    assert comments(harness.config_text()) == comments(CONFIG_TEXT)
    # With the listener running the state would read *installed*.
    harness.app.listener_running = True
    assert harness.app.state() is State.INSTALLED
