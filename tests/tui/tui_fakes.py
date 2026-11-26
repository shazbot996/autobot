"""Fakes for the terminal user interface tests: no terminal, no audio, no commands."""

from __future__ import annotations

import curses
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from autobot.audio_check import DeviceKind
from autobot.tui.app import App, Services
from autobot.tui.audio import BlockHandler, Clip, InputStream, Samples
from autobot.tui.config_edit import ConfigStore
from autobot.tui.pipewire import PipeWire
from autobot.tui.runner import BackgroundJob, CommandResult, Task
from autobot.tui.ui import Glyphs, Theme

CONFIG_TEXT = """\
# autobot configuration: the comments here must survive every write.

[host]
# Hostname of the Intel NUC.
nuc_hostname = "nuc"

[audio]
# "pipewire" follows PipeWire's default source and sink.
input_device = "pipewire"
output_device = "pipewire"  # the speaker
sample_rate = 16000
frame_ms = 80

[[wake_phrases]]
phrase = "autobot"
# kind is the detector that implements the phrase.
kind = "text_match"
model = ""
# Score from 0 to 1 at which the detector fires.
threshold = 0.5
target = "ack"

[ack]
text = "Yes?"
voice = "en_US-lessac-medium"
"""

# The shape of `sounddevice.query_devices()` on the NUC.
DEVICES: list[Mapping[str, Any]] = [
    {
        "name": "HDA Intel PCH: ALC256 Analog (hw:0,0)",
        "max_input_channels": 2,
        "max_output_channels": 2,
    },
    {
        "name": "AIRHUG 21: USB Audio (hw:1,0)",
        "max_input_channels": 2,
        "max_output_channels": 0,
    },
    {"name": "sysdefault", "max_input_channels": 128, "max_output_channels": 128},
    {"name": "pipewire", "max_input_channels": 64, "max_output_channels": 64},
    {"name": "default", "max_input_channels": 64, "max_output_channels": 64},
]
# The hardware devices reject 16 kHz, as on the NUC.
USABLE = (2, 3, 4)

WPCTL_STATUS = """\
PipeWire 'pipewire-0' [1.0.5, owner@nuc, cookie:1]
 └─ Clients:
        32. pipewire                            [1.0.5, owner@nuc, pid:3215]

Audio
 ├─ Devices:
 │      46. Built-in Audio                      [alsa]
 │      47. AIRHUG 21                           [alsa]
 │  
 ├─ Sinks:
 │  *   48. Built-in Audio Analog Stereo        [vol: 1.00]
 │  
 ├─ Sink endpoints:
 │  
 ├─ Sources:
 │  *   33. AIRHUG 21 Analog Stereo             [vol: 0.94]
 │      49. Built-in Audio Analog Stereo        [vol: 1.00]
 │  
 ├─ Source endpoints:
 │  
 └─ Streams:

Video
 ├─ Devices:
 │  
 ├─ Sinks:
 │  
 ├─ Sources:
 │      71. Webcam                              [v4l2]
 │  
 └─ Streams:

Settings
 └─ Default Configured Node Names:
         0. Audio/Sink    auto_null
         1. Audio/Source  alsa_input.usb-Generic_AIRHUG_21-00.analog-stereo
"""

type Key = int | str | Callable[[], None]

ENTER = 10
ESC = 27
BACKSPACE = 127
UP = curses.KEY_UP
DOWN = curses.KEY_DOWN
F1 = curses.KEY_F1
F10 = curses.KEY_F10


class ScriptExhausted(AssertionError):
    """The interface asked for more keys than the test scripted: it did not quit."""


class FakeScreen:
    """A grid of characters with a scripted keyboard.

    Like curses, `addstr` raises when asked to draw off the screen, so a test
    also proves the drawing code clips. Every refresh is kept in `frames`.
    """

    def __init__(self, keys: Sequence[Key], rows: int = 24, cols: int = 80) -> None:
        self.keys: list[Key] = list(keys)
        self.rows = rows
        self.cols = cols
        self.grid: list[list[str]] = []
        self.frames: list[str] = []
        self.erase()

    def getmaxyx(self) -> tuple[int, int]:
        return self.rows, self.cols

    def resize(self, rows: int, cols: int) -> None:
        self.rows, self.cols = rows, cols
        self.erase()

    def erase(self) -> None:
        self.grid = [[" "] * self.cols for _ in range(self.rows)]

    def addstr(self, y: int, x: int, text: str, attr: int = 0) -> None:
        if not (0 <= y < self.rows and 0 <= x and x + len(text) <= self.cols):
            raise curses.error("addstr off the screen")
        self.grid[y][x : x + len(text)] = list(text)
        if y == self.rows - 1 and x + len(text) == self.cols:
            raise curses.error("addstr moved the cursor off the screen")

    def refresh(self) -> None:
        self.frames.append(self.text())

    def text(self) -> str:
        return "\n".join("".join(row).rstrip() for row in self.grid)

    def getch(self) -> int:
        if not self.keys:
            raise ScriptExhausted(f"no more keys; the screen shows:\n{self.text()}")
        key = self.keys.pop(0)
        if callable(key):
            key()
            return -1
        return ord(key) if isinstance(key, str) else key

    def saw(self, text: str) -> bool:
        """Whether any frame so far showed `text`."""
        return any(text in frame for frame in self.frames)


def typed(text: str) -> list[Key]:
    return list(text)


class FakeStream:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeAudio:
    def __init__(self) -> None:
        self.devices = list(DEVICES)
        self.usable = set(USABLE)
        self.streams: list[FakeStream] = []
        self.opened: list[int] = []
        self.on_block: BlockHandler | None = None
        self.played: list[tuple[Clip, int]] = []
        self.stops = 0

    def query_devices(self) -> list[Mapping[str, Any]]:
        return list(self.devices)

    def supports(self, index: int, kind: DeviceKind, sample_rate: int) -> bool:
        return index in self.usable

    def open_input(
        self, index: int, sample_rate: int, block_samples: int, on_block: BlockHandler
    ) -> InputStream:
        stream = FakeStream()
        self.streams.append(stream)
        self.opened.append(index)
        self.on_block = on_block
        return stream

    def play(self, clip: Clip, index: int) -> None:
        self.played.append((clip, index))

    def stop_playback(self) -> None:
        self.stops += 1

    def feed(self, value: int, samples: int = 1280) -> None:
        """Deliver one block of constant samples, as the audio thread would."""
        assert self.on_block is not None, "no input stream is open"
        self.on_block(tone(value, samples))


def tone(value: int, samples: int = 1280) -> Samples:
    return np.full(samples, value, dtype=np.int16)


class DoneTask:
    def __init__(self, result: CommandResult) -> None:
        self.result = result

    def poll(self) -> CommandResult | None:
        return self.result

    def cancel(self) -> None:
        pass


@dataclass
class FakeRunner:
    """Records the commands the interface starts and answers them at once."""

    result: CommandResult = field(default_factory=lambda: CommandResult(0, "ok\n"))
    calls: list[list[str]] = field(default_factory=list)

    def start(
        self, argv: Sequence[str], *, cwd: Path | None = None, timeout: float = 60.0
    ) -> Task:
        self.calls.append(list(argv))
        return DoneTask(self.result)


@dataclass
class Harness:
    app: App
    screen: FakeScreen
    audio: FakeAudio
    runner: FakeRunner
    config_file: Path
    fixture_dir: Path
    wpctl_calls: list[list[str]]

    def run(self) -> int:
        return self.app.run()

    def config_text(self) -> str:
        return self.config_file.read_text(encoding="utf-8")


def make_harness(
    tmp_path: Path,
    keys: Sequence[Key],
    *,
    rows: int = 24,
    cols: int = 80,
    config_text: str = CONFIG_TEXT,
    hostname: str = "testbox",
    available: bool = True,
    train_argv: Sequence[str] = ("true",),
) -> Harness:
    config_file = tmp_path / "autobot.toml"
    config_file.write_text(config_text, encoding="utf-8")
    screen = FakeScreen(keys, rows, cols)
    audio = FakeAudio()
    runner = FakeRunner()
    wpctl_calls: list[list[str]] = []

    def wpctl(argv: Sequence[str]) -> CommandResult:
        wpctl_calls.append(list(argv))
        return CommandResult(0, WPCTL_STATUS if argv[1] == "status" else "")

    services = Services(
        store=ConfigStore(config_file),
        audio=audio,
        pipewire=PipeWire(wpctl),
        runner=runner,
        train_job=BackgroundJob(
            tmp_path / "train.log", tmp_path / "train.pid", "train-marker"
        ),
        train_argv=list(train_argv),
        hostname=hostname,
        repo=tmp_path,
        fixture_dir=tmp_path / "fixtures",
        module_available=lambda name: available,
    )
    app = App(screen, Theme.mono(), Glyphs.unicode(), services)
    return Harness(
        app, screen, audio, runner, config_file, tmp_path / "fixtures", wpctl_calls
    )
