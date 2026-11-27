"""The Face screen, opened from the main menu of the real `App` on fakes."""

import curses
from pathlib import Path
from typing import Any

import pytest
from tui_fakes import (
    CONFIG_TEXT,
    ENTER,
    ESC,
    F1,
    UP,
    Harness,
    Key,
    fake_colour_theme,
    make_harness,
)

from autobot.agents.question import COUNT_WORDS, INVITE
from autobot.tui.app import FRAME_MS, MIN_HEIGHT, MIN_WIDTH
from autobot.tui.echo import FINISHED, HEARD, PLAYING_BACK, RECORD_SECONDS, SPEECH_LINE
from autobot.tui.face_ticker import DOWN_TEXT
from autobot.tui.router import LISTENING, ROUTED
from autobot.tui.runner import listener_hold_argv
from autobot.tui.screens.face import FaceScreen
from autobot.tui.tree_game import Position

# Tab opens the face from the main menu (AG-8).
FACE = 9
AT = "2026-11-27T05:40:00.000+00:00"
PROMPT = 'Say "autobot" to wake me.'
OFF_NUC = "the face's microphone and voice runs on the NUC only; this host is testbox"
# One frame of the fake clock, and the microphone audio it brings.
FRAME = 0.1
FRAME_SAMPLES = 1600
# The one rule these tests route by: "echo" runs the echo.
ECHO_ONLY = (
    CONFIG_TEXT
    + """
[[routes]]
command = "echo"
agent = "echo"
"""
)


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
    for title in ("┌─ Summary ", "┌─ autobot ", "┌─ Map "):
        assert title in frame, title
    # The summary panel is left of the character, and above the detail panel.
    summary = next(index for index, line in enumerate(lines) if "Summary" in line)
    assert "┌─ autobot " in lines[summary]
    assert lines[summary].index("Summary") < lines[summary].index("autobot")
    detail = next(index for index, line in enumerate(lines) if "┌─ Map " in line)
    assert detail > summary
    # Before a wake the map shows the root's box, with the protagonist on it.
    below = "\n".join(lines[detail:])
    assert "│ autobot │" in below
    assert "╭─╮╭─╮" in below
    box = next(i for i, line in enumerate(lines) if "│ autobot │" in line)
    eyes = next(i for i, line in enumerate(lines) if "╭─╮╭─╮" in line)
    assert detail < eyes < box
    assert PROMPT in frame
    # Disconnected, the ticker on the last row inside the frame says so.
    assert DOWN_TEXT in lines[ticker_row(rows)]
    # On the NUC, the microphone opened and the lines were rendered once:
    # the echo's, and the question agent's invitation and count.
    assert harness.audio.opened == [3]
    assert [call[0] for call in harness.voice.calls] == [
        SPEECH_LINE,
        INVITE,
        *COUNT_WORDS,
    ]
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
    harness = make_harness(
        tmp_path,
        [],
        theme="dark",
        rows=rows,
        cols=MIN_WIDTH,
        config_text=ECHO_ONLY,
    )
    clock = FakeClock()
    harness.app.services.clock = clock
    screen = harness.screen
    before: list[list[str]] = []
    after: list[list[str]] = []
    popped: list[tuple[str, int, int]] = []
    delays: list[int] = []

    def look(into: list[list[str]]) -> Key:
        return lambda: into.append(character(screen.text()))

    def ticker() -> None:
        row = ticker_row(rows)
        text = screen.text().splitlines()[row]
        at = text.index("autobot")
        hey = text.index("hey")
        popped.append((text, screen.attr_at(row, at), screen.attr_at(row, hey)))
        # The face times its frames by the ticker's next column.
        delays.append(screen.delay)

    def frame() -> None:
        clock.now += FRAME

    screen.keys = [
        FACE,
        -1,
        look(before),
        lambda: harness.events.feed(*woke(1)),
        -1,
        look(after),
        *[frame] * 14,
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
    assert 1 <= delays[0] < FRAME_MS
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
    clock = FakeClock()
    harness.app.services.clock = clock
    shown: list[str] = []

    def look() -> None:
        shown.append(harness.screen.text().splitlines()[ticker_row(rows)])

    def frame() -> None:
        clock.now += FRAME

    harness.screen.keys = [
        FACE,
        lambda: harness.events.feed(heard("good morning", 1)),
        *[frame] * 14,
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


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_an_agents_view_scrolls_with_the_monitors_keys(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [], rows=MIN_HEIGHT, cols=MIN_WIDTH)
    clock = FakeClock()
    harness.app.services.clock = clock

    def fill() -> None:
        view = harness.app.views[-1]
        assert isinstance(view, FaceScreen)
        # An agent's running view is shown, as it is while the agent runs.
        view._level = Position("agents", "time", "running")
        for number in range(1, 31):
            view.model.detail.append(f"detail line {number}")

    def later() -> None:
        clock.now += 1.0

    harness.screen.keys = [
        FACE,
        fill,
        later,
        -1,
        UP,
        UP,
        -1,
        curses.KEY_END,
        -1,
        ESC,
        "q",
    ]
    assert harness.run() == 0
    screen = harness.screen
    frames = face_frames(harness)
    # The panel is the agent's, titled with its name, and no map is drawn.
    assert "┌─ time " in frames[-1]
    assert "┌─ Map " not in frames[-1] and "│ autobot │" not in frames[-1]
    assert any("detail line 30" in frame for frame in frames)
    assert screen.saw("scrolled back 2 lines: End follows")
    assert "detail line 30" in frames[-1]
    assert "scrolled back" not in frames[-1]


def test_the_scroll_keys_do_nothing_on_the_map(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [FACE, -1, UP, curses.KEY_PPAGE, -1, ESC, "q"])
    assert harness.run() == 0
    view_frames = face_frames(harness)
    assert all("scrolled back" not in frame for frame in view_frames)
    assert "┌─ Map " in view_frames[-1]


def test_help_names_the_face_screens_keys(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [FACE, -1, F1, -1, ENTER, ESC, "q"], rows=40)
    assert harness.run() == 0
    assert harness.screen.saw("Up, Down       scroll an agent's view")
    assert harness.screen.saw("Tab            back to the main menu (also Esc)")


# --- the game screen ----------------------------------------------------------

TREE = (
    CONFIG_TEXT
    + """
[[routes]]
command = "weather"
agent = "weather"

[[routes]]
command = "time"
agent = "time"
"""
)
QUESTION = "Shall I look it up?"
CONFIRMING = (
    TREE
    + f"""
[[agents]]
name = "weather"
confirm_entry = true
confirm_entry_question = "{QUESTION}"
"""
)


def map_rows(frame: str) -> list[str]:
    """The rows of the detail panel of a frame, its title row first."""
    lines = frame.splitlines()
    top = next(index for index, line in enumerate(lines) if "┌─ Map " in line)
    return lines[top:]


def green_text(harness: Harness) -> str:
    """The characters of the detail panel drawn in the theme's green, row by row."""
    screen = harness.screen
    ok = harness.app.canvas.theme.ok
    lines = screen.text().splitlines()
    top = next(index for index, line in enumerate(lines) if "┌─ Map " in line)
    return "\n".join(
        "".join(
            screen.grid[row][col] if screen.attr_at(row, col) == ok else " "
            for col in range(screen.cols)
        ).rstrip()
        for row in range(top, len(lines) - 2)
    )


@pytest.mark.parametrize(("rows", "cols"), [(MIN_HEIGHT, MIN_WIDTH), (40, 120)])
def test_the_protagonist_walks_the_tree_as_the_session_moves(
    tmp_path: Path, rows: int, cols: int
) -> None:
    harness = make_harness(
        tmp_path,
        [],
        hostname="nuc",
        rows=rows,
        cols=cols,
        config_text=CONFIRMING,
        theme="light",
    )
    clock = FakeClock()
    harness.app.services.clock = clock
    seen: dict[str, str] = {}
    green: dict[str, str] = {}

    def frame() -> None:
        clock.now += FRAME

    def look(name: str) -> Key:
        def note() -> None:
            seen[name] = harness.screen.text()
            green[name] = green_text(harness)

        return note

    harness.screen.keys = [
        FACE,
        -1,
        look("idle"),
        lambda: harness.events.feed(*woke(1)),
        *[frame] * 8,
        look("agents"),
        lambda: harness.events.feed(heard("weather", 2)),
        *[frame] * 8,
        look("weather"),
        ESC,
        "q",
    ]
    assert harness.run() == 0
    # Before a wake: the root's box, in green, with the protagonist on it.
    idle = "\n".join(map_rows(seen["idle"]))
    assert "┌─ Map " in idle and "│ autobot │" in idle and "╭─╮╭─╮" in idle
    assert "autobot" in green["idle"]
    assert "say [cancel] to go back" in map_rows(seen["idle"])[0]
    # After the wake he stands on the agents' level, with the keyword beside it.
    agents = map_rows(seen["agents"])
    assert any("╭─╮╭─╮" in line for line in agents)
    assert "say [cancel] to go back" in agents[0]
    assert "autobot" not in green["agents"]
    # After the command the weather box is green, and its question is under it.
    weather = "\n".join(map_rows(seen["weather"]))
    assert "weather" in green["weather"]
    assert "time" not in green["weather"]
    assert QUESTION in weather
    assert all(
        len(row) <= cols
        for frame in harness.screen.frames
        for row in frame.splitlines()
    )


def test_while_an_agent_runs_the_panel_is_its_view_and_no_map(tmp_path: Path) -> None:
    harness = make_harness(
        tmp_path, [], hostname="nuc", rows=40, cols=120, config_text=TREE
    )
    clock = FakeClock()
    harness.app.services.clock = clock

    def frame() -> None:
        clock.now += FRAME

    shots: list[str] = []
    harness.screen.keys = [
        FACE,
        -1,
        lambda: harness.events.feed(*woke(1)),
        -1,
        lambda: harness.events.feed(heard("what time is it", 2)),
        *[frame] * 10,
        lambda: shots.append(harness.screen.text()),
        # The answer stays readable after the agent has ended...
        *[frame] * 100,
        lambda: shots.append(harness.screen.text()),
        # ...and then the protagonist is back on the map, at the root.
        *[frame] * 150,
        lambda: shots.append(harness.screen.text()),
        ESC,
        "q",
    ]
    assert harness.run() == 0
    running, lingering, back = shots
    for shot in (running, lingering):
        assert "┌─ time " in shot and "┌─ Map " not in shot
        assert "│ autobot │" not in shot
        assert "Friday, November 27, 2026" in shot and "15:42:07" in shot
    assert "It's 3:42 in the afternoon." in running
    assert "┌─ Map " in back and "│ autobot │" in back
    # The time agent started no command and looked nothing up.
    assert harness.launch.calls == [] and harness.lookup.calls == []


def test_a_wake_ends_the_lingering_view_at_once(tmp_path: Path) -> None:
    harness = make_harness(
        tmp_path, [], hostname="nuc", rows=40, cols=120, config_text=TREE
    )
    clock = FakeClock()
    harness.app.services.clock = clock

    def frame() -> None:
        clock.now += FRAME

    shots: list[str] = []
    harness.screen.keys = [
        FACE,
        -1,
        lambda: harness.events.feed(*woke(1)),
        -1,
        lambda: harness.events.feed(heard("time", 2)),
        *[frame] * 80,
        lambda: shots.append(harness.screen.text()),
        lambda: harness.events.feed(*woke(3)),
        frame,
        lambda: shots.append(harness.screen.text()),
        ESC,
        "q",
    ]
    assert harness.run() == 0
    assert "┌─ time " in shots[0]
    assert "┌─ Map " in shots[1]


def test_on_the_nuc_a_wake_and_echo_run_the_echo_on_the_screen(
    tmp_path: Path,
) -> None:
    harness = make_harness(
        tmp_path, [], hostname="nuc", rows=40, cols=120, config_text=ECHO_ONLY
    )
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
