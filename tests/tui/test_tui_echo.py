"""`autobot.tui.echo`: the face's first experience, on fakes and a fake clock."""

import threading
import time
import tomllib
from pathlib import Path
from typing import Any

import numpy as np
from tui_fakes import (
    CONFIG_TEXT,
    FIXTURES,
    VOICE_FIXTURE,
    FakeAudio,
    FakeRunner,
    FakeVoice,
    fixture_clip,
)

from autobot.audio_check import resolve_device
from autobot.config import parse_config
from autobot.tui.audio import Samples
from autobot.tui.echo import (
    FINISHED,
    HEARD,
    LISTENER_NOT_RUNNING,
    NO_AUDIO,
    PLAYING_BACK,
    RECORD_SECONDS,
    SPEECH_LINE,
    Echo,
    Stage,
    prepare,
)
from autobot.tui.face import FaceModel, SummaryLine
from autobot.tui.face_voice import VoiceError
from autobot.tui.runner import CommandResult, FunctionTask, listener_hold_argv

AT = "2026-11-27T05:40:00.000+00:00"
# One frame of the interface, in seconds, and the audio one frame brings.
FRAME = 0.1
FRAME_SAMPLES = 1600
# The owner's voice, as the microphone delivers it: a committed fixture.
MIC_FIXTURE = FIXTURES / "yo_autobot.wav"
MISSING = (
    "piper voice file is missing: models/piper/en_US-joe-medium.onnx "
    "('make fetch-models' fetches it)"
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def wake(stretch: int) -> list[dict[str, Any]]:
    """What the listener publishes for a stretch that woke it."""
    return [
        {
            "event": "transcript",
            "phrase": "autobot",
            "kind": "text_match",
            "text": "hey autobot",
            "stretch": stretch,
            "at": AT,
        },
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


class Rig:
    """`FaceModel` running the echo on fakes, with a microphone fed from a fixture."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        hostname: str = "nuc",
        spawn: Any = None,
    ) -> None:
        self.repo = tmp_path / "repo"
        self.repo.mkdir()
        self.clock = FakeClock()
        self.audio = FakeAudio()
        self.runner = FakeRunner()
        self.voice = FakeVoice()
        self.echoes: list[Echo] = []
        self.mic = fixture_clip(MIC_FIXTURE).samples
        self.mic_at = 0
        # Where in the microphone's audio the recording began.
        self.recorded_from: int | None = None

        def make() -> Echo:
            echo = Echo()
            self.echoes.append(echo)
            return echo

        self.model = FaceModel(
            parse_config(tomllib.loads(CONFIG_TEXT)),
            experience=make,
            audio=self.audio,
            runner=self.runner,
            render=self.voice,
            spawn=spawn or (lambda function: FunctionTask(function, inline=True)),
            repo=self.repo,
            models_root=self.repo,
            hostname=hostname,
            clock=self.clock,
        )

    @property
    def echo(self) -> Echo:
        return self.echoes[-1]

    def open(self) -> None:
        """The screen opens on the NUC: the microphone, and the line's render."""
        assert self.model.open()
        prepare(self.model.voice)

    def block(self) -> Samples:
        """The next frame of the microphone fixture, round and round."""
        indexes = np.arange(self.mic_at, self.mic_at + FRAME_SAMPLES) % len(self.mic)
        self.mic_at += FRAME_SAMPLES
        return self.mic[indexes]

    def frame(self, *, audio: bool = True) -> None:
        self.clock.now += FRAME
        if audio and self.audio.on_block is not None:
            self.audio.on_block(self.block())
        self.model.tick()
        if self.recorded_from is None and self.model.context.capture.recording:
            self.recorded_from = self.mic_at

    def run(self, limit: float = 30.0) -> list[tuple[Stage, float, bool]]:
        """Frames until the echo ends: each frame's stage, mouth level and mute."""
        seen: list[tuple[Stage, float, bool]] = []
        for _ in range(round(limit / FRAME)):
            if not self.model.running:
                break
            self.frame()
            seen.append(
                (self.echo.stage, self.model.mouth.level(), self.model.ticker.muted)
            )
        return seen


def files_under(path: Path) -> set[Path]:
    return set(path.rglob("*"))


def test_the_echo_runs_its_five_steps_in_order(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    before = files_under(tmp_path)
    rig.open()
    rig.model.feed(wake(1))
    assert rig.echo.stage == "woken"
    assert rig.model.summary.texts == [HEARD]
    assert rig.model.eyes.name == "wake"
    seen = rig.run()
    assert not rig.model.running
    assert rig.echo.done
    stages: list[Stage] = []
    for stage, _, _ in seen:
        if not stages or stages[-1] != stage:
            stages.append(stage)
    assert stages == ["speaking", "recording", "playing back", "done"]

    # Two holds: one the length of the line, one the length of the recording.
    speech = fixture_clip(VOICE_FIXTURE)
    assert rig.runner.calls == [
        listener_hold_argv(speech.seconds),
        listener_hold_argv(RECORD_SECONDS),
    ]
    # The line, then five seconds of the microphone, on the output device.
    output = resolve_device("pipewire", "output", rig.audio.query_devices())
    assert [index for _, index in rig.audio.played] == [output, output]
    spoken, played_back = (clip for clip, _ in rig.audio.played)
    assert np.array_equal(spoken.samples, speech.samples)
    assert played_back.seconds == RECORD_SECONDS
    assert rig.recorded_from is not None
    heard = np.arange(rig.recorded_from, rig.recorded_from + len(played_back.samples))
    assert np.array_equal(played_back.samples, rig.mic[heard % len(rig.mic)])

    # The mouth moves during the line and the playback, and is flat after.
    for moving in ("speaking", "playing back"):
        assert max(level for stage, level, _ in seen if stage == moving) > 0
    assert all(level == 0.0 for stage, level, _ in seen if stage == "recording")
    assert rig.model.mouth.level() == 0.0
    # The ticker is muted while autobot makes sound, and only then.
    assert all(muted for stage, _, muted in seen if stage == "speaking")
    assert not any(muted for stage, _, muted in seen if stage == "recording")
    assert not rig.model.ticker.muted

    texts = rig.model.summary.texts
    assert texts[:2] == [HEARD, SPEECH_LINE]
    assert texts[-2:] == [PLAYING_BACK, FINISHED]
    assert rig.model.detail.shown(5)[-1].startswith("recorded 5.0 s of 5.0 s, level ")
    assert rig.model.eyes.name == "done"
    # The recording went to no file: not in the repository, not in a dataset.
    assert files_under(tmp_path) == before


def test_the_summary_counts_the_recording_down(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.open()
    rig.model.feed(wake(1))
    lefts: list[str] = []
    details: list[str] = []
    while rig.model.running:
        rig.frame()
        if rig.echo.stage == "recording":
            assert rig.model.eyes.name == "listening"
            lefts.append(rig.model.summary.texts[-1])
            details.append(rig.model.detail.shown(1)[-1])
    shown = list(dict.fromkeys(lefts))
    assert shown == [f"Recording: {n} s left" for n in (5, 4, 3, 2, 1)]
    assert "recorded 2.4 s of 5.0 s" in " ".join(details)
    assert all(line.endswith(" dBFS") for line in details)


def test_the_speech_line_is_queued_and_spoken_as_shown(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.open()
    rig.model.feed(wake(1))
    rig.frame()
    assert rig.echo.stage == "speaking"
    assert rig.model.eyes.name == "speaking"
    # Shown in the summary, and taken off the queue as it was spoken.
    assert rig.model.summary.texts == [HEARD, SPEECH_LINE]
    assert list(rig.model.context.speech) == []
    # Rendered once, when the face opened.
    assert [text for text, _, _ in rig.voice.calls] == [SPEECH_LINE]


def test_a_missing_voice_ends_the_echo_in_trouble(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.voice.error = VoiceError(MISSING)
    rig.open()
    rig.model.feed(wake(1))
    rig.frame()
    assert list(rig.model.summary.lines) == [
        SummaryLine(HEARD),
        SummaryLine(MISSING, trouble=True),
    ]
    assert rig.model.eyes.name == "trouble"
    assert not rig.model.running
    assert rig.runner.calls == []
    assert rig.audio.played == []
    # The face stays open, and the next wake starts a new echo.
    rig.model.feed(wake(2))
    assert len(rig.echoes) == 2


def test_the_echo_waits_for_a_line_still_rendering(tmp_path: Path) -> None:
    ready = threading.Event()

    def slow_spawn(function: Any) -> FunctionTask:
        def later() -> object:
            ready.wait(5.0)
            return function()

        return FunctionTask(later)

    rig = Rig(tmp_path, spawn=slow_spawn)
    rig.open()
    rig.model.feed(wake(1))
    for _ in range(5):
        rig.frame()
    assert rig.echo.stage == "speaking"
    assert rig.audio.played == []
    assert rig.runner.calls == []
    ready.set()
    deadline = time.monotonic() + 5.0
    while not rig.audio.played and time.monotonic() < deadline:
        time.sleep(0.01)
        rig.frame()
    assert len(rig.audio.played) == 1
    assert len(rig.runner.calls) == 1


def test_off_the_nuc_the_echo_gives_the_usual_line(tmp_path: Path) -> None:
    rig = Rig(tmp_path, hostname="laptop")
    rig.model.feed(wake(1))
    rig.frame()
    assert rig.model.summary.lines[-1] == SummaryLine(
        "the echo runs on the NUC only; this host is laptop", trouble=True
    )
    assert not rig.model.running
    assert rig.voice.calls == []
    assert rig.audio.played == []


def test_a_microphone_that_did_not_open_ends_the_echo(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.model.feed(wake(1))
    rig.frame()
    assert rig.model.summary.lines[-1] == SummaryLine(
        "the microphone is not open", trouble=True
    )
    assert not rig.model.running


def test_a_missing_output_device_ends_the_echo(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.open()
    rig.audio.devices = [
        device for device in rig.audio.devices if device["name"] != "pipewire"
    ]
    rig.model.feed(wake(1))
    rig.frame()
    last = rig.model.summary.lines[-1]
    assert last.trouble
    assert "pipewire" in last.text
    assert not rig.model.running
    assert rig.audio.played == []


def test_a_listener_that_is_not_running_is_not_trouble(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.runner.result = CommandResult(1, LISTENER_NOT_RUNNING + "\n")
    rig.open()
    rig.model.feed(wake(1))
    rig.run()
    assert rig.model.summary.texts[-1] == FINISHED
    assert not any(line.startswith("hold:") for line in rig.model.detail.lines)
    assert len(rig.audio.played) == 2


def test_another_hold_failure_is_a_detail_line(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.runner.result = CommandResult(
        1, "the listener did not take the hold within 5 s\n"
    )
    rig.open()
    rig.model.feed(wake(1))
    rig.run()
    assert rig.model.summary.texts[-1] == FINISHED
    holds = [line for line in rig.model.detail.lines if line.startswith("hold:")]
    assert holds == ["hold: the listener did not take the hold within 5 s"] * 2
    assert len(rig.audio.played) == 2


def test_a_microphone_that_delivers_nothing_ends_the_recording(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.open()
    rig.model.feed(wake(1))
    for _ in range(200):
        if not rig.model.running:
            break
        # The stream is open but no audio arrives.
        rig.frame(audio=False)
    assert not rig.model.running
    assert rig.model.summary.lines[-1] == SummaryLine(NO_AUDIO, trouble=True)
    assert not rig.model.context.capture.recording
    assert len(rig.audio.played) == 1


def test_closing_the_face_mid_echo_drops_the_recording(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    before = files_under(tmp_path)
    rig.open()
    rig.model.feed(wake(1))
    while rig.model.running and rig.echo.stage != "recording":
        rig.frame()
    for _ in range(10):
        rig.frame()
    assert rig.model.context.capture.recording
    rig.model.close()
    assert not rig.model.running
    assert not rig.model.context.capture.recording
    assert rig.model.context.capture.take_clip() is None
    assert len(rig.audio.played) == 1
    assert files_under(tmp_path) == before
