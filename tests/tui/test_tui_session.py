"""The recording session: the model (`autobot.tui.session`) and its screen.

The screen tests drive the real application through a short script of three
prompts and an optional long one shortened to seconds, on the configured NUC
(`hostname="nuc"`), feeding blocks of 80 ms through the fake audio backend.
"""

import curses
import os
import wave
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from tui_fakes import (
    CONFIG_TEXT,
    ENTER,
    ESC,
    F10,
    Harness,
    Key,
    make_harness,
    typed,
)

from autobot.tui.audio import SILENCE_WARNING, Clip
from autobot.tui.screens.session import (
    SILENCE_QUESTION,
    clock_text,
    large_type,
    minutes_words,
)
from autobot.tui.session import (
    CLIPPED_PEAK,
    QUIET_PEAK,
    Session,
    complete_speakers,
    remove_stale_streams,
    stream_path,
    take_warning,
)
from autobot.wakeword.data import phrase_table
from autobot.wakeword.dataset import (
    LABEL_AMBIENT,
    LABEL_NEGATIVE,
    LABEL_POSITIVE,
    ClipRecord,
    VoiceDataset,
)
from autobot.wakeword.script import Prompt, build_script

RATE = 16000
# One block of the fake backend: 80 ms.
BLOCK = 1280
BARE = Prompt("bare-01", "autobot", LABEL_POSITIVE, "Say the wake phrase.")
HARD = Prompt("hard-01", "auto bought", LABEL_NEGATIVE, "Say these words.")
# Five blocks: it counts down and stops by itself.
ROOM = Prompt("ambient-01", "say nothing", LABEL_AMBIENT, "Say nothing.", seconds=0.4)
# The long prompt, shortened: ten blocks, and it can be stopped after five.
LONG = Prompt(
    "ambient-long",
    "leave it running",
    LABEL_AMBIENT,
    "Leave it running (60 min, optional)",
    seconds=0.8,
    min_seconds=0.4,
    optional=True,
)
SCRIPT = [BARE, HARD, ROOM, LONG]
SESSION = "1"


def clip(seconds: float = 0.5, value: int = 3000) -> Clip:
    return Clip(np.full(int(seconds * RATE), value, dtype=np.int16), RATE)


def feed(harness: Harness, blocks: int = 4, value: int = 3000) -> Key:
    """A key that delivers `blocks` blocks, as the audio thread would."""

    def deliver() -> None:
        for _ in range(blocks):
            harness.audio.feed(value)

    return deliver


def start(*keys: Key) -> list[Key]:
    """Open the session as Charles, then `keys`."""
    return [SESSION, *typed("Charles"), ENTER, *keys]


def frames_of(path: Path) -> int:
    with wave.open(str(path), "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (
            1,
            2,
            RATE,
        )
        return wav.getnframes()


def partial_files(harness: Harness) -> list[str]:
    root = harness.dataset.root
    return (
        sorted(path.name for path in root.glob(".recording-*")) if root.exists() else []
    )


def answer_required(dataset: VoiceDataset, speaker: str = "Charles") -> Session:
    """A session whose three required prompts each have a take."""
    session = Session(dataset, "autobot", speaker, SCRIPT)
    for prompt in (BARE, HARD, ROOM):
        session.accept(prompt, clip(), "pipewire")
    return session


# --- the model --------------------------------------------------------------------


def test_a_new_session_starts_at_the_first_prompt(tmp_path: Path) -> None:
    session = Session(VoiceDataset(tmp_path / "voice"), "autobot", " Charles ", SCRIPT)
    assert session.speaker == "Charles"
    assert session.next_prompt() is BARE
    assert session.next_index() == 0
    assert not session.complete()
    progress = session.progress()
    assert (progress.done, progress.total, progress.fraction) == (0, 3, 0.0)
    assert progress.labels == {
        LABEL_POSITIVE: (0, 1),
        LABEL_NEGATIVE: (0, 1),
        LABEL_AMBIENT: (0, 1),
    }
    assert (progress.optional_done, progress.optional_total) == (0, 1)
    # Nothing is written by looking.
    assert not (tmp_path / "voice").exists()


def test_accept_stores_the_clip_with_its_prompt(tmp_path: Path) -> None:
    dataset = VoiceDataset(tmp_path / "voice")
    session = Session(dataset, "autobot", "Charles", SCRIPT)
    stored = session.accept(BARE, clip(), "pipewire")
    assert session.clips_for(BARE) == [stored]
    assert session.answered(BARE) and not session.answered(HARD)
    assert session.next_prompt() is HARD
    (record,) = dataset.records()
    assert record == stored
    assert (record.phrase, record.prompt, record.prompt_id) == (
        "autobot",
        "autobot",
        "bare-01",
    )
    assert (record.label, record.source, record.speaker, record.device) == (
        LABEL_POSITIVE,
        "session",
        "Charles",
        "pipewire",
    )
    assert record.file.startswith("Charles-bare-01-")
    assert dataset.seconds(record) == 0.5


def test_a_session_resumes_from_the_manifest(tmp_path: Path) -> None:
    dataset = VoiceDataset(tmp_path / "voice")
    first = Session(dataset, "autobot", "Charles", SCRIPT)
    first.accept(BARE, clip(), "pipewire")
    first.accept(BARE, clip(), "pipewire")
    # A later run of the interface: a new object, the same manifest.
    later = Session(dataset, "autobot", "Charles", SCRIPT)
    assert later.next_prompt() is HARD
    assert len(later.clips_for(BARE)) == 2
    assert later.progress().done == 1


def test_complete_without_the_optional_prompt(tmp_path: Path) -> None:
    dataset = VoiceDataset(tmp_path / "voice")
    session = answer_required(dataset)
    assert session.complete()
    assert session.progress().complete
    # The optional long prompt is still offered next.
    assert session.next_prompt() is LONG
    session.accept(LONG, clip(), "pipewire")
    assert session.next_prompt() is None
    assert session.progress().optional_done == 1


def test_discard_removes_the_latest_take_only(tmp_path: Path) -> None:
    dataset = VoiceDataset(tmp_path / "voice")
    session = Session(dataset, "autobot", "Charles", SCRIPT)
    first = session.accept(BARE, clip(0.3), "pipewire")
    second = session.accept(BARE, clip(0.6), "pipewire")
    assert session.discard(BARE) == second
    assert session.clips_for(BARE) == [first]
    assert [record.file for record in dataset.records()] == [first.file]
    assert not dataset.path(second).exists()
    assert session.discard(BARE) == first
    assert session.discard(BARE) is None
    assert session.next_prompt() is BARE


def test_another_speaker_has_a_session_of_their_own(tmp_path: Path) -> None:
    dataset = VoiceDataset(tmp_path / "voice")
    answer_required(dataset, "Charles")
    sam = Session(dataset, "autobot", "Sam", SCRIPT)
    assert sam.next_prompt() is BARE
    sam.accept(BARE, clip(), "pipewire")
    assert complete_speakers(dataset, "autobot", SCRIPT) == ["Charles"]
    assert complete_speakers(dataset, "another phrase", SCRIPT) == []


def test_clips_with_no_script_prompt_answer_nothing(tmp_path: Path) -> None:
    dataset = VoiceDataset(tmp_path / "voice")
    dataset.add(
        clip(),
        ClipRecord(
            file="",
            phrase="autobot",
            prompt="audio screen",
            prompt_id="",
            label=LABEL_POSITIVE,
            source="session",
            speaker="Charles",
            device="pipewire",
            recorded_at="2026-11-26T17:40:03-05:00",
        ),
    )
    dataset.add(
        clip(),
        ClipRecord(
            file="",
            phrase="autobot",
            prompt="monitor: missed wake",
            prompt_id="bare-01",
            label=LABEL_POSITIVE,
            source="monitor",
            speaker="Charles",
            device="pipewire",
            recorded_at="2026-11-26T17:40:04-05:00",
        ),
    )
    session = Session(dataset, "autobot", "Charles", SCRIPT)
    assert session.next_prompt() is BARE
    assert complete_speakers(dataset, "autobot", SCRIPT) == []


def test_accept_file_moves_a_streamed_recording_into_place(tmp_path: Path) -> None:
    dataset = VoiceDataset(tmp_path / "voice")
    session = Session(dataset, "autobot", "Charles", SCRIPT)
    source = stream_path(dataset.root)
    assert source.parent == dataset.root
    assert source.name == f".recording-{os.getpid()}.wav.part"
    dataset.root.mkdir(parents=True)
    with wave.open(str(source), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(clip(1.0).samples.tobytes())
    stored = session.accept_file(LONG, source, "pipewire")
    assert not source.exists()
    assert frames_of(dataset.path(stored)) == RATE
    assert stored.prompt_id == "ambient-long"
    assert session.answered(LONG)


def test_the_real_script_is_complete_at_its_ninety_one_required_prompts(
    tmp_path: Path,
) -> None:
    script = build_script("autobot", phrase_table("autobot"))
    session = Session(VoiceDataset(tmp_path / "voice"), "autobot", "Charles", script)
    progress = session.progress()
    assert (progress.total, progress.optional_total) == (91, 1)
    assert progress.labels == {
        LABEL_POSITIVE: (0, 56),
        LABEL_NEGATIVE: (0, 30),
        LABEL_AMBIENT: (0, 5),
    }


def test_stale_partial_recordings_are_removed_and_live_ones_left(
    tmp_path: Path,
) -> None:
    root = tmp_path / "voice"
    root.mkdir()
    # Nobody has this pid: the interface that wrote the file is gone.
    dead = stream_path(root, 4_000_000_000)
    own = stream_path(root)
    # pid 1 is always alive: another interface is still recording.
    live = stream_path(root, 1)
    clip_file = root / "Charles-bare-01-20261126-174003.wav"
    for path in (dead, own, live, clip_file):
        path.write_bytes(b"RIFF")
    assert sorted(remove_stale_streams(root)) == sorted([dead, own])
    assert sorted(path.name for path in root.iterdir()) == sorted(
        [live.name, clip_file.name]
    )
    # A directory that is not there is not an error.
    assert remove_stale_streams(tmp_path / "missing") == []
    # The dataset never counted any of them: no manifest line names them.
    assert VoiceDataset(root).records() == []


def test_take_warning() -> None:
    assert take_warning(8000, LABEL_POSITIVE) == ""
    assert take_warning(0, LABEL_POSITIVE).startswith("digital silence")
    assert take_warning(1, LABEL_AMBIENT).startswith("digital silence")
    assert take_warning(QUIET_PEAK - 1, LABEL_POSITIVE).startswith(
        "very quiet (peak -40"
    )
    assert take_warning(QUIET_PEAK, LABEL_POSITIVE) == ""
    # A quiet room is what an ambient prompt asks for.
    assert take_warning(50, LABEL_AMBIENT) == ""
    assert take_warning(CLIPPED_PEAK, LABEL_NEGATIVE).startswith("clipped")
    assert take_warning(32767, LABEL_AMBIENT).startswith("clipped")


def test_large_type_spaces_short_prompts_and_folds_long_ones() -> None:
    assert large_type("autobot", 60, 3) == ["A U T O B O T"]
    assert large_type("hey autobot", 60, 3) == ["H E Y   A U T O B O T"]
    long = "hey autobot, add milk to the shopping list"
    assert large_type(long, 60, 3) == [long]
    folded = large_type(long, 20, 3)
    assert len(folded) == 3 and all(len(line) <= 20 for line in folded)
    assert len(large_type(long, 20, 1)) == 1


def test_clock_and_minutes_words() -> None:
    assert clock_text(0) == "0:00"
    assert clock_text(59.9) == "0:59"
    assert clock_text(1800) == "30:00"
    assert clock_text(-3) == "0:00"
    assert minutes_words(3600) == "60 minutes"
    assert minutes_words(60) == "1 minute"
    assert minutes_words(0.4) == "0.4 seconds"


# --- the screen: a whole short session ------------------------------------------------


def test_a_whole_short_session(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc", script=SCRIPT)
    harness.screen.keys = start(
        # Prompt 1: record, stop, play it back, go on.
        ENTER,
        feed(harness),
        ENTER,
        "p",
        "n",
        # Prompt 2.
        ENTER,
        feed(harness),
        ENTER,
        "n",
        # Prompt 3 is the room: it counts down and stops itself.
        ENTER,
        feed(harness, 2),
        -1,
        feed(harness, 3),
        -1,
        # The optional long prompt is offered, and N skips it.
        "n",
        -1,
        "n",
        ESC,
        -1,
        "q",
    )
    assert harness.run() == 0
    screen = harness.screen

    # The session asked for the name, showed the first prompt large and framed.
    assert screen.saw("Who is speaking? The speaker's name:")
    assert screen.saw("Speaker Charles")
    assert screen.saw("Prompt 1 of 4")
    assert screen.saw("Say the wake phrase.")
    assert screen.saw("╔═ say ═")
    assert screen.saw("A U T O B O T")
    assert screen.saw("positive 0/1   negative 0/1   ambient 0/1   + optional 0/1")
    # The state is *recording* while the session is open.
    assert screen.saw("RECORDING")
    assert screen.saw("● REC")
    assert screen.saw("Take saved (0.3 s, peak -20.8 dBFS). N next, P play, R again.")
    assert screen.saw("Playing the latest take (0.3 s).")
    assert screen.saw("Takes  1")
    # The room prompt counted down.
    assert screen.saw("╔═ say nothing ═")
    assert screen.saw("left of 0:00   say nothing")
    assert screen.saw(
        "The session is complete. N: the optional long prompt; Esc: leave."
    )
    # The long prompt was offered.
    assert screen.saw("Leave it running (60 min, optional)")
    assert screen.saw("╔═ optional ═")
    assert screen.saw("L E A V E   I T   R U N N I N G")
    assert screen.saw("That was the last prompt; the session is complete.")

    # One clip per required prompt, labelled by its prompt, and no more.
    manifest = harness.manifest()
    assert [line["prompt_id"] for line in manifest] == [
        "bare-01",
        "hard-01",
        "ambient-01",
    ]
    assert [line["label"] for line in manifest] == [
        LABEL_POSITIVE,
        LABEL_NEGATIVE,
        LABEL_AMBIENT,
    ]
    assert [line["prompt"] for line in manifest] == [
        "autobot",
        "auto bought",
        "say nothing",
    ]
    for line in manifest:
        assert (line["speaker"], line["source"], line["device"], line["phrase"]) == (
            "Charles",
            "session",
            "pipewire",
            "autobot",
        )
    root = harness.dataset.root
    assert [frames_of(root / line["file"]) for line in manifest] == [
        4 * BLOCK,
        4 * BLOCK,
        5 * BLOCK,
    ]
    assert sorted(path.name for path in root.iterdir()) == sorted(
        [*(line["file"] for line in manifest), "manifest.jsonl"]
    )

    # The session is complete without the optional prompt.
    session = Session(harness.dataset, "autobot", "Charles", SCRIPT)
    assert session.complete()
    assert session.next_prompt() is LONG

    # One input stream served the meter and every take, and it is closed.
    assert harness.audio.opened == [3]
    assert all(stream.closed for stream in harness.audio.streams)
    played, index = harness.audio.played[0]
    assert (len(played.samples), index) == (4 * BLOCK, 3)
    # Back on the main menu the state is no longer *recording*.
    last = screen.frames[-1]
    assert "UNTRAINED" in last and "RECORDING" not in last
    assert "Dataset: 1 positives, 1 negatives, 0 min ambient" in last
    assert "A session is complete (Charles): Train the model is next" in last
    assert harness.config_text() == CONFIG_TEXT
    assert not harness.fixture_dir.exists()


def test_off_the_nuc_the_session_refuses_in_one_line(tmp_path: Path) -> None:
    harness = make_harness(
        tmp_path, [SESSION, -1, "q"], hostname="testbox", script=SCRIPT
    )
    harness.run()
    screen = harness.screen
    assert screen.saw(
        "the recording session runs on the NUC only; this host is testbox"
    )
    assert not screen.saw("Who is speaking?")
    assert not screen.saw("═ Recording session ")
    assert not screen.saw("RECORDING")
    # Nothing was opened and nothing was recorded.
    assert harness.audio.streams == []
    assert not harness.dataset.root.exists()


def test_an_unset_nuc_hostname_refuses_too(tmp_path: Path) -> None:
    text = CONFIG_TEXT.replace('nuc_hostname = "nuc"', 'nuc_hostname = ""')
    harness = make_harness(
        tmp_path, [SESSION, -1, "q"], hostname="nuc", script=SCRIPT, config_text=text
    )
    harness.run()
    assert harness.screen.saw(
        "the recording session runs on the NUC only; this host is nuc"
    )
    assert harness.audio.streams == []


def test_the_hostname_is_compared_without_case(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, start(ESC, "q"), hostname="NUC", script=SCRIPT)
    harness.run()
    assert harness.screen.saw("═ Recording session ")


def test_cancelling_the_name_opens_nothing(tmp_path: Path) -> None:
    harness = make_harness(
        tmp_path, [SESSION, *typed("Ch"), ESC, "q"], hostname="nuc", script=SCRIPT
    )
    harness.run()
    assert harness.screen.saw("Ch_")
    assert not harness.screen.saw("Prompt 1 of 4")
    assert harness.audio.streams == []


def test_a_name_with_nothing_usable_is_refused(tmp_path: Path) -> None:
    keys: list[Key] = [SESSION, *typed("..."), ENTER, ESC, "q"]
    harness = make_harness(tmp_path, keys, hostname="nuc", script=SCRIPT)
    harness.run()
    assert harness.screen.saw("the name needs at least one letter or digit")
    assert not harness.screen.saw("Prompt 1 of 4")


def test_the_session_resumes_at_the_first_unanswered_prompt(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc", script=SCRIPT)
    harness.screen.keys = [
        *start(ENTER, feed(harness), ENTER, ESC),
        # Reopen: the last speaker's name is offered, Enter accepts it.
        SESSION,
        ENTER,
        -1,
        ESC,
        "q",
    ]
    harness.run()
    screen = harness.screen
    assert screen.saw("Charles_")
    assert screen.saw("Welcome back, Charles: resuming at prompt 2 of 4.")
    assert screen.saw("Prompt 2 of 4")
    assert screen.saw("A U T O   B O U G H T")
    assert [line["prompt_id"] for line in harness.manifest()] == ["bare-01"]
    # Each visit opened its own stream and closed it.
    assert len(harness.audio.streams) == 2
    assert all(stream.closed for stream in harness.audio.streams)


def test_b_goes_back_and_shows_the_takes_of_that_prompt(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc", script=SCRIPT)
    seen: list[str] = []
    harness.screen.keys = start(
        ENTER,
        feed(harness),
        ENTER,
        "n",
        -1,
        "b",
        -1,
        lambda: seen.append(harness.screen.frames[-1]),
        "b",
        ESC,
        "q",
    )
    harness.run()
    (frame,) = seen
    assert "Prompt 1 of 4" in frame
    assert "Ready. 1 take of this prompt." in frame
    assert "  1.   0.3 s  peak -20.8 dBFS" in frame


def test_r_replaces_the_latest_take_and_d_discards_it(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc", script=SCRIPT)
    kept: list[list[int]] = []

    def takes() -> None:
        root = harness.dataset.root
        kept.append([frames_of(root / line["file"]) for line in harness.manifest()])

    harness.screen.keys = start(
        ENTER,
        feed(harness, 4),
        ENTER,
        takes,
        # Record it again: six blocks this time.
        "r",
        feed(harness, 6),
        ENTER,
        takes,
        "d",
        takes,
        # Nothing left to discard or to play.
        "d",
        -1,
        "p",
        -1,
        ESC,
        "q",
    )
    harness.run()
    # One take at a time: the second replaced the first, then it was discarded.
    assert kept == [[4 * BLOCK], [6 * BLOCK], []]
    assert harness.screen.saw("Discarded the take Charles-bare-01-")
    assert harness.screen.saw("No take to discard.")
    assert harness.screen.saw("No take to play yet: Enter records one.")
    assert sorted(path.name for path in harness.dataset.root.iterdir()) == [
        "manifest.jsonl"
    ]


def test_enter_again_adds_a_second_take_of_the_same_prompt(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc", script=SCRIPT)
    harness.screen.keys = start(
        ENTER, feed(harness), ENTER, ENTER, feed(harness, 5), ENTER, -1, ESC, "q"
    )
    harness.run()
    assert [line["prompt_id"] for line in harness.manifest()] == ["bare-01", "bare-01"]
    assert harness.screen.saw("Ready. 2 takes of this prompt.")
    assert harness.screen.saw("Takes  2")


@pytest.mark.parametrize(
    ("value", "warning"),
    [
        (100, "Saved, but very quiet (peak -50 dBFS): move closer. R redoes it."),
        (0, "Saved, but digital silence: is the microphone muted? R redoes it."),
        (32767, "Saved, but clipped: move back or lower the input level. R redoes it."),
    ],
)
def test_a_take_at_a_wrong_level_is_flagged_not_refused(
    tmp_path: Path, value: int, warning: str
) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc", script=SCRIPT)
    harness.screen.keys = start(ENTER, feed(harness, 4, value), ENTER, -1, ESC, "q")
    harness.run()
    assert harness.screen.saw(warning)
    # It is kept all the same, and marked in the list of takes.
    assert len(harness.manifest()) == 1
    assert harness.screen.saw(warning[len("Saved, but ") :].split(":")[0])


def test_a_take_with_nothing_in_it_is_not_stored(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc", script=SCRIPT)
    harness.screen.keys = start(ENTER, feed(harness, 1), ENTER, -1, ESC, "q")
    harness.run()
    assert harness.screen.saw("Nothing was recorded (0.1 s).")
    assert harness.manifest() == []


def test_a_spoken_take_stops_by_itself_at_its_limit(tmp_path: Path) -> None:
    short = Prompt("bare-01", "autobot", LABEL_POSITIVE, "Say it.", seconds=0.4)
    harness = make_harness(tmp_path, [], hostname="nuc", script=[short, HARD])
    harness.screen.keys = start(ENTER, feed(harness, 9), -1, ESC, "q")
    harness.run()
    (line,) = harness.manifest()
    assert frames_of(harness.dataset.root / line["file"]) == 5 * BLOCK


# --- leaving in the middle of a recording ------------------------------------------------


def test_esc_during_a_recording_stops_and_keeps_the_take(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc", script=SCRIPT)
    after: list[str] = []
    harness.screen.keys = start(
        ENTER,
        feed(harness, 5),
        ESC,
        -1,
        lambda: after.append(harness.screen.frames[-1]),
        # The first Esc only stopped the recording; this one leaves.
        ESC,
        "q",
    )
    harness.run()
    (line,) = harness.manifest()
    assert frames_of(harness.dataset.root / line["file"]) == 5 * BLOCK
    assert "═ Recording session " in after[0]
    assert "Take saved (0.4 s" in after[0]
    assert all(stream.closed for stream in harness.audio.streams)


def test_f10_during_a_recording_saves_the_take_and_closes_the_stream(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc", script=SCRIPT)
    harness.screen.keys = start(ENTER, feed(harness, 5), F10)
    assert harness.run() == 0
    (line,) = harness.manifest()
    assert line["prompt_id"] == "bare-01"
    assert frames_of(harness.dataset.root / line["file"]) == 5 * BLOCK
    assert all(stream.closed for stream in harness.audio.streams)
    assert harness.app.session_open is False


def test_other_keys_do_nothing_while_recording(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc", script=SCRIPT)
    harness.screen.keys = start(
        ENTER, feed(harness, 2), "n", "q", "d", "b", feed(harness, 2), ENTER, ESC, "q"
    )
    harness.run()
    assert harness.screen.saw("Recording: Enter stops it.")
    (line,) = harness.manifest()
    # Still on the first prompt, with every block of the take.
    assert line["prompt_id"] == "bare-01"
    assert frames_of(harness.dataset.root / line["file"]) == 4 * BLOCK


def test_resizing_during_a_recording_loses_nothing(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc", script=SCRIPT)
    screen = harness.screen
    screen.keys = start(
        ENTER,
        feed(harness, 2),
        # Too small to draw: the recording carries on.
        lambda: screen.resize(10, 30),
        curses.KEY_RESIZE,
        feed(harness, 2),
        ENTER,  # ignored while the terminal is too small
        lambda: screen.resize(40, 120),
        curses.KEY_RESIZE,
        feed(harness, 2),
        ENTER,
        ESC,
        "q",
    )
    assert harness.run() == 0
    assert screen.saw("Need 72x22, have 30x10.")
    (line,) = harness.manifest()
    assert frames_of(harness.dataset.root / line["file"]) == 6 * BLOCK


def test_a_take_that_ends_while_the_terminal_is_too_small_is_stored(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc", script=[ROOM, BARE])
    screen = harness.screen
    screen.keys = start(
        ENTER,
        lambda: screen.resize(10, 30),
        curses.KEY_RESIZE,
        feed(harness, 5),
        -1,
        F10,
    )
    harness.run()
    (line,) = harness.manifest()
    assert frames_of(harness.dataset.root / line["file"]) == 5 * BLOCK


# --- the hands-off prompts -----------------------------------------------------------------


def to_the_room(harness: Harness, *keys: Key) -> list[Key]:
    """Open the session at the room prompt (the first two already have takes)."""
    session = Session(harness.dataset, "autobot", "Charles", SCRIPT)
    session.accept(BARE, clip(), "pipewire")
    session.accept(HARD, clip(), "pipewire")
    return [SESSION, ENTER, *keys]


def test_an_ambient_prompt_cannot_be_stopped_early_and_esc_cancels_it(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc", script=SCRIPT)
    after: list[str] = []
    harness.screen.keys = to_the_room(
        harness,
        ENTER,
        feed(harness, 2),
        # Enter does not stop a countdown.
        ENTER,
        -1,
        ESC,
        -1,
        lambda: after.append(harness.screen.frames[-1]),
        ESC,
        "q",
    )
    harness.run()
    screen = harness.screen
    assert screen.saw(
        "Recording the room for 0.4 seconds: say nothing. It stops by itself."
    )
    assert screen.saw("It stops by itself in 0:00. Esc cancels it.")
    assert screen.saw("Cancelled: nothing of that recording was kept.")
    # Esc cancelled the recording and stayed on the prompt.
    assert "Prompt 3 of 4" in after[0]
    assert [line["prompt_id"] for line in harness.manifest()] == ["bare-01", "hard-01"]


def test_leaving_during_an_ambient_countdown_keeps_nothing_of_it(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc", script=SCRIPT)
    harness.screen.keys = to_the_room(harness, ENTER, feed(harness, 2), F10)
    harness.run()
    assert [line["prompt_id"] for line in harness.manifest()] == ["bare-01", "hard-01"]
    assert all(stream.closed for stream in harness.audio.streams)


def to_the_long_prompt(harness: Harness, *keys: Key) -> list[Key]:
    answer_required(harness.dataset)
    return [SESSION, ENTER, *keys]


def note(harness: Harness, into: list[list[str]]) -> Callable[[], None]:
    return lambda: into.append(partial_files(harness))


def test_the_long_prompt_streams_to_disk_and_stops_only_after_its_minimum(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc", script=SCRIPT)
    partial: list[list[str]] = []
    harness.screen.keys = to_the_long_prompt(
        harness,
        # Away and back: the hint of the optional prompt shows.
        "b",
        "n",
        -1,
        ENTER,
        feed(harness, 3),
        note(harness, partial),
        # Too early: Enter says when it can be stopped.
        ENTER,
        -1,
        feed(harness, 3),
        -1,
        # Past the minimum: Enter stops it and keeps the take.
        ENTER,
        note(harness, partial),
        ESC,
        "q",
    )
    harness.run()
    screen = harness.screen
    assert screen.saw(
        "The session is complete. This last prompt is optional: N skips it."
    )
    assert screen.saw(
        "Optional: Enter starts 0.8 seconds of room audio (0:00 at least); N skips."
    )
    assert screen.saw(
        "Recording the room: up to 0.8 seconds; Enter stops it after 0.4 seconds."
    )
    assert screen.saw("It can be stopped and kept in 0:00. Esc cancels it.")
    assert screen.saw("Enter can stop it in")
    assert screen.saw("Enter stops it and keeps the take")
    # While it ran, the audio was in a temporary file under the dataset directory.
    assert partial == [[f".recording-{os.getpid()}.wav.part"], []]
    line = harness.manifest()[-1]
    assert (line["prompt_id"], line["label"]) == ("ambient-long", LABEL_AMBIENT)
    assert frames_of(harness.dataset.root / line["file"]) == 6 * BLOCK
    assert Session(harness.dataset, "autobot", "Charles", SCRIPT).next_prompt() is None


def test_the_long_prompt_ends_by_itself_at_its_full_length(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc", script=SCRIPT)
    harness.screen.keys = to_the_long_prompt(
        harness, ENTER, feed(harness, 12), -1, -1, ESC, "q"
    )
    harness.run()
    line = harness.manifest()[-1]
    assert line["prompt_id"] == "ambient-long"
    assert frames_of(harness.dataset.root / line["file"]) == 10 * BLOCK
    assert partial_files(harness) == []
    assert harness.screen.saw("Take saved (0.8 s, peak -20.8 dBFS).")


def test_cancelling_the_long_prompt_before_its_minimum_asks_and_keeps_nothing(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc", script=SCRIPT)
    partial: list[list[str]] = []
    harness.screen.keys = to_the_long_prompt(
        harness,
        ENTER,
        feed(harness, 2),
        # Esc asks; "no" carries on recording.
        ESC,
        "n",
        feed(harness, 1),
        note(harness, partial),
        ESC,
        "y",
        note(harness, partial),
        ESC,
        "q",
    )
    harness.run()
    screen = harness.screen
    assert screen.saw("═ Cancel the long recording? ")
    assert screen.saw("Under 0.4 seconds nothing is kept.")
    assert screen.saw("Cancelled: nothing of that recording was kept.")
    assert partial == [[f".recording-{os.getpid()}.wav.part"], []]
    assert [line["prompt_id"] for line in harness.manifest()] == [
        "bare-01",
        "hard-01",
        "ambient-01",
    ]


def test_leaving_the_long_prompt_keeps_it_only_past_its_minimum(tmp_path: Path) -> None:
    for name in ("early", "late"):
        (tmp_path / name).mkdir()
    early = make_harness(tmp_path / "early", [], hostname="nuc", script=SCRIPT)
    early.screen.keys = to_the_long_prompt(early, ENTER, feed(early, 3), F10)
    early.run()
    assert len(early.manifest()) == 3
    assert partial_files(early) == []

    late = make_harness(tmp_path / "late", [], hostname="nuc", script=SCRIPT)
    late.screen.keys = to_the_long_prompt(late, ENTER, feed(late, 7), F10)
    late.run()
    line = late.manifest()[-1]
    assert line["prompt_id"] == "ambient-long"
    assert frames_of(late.dataset.root / line["file"]) == 7 * BLOCK
    assert partial_files(late) == []


def test_a_partial_file_left_by_an_interrupted_recording_is_cleaned_up(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc", script=SCRIPT)
    session = answer_required(harness.dataset)
    # The interface was killed an hour into the long prompt, a while ago.
    stale = stream_path(harness.dataset.root, 4_000_000_000)
    stale.write_bytes(b"RIFF....WAVE")
    assert session.next_prompt() is LONG
    harness.screen.keys = [SESSION, ENTER, -1, ESC, "q"]
    harness.run()
    assert not stale.exists()
    # It never was a clip: the session still offers the long prompt.
    assert harness.screen.saw("Prompt 4 of 4")
    assert len(harness.manifest()) == 3


def test_a_long_take_is_not_discarded_without_asking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("autobot.tui.screens.session.CONFIRM_DISCARD_SECONDS", 0.3)
    harness = make_harness(tmp_path, [], hostname="nuc", script=SCRIPT)
    kept: list[int] = []
    harness.screen.keys = start(
        ENTER,
        feed(harness, 5),
        ENTER,
        # "No" keeps the take; "yes" deletes it.
        "d",
        "n",
        lambda: kept.append(len(harness.manifest())),
        "d",
        "y",
        ESC,
        "q",
    )
    harness.run()
    assert harness.screen.saw("═ Discard the take? ")
    assert kept == [1]
    assert harness.manifest() == []


# --- the input device -------------------------------------------------------------------


def test_an_input_device_that_does_not_open_is_one_line_and_no_crash(
    tmp_path: Path,
) -> None:
    text = CONFIG_TEXT.replace(
        'input_device = "pipewire"', 'input_device = "no such mic"'
    )
    harness = make_harness(
        tmp_path,
        start(-1, ENTER, -1, ESC, "q"),
        hostname="nuc",
        script=SCRIPT,
        config_text=text,
    )
    harness.run()
    screen = harness.screen
    assert screen.saw("no input device matches 'no such mic'")
    assert screen.saw("Level  (no input stream)")
    assert screen.saw("Enter tries the input device again.")
    assert harness.audio.streams == []
    assert harness.manifest() == []


def test_the_session_draws_at_the_minimum_size(tmp_path: Path) -> None:
    harness = make_harness(
        tmp_path, [], hostname="nuc", script=SCRIPT, rows=22, cols=72
    )
    harness.screen.keys = start(ENTER, feed(harness), ENTER, -1, ESC, "q")
    harness.run()
    screen = harness.screen
    frame = next(f for f in screen.frames if "Takes  1" in f)
    assert "A U T O B O T" in frame
    assert "Peak  █" in frame
    assert "Enter Rec   P Play   R Redo   D Drop   N Next   B Back   Esc Leave" in frame
    # The key hints and the message fit the 72 columns.
    assert all(len(row) <= 72 for row in frame.splitlines())


# --- a muted microphone is caught before a take is made ------------------------------

# Thirty blocks of zeros: 2.4 s of digital silence on the meter.
MUTED = 30


def test_a_silent_input_is_said_before_anything_is_recorded(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc", script=SCRIPT)
    harness.screen.keys = start(-1, feed(harness, MUTED, 0), -1, -1, ESC, "q")
    harness.run()
    screen = harness.screen
    # On the screen where the RMS meter is, and on the message line.
    assert screen.saw(f"NO SIGNAL  {SILENCE_WARNING}")
    assert screen.saw(f"No signal: {SILENCE_WARNING}.")
    assert harness.manifest() == []


def test_a_short_silence_is_not_a_muted_microphone(tmp_path: Path) -> None:
    # A stream may open with a moment of zeros.
    harness = make_harness(tmp_path, [], hostname="nuc", script=SCRIPT)
    harness.screen.keys = start(
        -1, feed(harness, 20, 0), -1, feed(harness), -1, ESC, "q"
    )
    harness.run()
    assert not harness.screen.saw("NO SIGNAL")
    assert not harness.screen.saw("No signal")


def test_enter_asks_before_recording_silence_and_no_records_nothing(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc", script=SCRIPT)
    harness.screen.keys = start(
        feed(harness, MUTED, 0), -1, ENTER, -1, "n", -1, feed(harness, 4, 0), ESC, "q"
    )
    harness.run()
    screen = harness.screen
    assert screen.saw(f"═ {SILENCE_QUESTION} ")
    assert screen.saw("digital silence for 0:02: nothing said now")
    assert screen.saw("Check the microphone's own mute button, then the")
    assert screen.saw("Record anyway?")
    assert not screen.saw("Recording: speak now.")
    assert harness.manifest() == []


def test_recording_silence_anyway_is_the_owners_choice(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc", script=SCRIPT)
    harness.screen.keys = start(
        feed(harness, MUTED, 0),
        -1,
        ENTER,
        "y",
        feed(harness, 4, 0),
        ENTER,
        -1,
        ESC,
        "q",
    )
    harness.run()
    assert len(harness.manifest()) == 1
    assert harness.screen.saw(
        "Saved, but digital silence: is the microphone muted? R redoes it."
    )


def test_r_asks_before_it_discards_a_take_for_silence(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc", script=SCRIPT)
    harness.screen.keys = start(
        ENTER,
        feed(harness),
        ENTER,
        feed(harness, MUTED, 0),
        -1,
        "r",
        -1,
        "n",
        -1,
        ESC,
        "q",
    )
    harness.run()
    assert harness.screen.saw(f"═ {SILENCE_QUESTION} ")
    # The good take is still there.
    assert [line["prompt_id"] for line in harness.manifest()] == ["bare-01"]
    assert not harness.screen.saw("Discarded the take")


def test_a_signal_that_returns_clears_the_warning(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc", script=SCRIPT)
    harness.screen.keys = start(
        feed(harness, MUTED, 0),
        -1,
        feed(harness),
        -1,
        ENTER,
        feed(harness),
        ENTER,
        -1,
        ESC,
        "q",
    )
    harness.run()
    screen = harness.screen
    assert screen.saw("The microphone is delivering a signal again.")
    assert "NO SIGNAL" not in screen.frames[-3]
    # Enter recorded without asking.
    assert not screen.saw(f"═ {SILENCE_QUESTION} ")
    assert len(harness.manifest()) == 1


@pytest.mark.parametrize(("rows", "cols"), [(24, 80), (22, 72)])
def test_the_silence_warning_fits_every_size(
    tmp_path: Path, rows: int, cols: int
) -> None:
    harness = make_harness(
        tmp_path, [], hostname="nuc", script=SCRIPT, rows=rows, cols=cols
    )
    harness.screen.keys = start(feed(harness, MUTED, 0), -1, ENTER, -1, "n", ESC, "q")
    harness.run()
    screen = harness.screen
    assert screen.saw(
        "NO SIGNAL  the microphone delivers silence: check its mute button"
    )
    assert screen.saw("Record anyway?")
    assert all(
        len(row) <= cols for frame in screen.frames for row in frame.splitlines()
    )
