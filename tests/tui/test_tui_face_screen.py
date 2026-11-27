"""The Face screen, opened from the main menu of the real `App` on fakes."""

import curses
from pathlib import Path
from typing import Any

import pytest
from tui_fakes import (
    ENTER,
    ESC,
    F1,
    UP,
    Harness,
    Key,
    fake_colour_theme,
    make_harness,
)

from autobot.tui.app import MIN_HEIGHT, MIN_WIDTH
from autobot.tui.echo import FINISHED, HEARD, PLAYING_BACK, RECORD_SECONDS, SPEECH_LINE
from autobot.tui.face_ticker import DOWN_TEXT
from autobot.tui.router import LISTENING, ROUTED
from autobot.tui.runner import listener_hold_argv
from autobot.tui.screens.face import FaceScreen

FACE = "0"
AT = "2026-11-27T05:40:00.000+00:00"
PROMPT = 'Say "autobot" to wake me.'
OFF_NUC = "the face's microphone and voice runs on the NUC only; this host is testbox"
# One frame of the fake clock, and the microphone audio it brings.
FRAME = 0.1
FRAME_SAMPLES = 1600


def heard(text: str, stretch: int) -> dict[str, Any]:
    return {
        "event": "transcript",
        "phrase": "autobot",
        "kind": "text_match",
        "text": text,
        "stretch": stretch,
        "at": AT,
    }


def woke(stretch: int) -> list[dict[str, Any]]:
    """What the listener publishes for a stretch that woke it."""
    return [
        heard("hey autobot", stretch),
        {
            "event": "score",
            "phrase": "autobot",
            "kind": "text_match",
            "score": 1.0,
            "stretch": stretch,
            "at": AT,
        },
        {
            "event": "wake",
            "phrase": "autobot",
            "target": "ack",
            "kind": "text_match",
            "score": 1.0,
            "at": AT,
        },
    ]


def face_frames(harness: Harness) -> list[str]:
    return [frame for frame in harness.screen.frames if "═ Face " in frame]


def ticker_row(rows: int) -> int:
    """The last row inside the frame: the frame is rows 1 to rows - 3."""
    return rows - 4


def character(frame: str) -> list[str]:
    """The autobot panel of a frame, as its rows."""
    lines = frame.splitlines()
    top = next(index for index, line in enumerate(lines) if "┌─ autobot " in line)
    left = lines[top].index("┌─ autobot ")
    panel = []
    for line in lines[top + 1 :]:
        cut = line[left:]
        if cut.startswith("└"):
            break
        panel.append(cut)
    return panel


@pytest.mark.parametrize(("rows", "cols"), [(MIN_HEIGHT, MIN_WIDTH), (40, 120)])
def test_face_opens_from_the_main_menu_with_its_layout(
    tmp_path: Path, rows: int, cols: int
) -> None:
    harness = make_harness(
        tmp_path, [FACE, -1, -1, ESC, "q"], hostname="nuc", rows=rows, cols=cols
    )
    assert harness.run() == 0
    frame = face_frames(harness)[-1]
    lines = frame.splitlines()
    # The header is the first row inside the frame.
    assert lines[2].lstrip("║ ").startswith("MIC")
    assert "FACE" in lines[2]
    assert lines[2].rstrip("║ ").endswith("listener: down")
    for title in ("┌─ Summary ", "┌─ autobot ", "┌─ Detail "):
        assert title in frame, title
    # The summary panel is left of the character, and above the detail panel.
    summary = next(index for index, line in enumerate(lines) if "Summary" in line)
    assert "┌─ autobot " in lines[summary]
    assert lines[summary].index("Summary") < lines[summary].index("autobot")
    detail = next(index for index, line in enumerate(lines) if "Detail" in line)
    assert detail > summary
    assert PROMPT in frame
    # Disconnected, the ticker on the last row inside the frame says so.
    assert DOWN_TEXT in lines[ticker_row(rows)]
    # On the NUC, the microphone opened and the line was rendered once.
    assert harness.audio.opened == [3]
    assert [call[0] for call in harness.voice.calls] == [SPEECH_LINE]
    # Leaving closed the stream and the event stream.
    assert all(stream.closed for stream in harness.audio.streams)
    assert harness.events.closes >= 1
    assert all(
        len(row) <= cols
        for frame in harness.screen.frames
        for row in frame.splitlines()
    )


def test_a_wake_pops_on_the_ticker_and_the_eyes_react(tmp_path: Path) -> None:
    rows = MIN_HEIGHT
    harness = make_harness(tmp_path, [], theme="dark", rows=rows, cols=MIN_WIDTH)
    screen = harness.screen
    before: list[list[str]] = []
    after: list[list[str]] = []
    popped: list[tuple[str, int, int]] = []

    def look(into: list[list[str]]) -> Key:
        return lambda: into.append(character(screen.text()))

    def ticker() -> None:
        row = ticker_row(rows)
        text = screen.text().splitlines()[row]
        at = text.index("autobot")
        hey = text.index("hey")
        popped.append((text, screen.attr_at(row, at), screen.attr_at(row, hey)))

    screen.keys = [
        FACE,
        -1,
        look(before),
        lambda: harness.events.feed(*woke(1)),
        -1,
        look(after),
        *[-1] * 14,
        ticker,
        ESC,
        "q",
    ]
    assert harness.run() == 0
    theme = fake_colour_theme("dark")
    text, wake_attr, plain_attr = popped[0]
    assert "hey autobot" in text
    assert wake_attr == theme.wake
    assert plain_attr == theme.normal != theme.wake
    assert before[0] != after[0]
    assert screen.saw("listener: connected")
    # The wake only wakes autobot: it listens for a command, and no echo runs.
    assert screen.saw(LISTENING.format(commands='"echo"'))
    assert not screen.saw(HEARD)


def test_a_disconnected_stream_puts_the_down_sentence_on_the_ticker(
    tmp_path: Path,
) -> None:
    rows = MIN_HEIGHT
    harness = make_harness(tmp_path, [], rows=rows, cols=MIN_WIDTH)
    shown: list[str] = []

    def look() -> None:
        shown.append(harness.screen.text().splitlines()[ticker_row(rows)])

    harness.screen.keys = [
        FACE,
        lambda: harness.events.feed(heard("good morning", 1)),
        *[-1] * 14,
        look,
        harness.events.drop,
        -1,
        look,
        ESC,
        "q",
    ]
    assert harness.run() == 0
    assert "good morning" in shown[0] and DOWN_TEXT not in shown[0]
    assert DOWN_TEXT in shown[1] and "good morning" not in shown[1]


def test_off_the_nuc_the_face_opens_with_its_audio_idle(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [FACE, -1, ESC, "q"], hostname="testbox")
    assert harness.run() == 0
    screen = harness.screen
    assert screen.saw("═ Face ")
    assert screen.saw(OFF_NUC)
    assert screen.saw("OFF")
    assert screen.saw(PROMPT)
    # Nothing was opened and nothing was rendered.
    assert harness.audio.streams == []
    assert harness.voice.calls == []


def test_the_detail_panel_scrolls_with_the_monitors_keys(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [], rows=MIN_HEIGHT, cols=MIN_WIDTH)

    def fill() -> None:
        view = harness.app.views[-1]
        assert isinstance(view, FaceScreen)
        for number in range(1, 31):
            view.model.detail.append(f"detail line {number}")

    harness.screen.keys = [FACE, fill, -1, UP, UP, -1, curses.KEY_END, -1, ESC, "q"]
    assert harness.run() == 0
    screen = harness.screen
    frames = face_frames(harness)
    assert any("detail line 30" in frame for frame in frames)
    assert screen.saw("scrolled back 2 lines: End follows")
    assert "detail line 30" in frames[-1]
    assert "scrolled back" not in frames[-1]


def test_help_names_the_face_screens_keys(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [FACE, -1, F1, -1, ENTER, ESC, "q"])
    assert harness.run() == 0
    assert harness.screen.saw("Up, Down       scroll the detail panel")
    assert harness.screen.saw("End            follow the detail panel again")


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_on_the_nuc_a_wake_and_echo_run_the_echo_on_the_screen(
    tmp_path: Path,
) -> None:
    harness = make_harness(tmp_path, [], hostname="nuc", rows=40, cols=120)
    clock = FakeClock()
    harness.app.services.clock = clock
    audio = harness.audio
    before = sorted(tmp_path.rglob("*"))

    def frame() -> None:
        clock.now += FRAME
        if audio.on_block is not None:
            audio.feed(3000, FRAME_SAMPLES)

    harness.screen.keys = [
        FACE,
        -1,
        lambda: harness.events.feed(*woke(1)),
        -1,
        # The command, among other words, routes to the echo.
        lambda: harness.events.feed(heard("please echo my voice", 2)),
        -1,
        -1,
        # autobot speaks now: what is heard meanwhile is not shown.
        lambda: harness.events.feed(heard("you will not see this", 3)),
        *[frame] * 200,
        ESC,
        "q",
    ]
    assert harness.run() == 0
    screen = harness.screen
    assert screen.saw('rule 1: "echo" -> echo agent, heard "please echo my voice"')
    for line in (
        ROUTED.format(agent="echo"),
        HEARD,
        SPEECH_LINE,
        PLAYING_BACK,
        FINISHED,
    ):
        assert screen.saw(line), line
    assert not screen.saw("not see this")
    spoken, recorded = (clip for clip, _ in audio.played)
    assert [call for call in harness.runner.calls] == [
        listener_hold_argv(spoken.seconds),
        listener_hold_argv(RECORD_SECONDS),
    ]
    assert recorded.seconds == RECORD_SECONDS
    assert screen.saw("recorded 5.0 s of 5.0 s")
    # The recording went to no file.
    assert sorted(tmp_path.rglob("*")) == before
