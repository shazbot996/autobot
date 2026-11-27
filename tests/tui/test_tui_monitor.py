"""The live monitor: the event stream's logic, and the screen on the fakes.

`autobot.tui.monitor` is tested without a terminal; one test runs the real
`EventStream` against a real Unix socket in `tmp_path` (no network). The
screen tests feed a fake stream from a list and answer `control snapshot`
with a WAV file, as the listener would.
"""

import curses
import json
import socket
import sys
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from tui_fakes import (
    CONFIG_TEXT,
    DOWN,
    ENTER,
    ESC,
    Harness,
    Key,
    make_harness,
    typed,
)

from autobot.config import TUI_THEMES
from autobot.tui.app import MIN_HEIGHT, MIN_WIDTH
from autobot.tui.audio import Clip, write_wav
from autobot.tui.monitor import (
    HELD_STRETCHES,
    LAST_TEN_SECONDS,
    MARK_MISSED,
    MARK_RIGHT,
    MARK_WRONG,
    RECONNECT_SECONDS,
    EventStream,
    MonitorError,
    MonitorLog,
    clock_time,
    describe_stretch,
    latest_detection,
    mark_record,
    read_snapshot,
    recent_stretches,
)
from autobot.tui.runner import SNAPSHOT_FILE, CommandResult
from autobot.tui.screens.monitor import HINT, HINT_DOWN, NOTHING_TO_MARK
from autobot.wakeword.dataset import (
    LABEL_NEGATIVE,
    LABEL_POSITIVE,
    ClipRecord,
)

RATE = 16000
MONITOR = "4"
AT = "2026-11-26T17:32:23.561+00:00"
CONTROL = [sys.executable, "-m", "autobot.listener.control"]


def score(
    stretch: int,
    value: float | None = 0.851,
    phrase: str = "autobot",
    kind: str = "openwakeword",
    at: str = AT,
) -> dict[str, Any]:
    return {
        "event": "score",
        "phrase": phrase,
        "kind": kind,
        "score": value,
        "stretch": stretch,
        "at": at,
    }


def wake(value: float = 0.851, phrase: str = "autobot", at: str = AT) -> dict[str, Any]:
    return {
        "event": "wake",
        "phrase": phrase,
        "target": "ack",
        "kind": "openwakeword",
        "score": value,
        "at": at,
    }


# --- recent_stretches ---------------------------------------------------------------


def test_recent_stretches_keeps_the_last_five_by_identifier() -> None:
    stretches = recent_stretches([score(number) for number in range(1, 8)])
    assert [stretch.id for stretch in stretches] == [3, 4, 5, 6, 7]
    assert len(stretches) == HELD_STRETCHES
    assert all(not stretch.detected for stretch in stretches)


def test_a_wake_belongs_to_the_stretch_whose_score_preceded_it() -> None:
    stretches = recent_stretches(
        [score(1, 0.2), score(2, 0.91), wake(0.91), score(3, 0.1)]
    )
    assert [stretch.detected for stretch in stretches] == [False, True, False]
    detection = latest_detection(stretches)
    assert detection is not None
    assert (detection.id, detection.wakes[0].score) == (2, 0.91)
    assert describe_stretch(detection) == (
        f"stretch 2  {clock_time(AT)}  autobot 0.910  WAKE"
    )


def test_a_stretch_has_one_score_line_per_wake_phrase() -> None:
    stretches = recent_stretches(
        [
            score(4, 0.3),
            score(4, None, phrase="computer", kind="text_match"),
            wake(0.3),
        ]
    )
    assert len(stretches) == 1
    assert [(line.phrase, line.score) for line in stretches[0].scores] == [
        ("autobot", 0.3),
        ("computer", None),
    ]
    assert stretches[0].wakes[0].phrase == "autobot"
    assert describe_stretch(stretches[0]) == (
        f"stretch 4  {clock_time(AT)}  autobot 0.300  computer -  WAKE"
    )


def test_a_restarted_listener_starts_the_list_afresh() -> None:
    # Identifiers start at 1 again with each listener run.
    stretches = recent_stretches([score(6), wake(), score(7), score(1, 0.4)])
    assert [stretch.id for stretch in stretches] == [1]
    assert latest_detection(stretches) is None


def test_a_wake_with_no_score_before_it_and_other_lines_are_left_out() -> None:
    events: list[dict[str, Any]] = [
        wake(),
        {"event": "hello"},
        {"event": "score", "stretch": "seven"},
        score(1),
    ]
    stretches = recent_stretches(events)
    assert [(stretch.id, stretch.detected) for stretch in stretches] == [(1, False)]


def test_clock_time_is_local_and_survives_nonsense() -> None:
    assert len(clock_time(AT)) == 8
    assert clock_time("not a time") == "--:--:--"


# --- the log ------------------------------------------------------------------------


def test_the_log_has_a_line_per_event_and_marks_the_wake() -> None:
    log = MonitorLog()
    log.feed([score(2, 0.851), wake(0.851)])
    first, second = log.lines
    assert (first.stretch, first.wake) == (2, False)
    assert first.text == "autobot      openwakeword  0.851"
    assert (second.stretch, second.wake) == (2, True)
    assert second.text == "autobot      openwakeword  0.851  WAKE -> ack"
    assert [stretch.id for stretch in log.stretches] == [2]
    detection = log.detection()
    assert detection is not None and detection.id == 2


def test_the_log_holds_five_stretches_across_many_polls() -> None:
    log = MonitorLog()
    for number in range(1, 9):
        log.feed([score(number)])
        if number == 3:
            log.feed([wake()])
    assert [stretch.id for stretch in log.stretches] == [4, 5, 6, 7, 8]
    # The detection was on stretch 3, which the listener no longer holds.
    assert log.detection() is None
    assert len(log.lines) == 9


def test_a_dropped_connection_forgets_the_stretches_and_keeps_the_lines() -> None:
    log = MonitorLog()
    log.feed([score(5), wake()])
    log.disconnected()
    assert log.stretches == []
    assert log.detection() is None
    assert len(log.lines) == 2
    log.note("connected to the listener")
    assert log.lines[-1].note
    # A wake right after a reconnect has no stretch to belong to.
    log.feed([wake()])
    assert log.detection() is None


# --- the real client against a real Unix socket -------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def line(event: dict[str, Any]) -> bytes:
    return (json.dumps(event) + "\n").encode()


def test_event_stream_real_socket(tmp_path: Path) -> None:
    path = tmp_path / "l.sock"
    clock = Clock()
    stream = EventStream(path, clock)

    # No socket file: not connected, no events, no exception.
    assert stream.poll() == []
    assert not stream.connected

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)
    server.settimeout(5)
    try:
        # It does not try again before two seconds have passed.
        clock.now += RECONNECT_SECONDS / 2
        assert stream.poll() == []
        assert not stream.connected
        clock.now += RECONNECT_SECONDS
        assert stream.poll() == []
        assert stream.connected
        reader, _ = server.accept()

        # Complete lines are events; a partial line waits for its end.
        first, second = line(score(1)), line(wake())
        reader.sendall(first + second[:20])
        assert stream.poll() == [score(1)]
        assert stream.poll() == []
        reader.sendall(second[20:] + b"not json\n[1, 2]\n" + line(score(2)))
        assert stream.poll() == [wake(), score(2)]

        # The listener goes away: the last complete line still arrives.
        reader.sendall(line(score(3)) + b'{"event": "sco')
        reader.close()
        assert stream.poll() == [score(3)]
        assert not stream.connected
        # It reconnects, two seconds later, and the half line is forgotten.
        assert stream.poll() == []
        assert not stream.connected
        clock.now += RECONNECT_SECONDS
        assert stream.poll() == []
        assert stream.connected
        reader, _ = server.accept()
        reader.sendall(line(score(1)))
        assert stream.poll() == [score(1)]

        # Closed on purpose, the next poll connects at once.
        stream.close()
        assert not stream.connected
        assert stream.poll() == []
        assert stream.connected
        reader.close()
    finally:
        stream.close()
        server.close()


def test_event_stream_refused(tmp_path: Path) -> None:
    # A socket file nothing listens on (a listener that died) is "not now".
    path = tmp_path / "l.sock"
    dead = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    dead.bind(str(path))
    dead.close()
    stream = EventStream(path)
    assert stream.poll() == []
    assert not stream.connected


# --- what a mark saves ----------------------------------------------------------------


def snapshot_clip(seconds: float = 1.0, value: int = 2500) -> Clip:
    return Clip(np.full(int(seconds * RATE), value, dtype=np.int16), RATE)


def test_read_snapshot_reads_the_wav_and_deletes_it(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.wav"
    write_wav(path, snapshot_clip(0.5))
    clip = read_snapshot(path)
    assert (len(clip.samples), clip.sample_rate) == (RATE // 2, RATE)
    assert not path.exists()


def test_read_snapshot_fails_in_one_line_and_leaves_nothing(tmp_path: Path) -> None:
    missing = tmp_path / "snapshot.wav"
    with pytest.raises(MonitorError, match="cannot read .*snapshot.wav as a WAV file"):
        read_snapshot(missing)
    missing.write_bytes(b"not a wav file")
    with pytest.raises(MonitorError, match="cannot read"):
        read_snapshot(missing)
    assert not missing.exists()
    with wave.open(str(missing), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(b"\0" * 64)
    with pytest.raises(MonitorError, match="not a mono 16-bit WAV file"):
        read_snapshot(missing)
    assert not missing.exists()


def test_the_three_marks_and_their_manifest_lines() -> None:
    assert (MARK_RIGHT.label, MARK_RIGHT.prompt) == (
        LABEL_POSITIVE,
        "monitor: right detection",
    )
    assert (MARK_WRONG.label, MARK_WRONG.prompt) == (
        LABEL_NEGATIVE,
        "monitor: false accept",
    )
    assert (MARK_MISSED.label, MARK_MISSED.prompt) == (
        LABEL_POSITIVE,
        "monitor: missed wake",
    )
    record = mark_record(MARK_WRONG, "autobot", "Charles", "pipewire")
    assert (record.source, record.prompt_id, record.file) == ("monitor", "", "")
    assert (record.phrase, record.speaker, record.device) == (
        "autobot",
        "Charles",
        "pipewire",
    )


# --- the screen ---------------------------------------------------------------------


def opened(*keys: Key) -> list[Key]:
    """Open the monitor as Charles, then `keys`."""
    return [MONITOR, *typed("Charles"), ENTER, *keys]


def deliver(harness: Harness, *events: dict[str, Any]) -> Callable[[], None]:
    """A key that has the fake listener publish `events`."""
    return lambda: harness.events.feed(*events)


def answer_snapshots(harness: Harness, tmp_path: Path, seconds: float = 1.0) -> None:
    """Have `control snapshot` write its WAV and print its path, as the listener does."""

    def answer(argv: list[str]) -> CommandResult | None:
        if "snapshot" not in argv:
            return None
        path = tmp_path / SNAPSHOT_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        write_wav(path, snapshot_clip(seconds), overwrite=True)
        return CommandResult(0, f"{SNAPSHOT_FILE}\n")

    harness.runner.answer = answer


def frames_of(path: Path) -> int:
    with wave.open(str(path), "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (
            1,
            2,
            RATE,
        )
        return wav.getnframes()


def test_w_on_a_detection_saves_its_stretch_as_a_negative(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc")
    answer_snapshots(harness, tmp_path, seconds=1.5)
    harness.screen.keys = opened(
        -1,
        deliver(harness, score(2, 0.851), wake(0.851)),
        -1,
        "w",
        -1,
        ESC,
        "q",
    )
    assert harness.run() == 0
    screen = harness.screen

    # It asked who is speaking, then showed the events as they came.
    assert screen.saw("Who is speaking? The marked clips carry this name:")
    assert screen.saw("═ Live monitor ")
    assert screen.saw("connected: run/listener.sock")
    assert screen.saw(f"{clock_time(AT)}       #2  autobot      openwakeword  0.851")
    assert screen.saw("0.851  WAKE -> ack")
    assert screen.saw(f"Detection  #2  {clock_time(AT)}  autobot 0.851")
    assert screen.saw(HINT)

    # W ran the snapshot command with that stretch's identifier.
    assert harness.runner.calls == [[*CONTROL, "snapshot", "--stretch", "2"]]
    # The clip went into the dataset as a negative from the monitor.
    (entry,) = harness.manifest()
    assert entry["label"] == LABEL_NEGATIVE
    assert entry["source"] == "monitor"
    assert entry["prompt"] == "monitor: false accept"
    assert entry["prompt_id"] == ""
    assert (entry["speaker"], entry["phrase"], entry["device"]) == (
        "Charles",
        "autobot",
        "pipewire",
    )
    assert entry["file"].startswith("Charles-monitor-")
    assert frames_of(harness.dataset.root / entry["file"]) == int(1.5 * RATE)
    # The owner's voice does not linger outside the dataset.
    assert not (tmp_path / SNAPSHOT_FILE).exists()
    assert screen.saw(f"Saved as negative (1.5 s): {entry['file']}")
    assert screen.saw("#2  marked wrong: saved as negative")
    assert screen.saw("(marks saved this visit: 1)")
    # The stream was polled, never opened by anything else, and closed on leaving.
    assert harness.events.closes == 1
    assert harness.config_text() == CONFIG_TEXT


def test_r_on_a_detection_saves_its_stretch_as_a_positive(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc")
    answer_snapshots(harness, tmp_path)
    harness.screen.keys = opened(
        deliver(harness, score(1, 0.2), score(2, 0.9), wake(0.9), score(3, 0.1)),
        -1,
        "r",
        -1,
        ESC,
        "q",
    )
    harness.run()
    # The latest detection is stretch 2, not the latest stretch.
    assert harness.runner.calls == [[*CONTROL, "snapshot", "--stretch", "2"]]
    (entry,) = harness.manifest()
    assert (entry["label"], entry["source"], entry["prompt"]) == (
        LABEL_POSITIVE,
        "monitor",
        "monitor: right detection",
    )


def test_m_offers_the_recent_stretches_with_the_latest_selected(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc")
    answer_snapshots(harness, tmp_path)
    harness.screen.keys = opened(
        deliver(harness, score(6, 0.12), score(7, 0.31)),
        -1,
        "m",
        -1,
        ENTER,
        -1,
        ESC,
        "q",
    )
    harness.run()
    screen = harness.screen
    assert screen.saw("═ Which utterance was the missed wake? ")
    # The latest stretch is first and selected; the buffer is the last entry.
    assert screen.saw(f"► stretch 7  {clock_time(AT)}  autobot 0.310")
    assert screen.saw(f"  stretch 6  {clock_time(AT)}  autobot 0.120")
    assert screen.saw(f"  {LAST_TEN_SECONDS}")
    assert harness.runner.calls == [[*CONTROL, "snapshot", "--stretch", "7"]]
    (entry,) = harness.manifest()
    assert (entry["label"], entry["source"], entry["prompt"], entry["prompt_id"]) == (
        LABEL_POSITIVE,
        "monitor",
        "monitor: missed wake",
        "",
    )
    assert (harness.dataset.root / entry["file"]).is_file()


def test_m_and_the_last_ten_seconds_saves_the_buffer(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc")
    answer_snapshots(harness, tmp_path, seconds=10.0)
    harness.screen.keys = opened(
        deliver(harness, score(1, 0.1)),
        -1,
        "m",
        DOWN,
        ENTER,
        -1,
        # The buffer is not a stretch: a second miss saves it again.
        "m",
        DOWN,
        ENTER,
        ESC,
        "q",
    )
    harness.run()
    assert harness.runner.calls == [
        [*CONTROL, "snapshot", "--buffer"],
        [*CONTROL, "snapshot", "--buffer"],
    ]
    first, second = harness.manifest()
    assert first["label"] == second["label"] == LABEL_POSITIVE
    assert first["prompt"] == "monitor: missed wake"
    assert first["file"] != second["file"]
    assert frames_of(harness.dataset.root / first["file"]) == 10 * RATE


def test_with_no_stretch_held_the_buffer_is_the_only_entry(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc")
    answer_snapshots(harness, tmp_path)
    harness.screen.keys = opened(-1, "m", -1, ENTER, ESC, "q")
    harness.run()
    assert harness.screen.saw(f"► {LAST_TEN_SECONDS}")
    assert not harness.screen.saw("► stretch")
    assert harness.runner.calls == [[*CONTROL, "snapshot", "--buffer"]]
    assert len(harness.manifest()) == 1


def test_a_snapshot_that_fails_is_shown_and_adds_nothing(tmp_path: Path) -> None:
    reason = "stretch 1 is no longer held: the listener holds stretches 3 to 7"
    harness = make_harness(tmp_path, [], hostname="nuc")
    harness.runner.answer = lambda argv: CommandResult(1, f"{reason}\n")
    harness.screen.keys = opened(
        deliver(harness, score(1, 0.9), wake(0.9)),
        -1,
        "w",
        -1,
        ENTER,
        -1,
        ESC,
        "q",
    )
    harness.run()
    screen = harness.screen
    assert screen.saw("═ Not saved ")
    assert screen.saw("stretch 1 is no longer held: the listener holds stretches")
    assert screen.saw("Not saved: stretch 1 is no longer held")
    assert harness.manifest() == []
    assert not harness.dataset.root.exists()


def test_a_snapshot_that_wrote_no_file_adds_nothing(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc")
    harness.runner.result = CommandResult(0, f"{SNAPSHOT_FILE}\n")
    harness.screen.keys = opened(
        deliver(harness, score(1, 0.9), wake(0.9)), -1, "r", ENTER, ESC, "q"
    )
    harness.run()
    assert harness.screen.saw("Not saved: cannot read")
    assert harness.manifest() == []


def test_r_and_w_need_a_detection(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc")
    answer_snapshots(harness, tmp_path)
    harness.screen.keys = opened(
        deliver(harness, score(1, 0.2)), -1, "r", -1, "w", -1, ESC, "q"
    )
    harness.run()
    assert harness.screen.saw(NOTHING_TO_MARK)
    assert harness.screen.saw("Detection  none held")
    assert harness.runner.calls == []
    assert harness.manifest() == []


def test_a_stretch_is_saved_once(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc")
    answer_snapshots(harness, tmp_path)
    harness.screen.keys = opened(
        deliver(harness, score(4, 0.9), wake(0.9)), -1, "w", -1, "r", -1, ESC, "q"
    )
    harness.run()
    assert len(harness.runner.calls) == 1
    (entry,) = harness.manifest()
    assert harness.screen.saw(
        f"Stretch 4 is already saved as negative: {entry['file']}"
    )


def test_off_the_nuc_the_monitor_refuses_in_one_line(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [MONITOR, -1, "q"])
    harness.run()
    screen = harness.screen
    assert screen.saw("the live monitor runs on the NUC only; this host is testbox")
    assert not screen.saw("Who is speaking?")
    assert not screen.saw("═ Live monitor ")
    assert harness.events.closes == 0


def test_the_speaker_defaults_to_the_last_in_the_manifest(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc")
    harness.dataset.add(
        snapshot_clip(0.2),
        ClipRecord(
            file="",
            phrase="autobot",
            prompt="autobot",
            prompt_id="bare-01",
            label=LABEL_POSITIVE,
            source="session",
            speaker="Sam",
            device="pipewire",
            recorded_at="2026-11-26T17:40:03-05:00",
        ),
    )
    # Enter accepts the name offered; a `t` typed there is a letter, not a theme.
    harness.screen.keys = [MONITOR, -1, "t", ENTER, -1, ESC, "q"]
    harness.run()
    assert harness.screen.saw("Speaker    Samt   (marks saved this visit: 0)")
    assert harness.config_text() == CONFIG_TEXT


def test_cancelling_the_name_opens_nothing(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [MONITOR, ESC, -1, "q"], hostname="nuc")
    harness.run()
    assert not harness.screen.saw("Listener   ")
    assert harness.events.closes == 0


def test_with_the_listener_down_it_says_so_and_s_starts_it(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc")
    harness.runner.status = CommandResult(0, "listener: not running\n")
    harness.runner.result = CommandResult(0, "listener started (pid 4242)\n")
    harness.screen.keys = opened(-1, -1, "s", -1, ESC, "q")
    harness.run()
    screen = harness.screen
    assert screen.saw("Listener   not running: S starts it")
    assert screen.saw("(not connected to the listener: S starts it)")
    assert screen.saw(HINT_DOWN)
    assert screen.saw(" S Start")
    assert harness.runner.calls == [[*CONTROL, "up"]]
    assert screen.saw("Start: listener started (pid 4242)")


def test_marks_while_the_listener_is_down_show_its_one_line(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc")
    harness.runner.result = CommandResult(1, "the listener is not running\n")
    harness.screen.keys = opened(-1, "m", ENTER, -1, ENTER, ESC, "q")
    harness.run()
    assert harness.runner.calls == [[*CONTROL, "snapshot", "--buffer"]]
    assert harness.screen.saw("Not saved: the listener is not running")
    assert harness.manifest() == []


def test_a_listener_that_goes_away_forgets_its_stretches(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc")
    answer_snapshots(harness, tmp_path)
    harness.screen.keys = opened(
        deliver(harness, score(3, 0.9), wake(0.9)),
        -1,
        harness.events.drop,
        -1,
        "w",
        -1,
        ESC,
        "q",
    )
    harness.run()
    screen = harness.screen
    assert screen.saw("the listener went away: trying again every 2 s")
    # The detection's stretch may not be held by the next listener: no mark.
    assert screen.saw(NOTHING_TO_MARK)
    assert harness.runner.calls == []


def test_with_two_phrases_a_miss_asks_which_one(tmp_path: Path) -> None:
    two = CONFIG_TEXT.replace(
        "[ack]",
        '[[wake_phrases]]\nphrase = "computer"\nkind = "text_match"\n'
        'threshold = 0.5\ntarget = "ack"\n\n[ack]',
    )
    harness = make_harness(tmp_path, [], hostname="nuc", config_text=two)
    answer_snapshots(harness, tmp_path)
    harness.screen.keys = opened(
        deliver(
            harness,
            score(1, 0.2),
            score(1, 0.6, phrase="computer", kind="text_match"),
            wake(0.6, phrase="computer"),
        ),
        -1,
        "m",
        ENTER,
        -1,
        DOWN,
        ENTER,
        -1,
        ESC,
        "q",
    )
    harness.run()
    screen = harness.screen
    # One log line per phrase of the stretch.
    assert screen.saw("#1  autobot      openwakeword  0.200")
    assert screen.saw("#1  computer     text_match    0.600")
    assert screen.saw(
        f"► stretch 1  {clock_time(AT)}  autobot 0.200  computer 0.600  WAKE"
    )
    assert screen.saw("═ Which wake phrase was missed? ")
    (entry,) = harness.manifest()
    assert (entry["phrase"], entry["label"]) == ("computer", LABEL_POSITIVE)


def test_the_log_scrolls_back_and_end_follows(tmp_path: Path) -> None:
    harness = make_harness(
        tmp_path, [], hostname="nuc", rows=MIN_HEIGHT, cols=MIN_WIDTH
    )
    events = [score(number, number / 100) for number in range(1, 31)]
    harness.screen.keys = opened(
        deliver(harness, *events),
        -1,
        curses.KEY_PPAGE,
        -1,
        curses.KEY_END,
        -1,
        ESC,
        "q",
    )
    harness.run()
    screen = harness.screen
    # A page is the ten rows the log has in the smallest terminal.
    assert screen.saw("scrolled back 10 lines: End follows")
    scrolled = next(frame for frame in screen.frames if "scrolled back" in frame)
    assert "#20  autobot      openwakeword  0.200" in scrolled
    assert "#21  autobot" not in scrolled
    following = [frame for frame in screen.frames if "═ Live monitor " in frame][-1]
    assert "#30  autobot      openwakeword  0.300" in following
    assert "scrolled back" not in following


@pytest.mark.parametrize("theme", [None, *TUI_THEMES])
def test_the_monitor_draws_at_the_minimum_size_in_every_theme(
    tmp_path: Path, theme: str | None
) -> None:
    harness = make_harness(
        tmp_path, [], hostname="nuc", rows=MIN_HEIGHT, cols=MIN_WIDTH, theme=theme
    )
    answer_snapshots(harness, tmp_path)
    harness.screen.keys = opened(
        deliver(harness, score(2, 0.851), wake(0.851)),
        -1,
        "m",
        -1,
        ESC,
        "w",
        -1,
        ESC,
        "q",
    )
    assert harness.run() == 0
    screen = harness.screen
    assert not screen.saw("Terminal too small")
    assert screen.saw("┌─ Listener ")
    assert screen.saw(HINT)
    frame = next(frame for frame in screen.frames if "WAKE -> ack" in frame)
    assert all(len(row) <= MIN_WIDTH for row in frame.splitlines())
    if theme is not None:
        # WAKE is drawn in the error colour, not the log's.
        row, text = next(
            (number, text)
            for number, text in enumerate(frame.splitlines())
            if "WAKE -> ack" in text
        )
        assert harness.app.canvas.theme.error != harness.app.canvas.theme.normal
        assert screen.saw("Saved as negative")
        assert text.index("WAKE") > 0 and row > 0


def test_t_switches_the_theme_on_the_monitor(tmp_path: Path) -> None:
    harness = make_harness(
        tmp_path, opened(-1, "T", -1, ESC, "q"), hostname="nuc", theme="light"
    )
    harness.run()
    assert 'theme = "dark"' in harness.config_text()
    # The speaker's name was typed with a `t` in no part of it lost: "Charles".
    assert harness.screen.saw("Speaker    Charles")
