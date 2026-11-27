"""`autobot.tui.face`: the panels, what an experience drives, and the face's model."""

import tomllib
from pathlib import Path
from typing import Any

import pytest
from tui_fakes import (
    CONFIG_TEXT,
    VOICE_FIXTURE,
    FakeAudio,
    FakeRunner,
    FakeVoice,
    fixture_clip,
)

from autobot.audio_check import resolve_device
from autobot.config import Config, parse_config
from autobot.tui.face import (
    DetailPanel,
    FaceContext,
    FaceError,
    FaceModel,
    SummaryLine,
    SummaryPanel,
    prompt_text,
)
from autobot.tui.face_reactions import ReactionSet, reaction_set
from autobot.tui.face_ticker import Cell
from autobot.tui.face_voice import VoiceError
from autobot.tui.runner import FunctionTask, listener_hold_argv

AT = "2026-11-27T05:30:00.000+00:00"
WIDTH = 40
# One frame of the interface, in seconds.
FRAME = 0.1


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class FakeExperience:
    """An experience that says one line, runs for `seconds`, and ends."""

    def __init__(self, seconds: float = 3.0, reactions: ReactionSet | None = None):
        self.seconds = seconds
        self.reactions = reactions
        self.done = False
        self.started_at: float | None = None
        self.ticks = 0
        self.wake_on_start = ""

    def start(self, face: FaceContext) -> None:
        self.started_at = face.clock()
        self.wake_on_start = face.eyes.name
        face.summary.add("I heard you.")
        face.detail.append("started")

    def tick(self, face: FaceContext) -> None:
        self.ticks += 1
        assert self.started_at is not None
        # A frame's worth of float error is not a frame short.
        if face.clock() - self.started_at >= self.seconds - FRAME / 2:
            face.summary.add("Finished.")
            self.done = True


def config(text: str = CONFIG_TEXT) -> Config:
    return parse_config(tomllib.loads(text))


def transcript(text: str, stretch: int) -> dict[str, Any]:
    return {
        "event": "transcript",
        "phrase": "autobot",
        "kind": "text_match",
        "text": text,
        "stretch": stretch,
        "at": AT,
    }


def score(stretch: int) -> dict[str, Any]:
    return {
        "event": "score",
        "phrase": "autobot",
        "kind": "text_match",
        "score": 1.0,
        "stretch": stretch,
        "at": AT,
    }


def wake() -> dict[str, Any]:
    return {
        "event": "wake",
        "phrase": "autobot",
        "target": "ack",
        "kind": "text_match",
        "score": 1.0,
        "at": AT,
    }


def heard(text: str, stretch: int) -> list[dict[str, Any]]:
    """What the listener publishes for a stretch that woke it."""
    return [transcript(text, stretch), score(stretch), wake()]


def popped(cells: list[Cell]) -> str:
    return "".join(cell.char for cell in cells if cell.wake)


class Rig:
    """`FaceModel` on fakes, with the experiences it made."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        hostname: str = "nuc",
        seconds: float = 3.0,
        reactions: ReactionSet | None = None,
    ) -> None:
        self.clock = FakeClock()
        self.audio = FakeAudio()
        self.runner = FakeRunner()
        self.voice = FakeVoice()
        self.made: list[FakeExperience] = []
        self.models_root = tmp_path / "models-root"

        def make() -> FakeExperience:
            experience = FakeExperience(seconds, reactions)
            self.made.append(experience)
            return experience

        self.model = FaceModel(
            config(),
            experience=make,
            audio=self.audio,
            runner=self.runner,
            render=self.voice,
            spawn=lambda function: FunctionTask(function, inline=True),
            repo=tmp_path,
            models_root=self.models_root,
            hostname=hostname,
            clock=self.clock,
        )

    def frames(self, seconds: float) -> None:
        """Run the face for `seconds` of frames."""
        for _ in range(round(seconds / FRAME)):
            self.clock.now += FRAME
            self.model.tick()


# --- the summary panel ----------------------------------------------------------


def test_the_summary_says_what_to_do_before_the_first_wake() -> None:
    panel = SummaryPanel("autobot")
    assert panel.texts == ['Say "autobot" to wake me.']
    assert prompt_text("hey there") == 'Say "hey there" to wake me.'


def test_the_summary_keeps_lines_newest_at_the_bottom() -> None:
    panel = SummaryPanel("autobot")
    panel.clear()
    assert panel.texts == []
    panel.add("I heard you.")
    panel.add("Recording: 5 s left")
    panel.update("Recording: 4 s left")
    panel.trouble("no output device")
    assert list(panel.lines) == [
        SummaryLine("I heard you."),
        SummaryLine("Recording: 4 s left"),
        SummaryLine("no output device", trouble=True),
    ]
    panel.clear()
    panel.update("only line")
    assert panel.texts == ["only line"]


# --- the detail panel -------------------------------------------------------------


def detail_with(count: int) -> DetailPanel:
    model = FaceModel(
        config(),
        experience=FakeExperience,
        audio=FakeAudio(),
        runner=FakeRunner(),
        render=FakeVoice(),
        spawn=lambda function: FunctionTask(function, inline=True),
        repo=Path("."),
        models_root=Path("."),
        hostname="nuc",
        clock=FakeClock(),
    )
    panel = model.detail
    for number in range(1, count + 1):
        panel.append(f"line {number}")
    return panel


def test_the_detail_panel_shows_its_newest_lines() -> None:
    panel = detail_with(10)
    assert panel.shown(3) == ["line 8", "line 9", "line 10"]
    panel.update("line ten")
    assert panel.shown(3)[-1] == "line ten"
    assert panel.shown(0) == []
    panel.clear()
    assert panel.shown(3) == []
    panel.update("first")
    assert panel.shown(3) == ["first"]


def test_the_detail_panel_scrolls_with_the_live_monitors_keys() -> None:
    panel = detail_with(20)
    assert panel.shown(5)[-1] == "line 20"
    panel.up()
    assert panel.shown(5)[-1] == "line 19"
    panel.page_up()
    assert panel.shown(5)[-1] == "line 14"
    panel.page_down()
    panel.down()
    assert panel.shown(5)[-1] == "line 20"
    panel.down()
    assert panel.scroll == 0
    # Never further back than the first line.
    for _ in range(10):
        panel.page_up()
    assert panel.shown(5) == [f"line {n}" for n in range(1, 6)]
    panel.end()
    assert panel.shown(5)[-1] == "line 20"


def test_a_detail_panel_scrolled_back_stays_on_the_lines_it_shows() -> None:
    panel = detail_with(20)
    panel.shown(5)
    panel.up()
    before = panel.shown(5)
    panel.append("line 21")
    panel.append("line 22")
    assert panel.shown(5) == before
    panel.end()
    assert panel.shown(5)[-1] == "line 22"


# --- one experience at a time ----------------------------------------------------


def test_two_wakes_a_second_apart_start_one_experience(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.model.feed(heard("hey autobot", 1))
    rig.frames(1.0)
    rig.model.feed(heard("autobot again", 2))
    rig.frames(1.0)
    assert len(rig.made) == 1
    assert rig.model.experience is rig.made[0]
    # The second wake reached the experience nowhere: it was not started again.
    assert rig.made[0].started_at == 100.0
    assert rig.made[0].ticks == 20
    # Both wakes popped on the ticker, once the second has scrolled in.
    for _ in range(15):
        rig.model.ticker.advance()
    cells = rig.model.ticker.view(WIDTH)
    text = "".join(cell.char for cell in cells)
    assert "hey autobot" in text
    assert "autobot again" in text
    assert popped(cells) == "autobotautobot"


def test_every_wake_plays_the_wake_reaction_at_once(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    assert rig.model.eyes.name == "idle"
    rig.model.feed(heard("hey autobot", 1))
    # Already playing when the experience started.
    assert rig.made[0].wake_on_start == "wake"
    rig.frames(2.0)
    assert rig.model.eyes.name == "idle"
    rig.model.feed(heard("autobot", 2))
    assert rig.model.eyes.name == "wake"
    assert len(rig.made) == 1


def test_the_summary_is_cleared_at_each_sessions_wake(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    assert rig.model.summary.texts == ['Say "autobot" to wake me.']
    rig.model.feed(heard("hey autobot", 1))
    assert rig.model.summary.texts == ["I heard you."]
    rig.frames(3.0)
    # The run is over; its messages stay to be read.
    assert not rig.model.running
    assert rig.model.summary.texts == ["I heard you.", "Finished."]
    assert rig.model.detail.shown(5) == ["started"]
    rig.model.feed(heard("autobot", 2))
    assert len(rig.made) == 2
    assert rig.model.summary.texts == ["I heard you."]
    assert rig.model.detail.shown(5) == ["started"]


def test_an_experiences_own_reactions_are_played(tmp_path: Path) -> None:
    own = reaction_set({"wake": {"poses": [{"pupil": "up left", "hold": 400}]}})
    rig = Rig(tmp_path, reactions=own)
    rig.model.feed(heard("hey autobot", 1))
    assert rig.model.context.reactions is own
    rig.model.tick()
    assert (rig.model.pose().across, rig.model.pose().level) == ("left", "up")
    # A name the set leaves out is the built-in reaction.
    rig.model.context.react("done")
    assert rig.model.eyes.name == "done"
    rig.frames(3.0)
    assert rig.model.context.reactions is None


def test_a_disconnected_stream_puts_the_ticker_down(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.model.feed([], connected=False)
    assert rig.model.ticker.down
    rig.model.feed(heard("hey autobot", 1))
    assert not rig.model.ticker.down
    assert len(rig.made) == 1


# --- what an experience drives -----------------------------------------------------


def test_say_shows_the_line_and_queues_the_same_text_to_be_spoken(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)
    face = rig.model.context
    face.summary.clear()
    face.say("Hey, I'm going to record your voice. Go ahead.")
    assert face.summary.texts == ["Hey, I'm going to record your voice. Go ahead."]
    assert list(face.speech) == ["Hey, I'm going to record your voice. Go ahead."]
    assert rig.voice.calls == [
        (
            "Hey, I'm going to record your voice. Go ahead.",
            "en_US-joe-medium",
            rig.models_root,
        )
    ]
    clip = face.voice.clip("Hey, I'm going to record your voice. Go ahead.")
    assert clip is not None
    assert clip.seconds == fixture_clip(VOICE_FIXTURE).seconds


def test_a_line_is_rendered_once(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    voice = rig.model.voice
    voice.prepare("Go ahead.")
    voice.prepare("Go ahead.")
    assert len(rig.voice.calls) == 1
    assert voice.clip("never asked") is None
    assert voice.error("never asked") == ""


def test_a_voice_that_fails_gives_its_one_line(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.voice.error = VoiceError("piper voice file is missing: x.onnx")
    rig.model.voice.prepare("Go ahead.")
    assert rig.model.voice.clip("Go ahead.") is None
    assert rig.model.voice.error("Go ahead.") == "piper voice file is missing: x.onnx"


def test_a_hold_runs_control_hold_and_mutes_the_ticker(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    face = rig.model.context
    task = face.hold(2.5)
    assert rig.runner.calls == [listener_hold_argv(2.5)]
    assert task.poll() is not None
    assert rig.model.ticker.muted
    rig.model.feed([transcript("what autobot says", 1), score(1)])
    assert "what autobot says" not in rig.model.ticker.text
    face.release()
    assert not rig.model.ticker.muted


def test_playing_a_clip_moves_the_mouth_on_the_output_device(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    face = rig.model.context
    clip = fixture_clip(VOICE_FIXTURE)
    face.play(clip)
    index = resolve_device("pipewire", "output", rig.audio.query_devices())
    assert rig.audio.played == [(clip, index)]
    assert face.playing
    rig.clock.now += 0.5
    assert rig.model.mouth.level() > 0
    face.stop()
    assert rig.audio.stops == 1
    assert not face.playing
    assert rig.model.mouth.level() == 0.0


def test_an_output_device_that_does_not_resolve_is_a_face_error(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)
    rig.audio.devices = []
    with pytest.raises(FaceError, match="output"):
        rig.model.context.play(fixture_clip(VOICE_FIXTURE))
    assert rig.audio.played == []
    assert not rig.model.context.playing


def test_off_the_nuc_the_face_gives_the_usual_line(tmp_path: Path) -> None:
    assert Rig(tmp_path).model.context.refusal("the echo") == ""
    face = Rig(tmp_path, hostname="laptop").model.context
    assert not face.on_nuc
    assert face.refusal("the echo") == (
        "the echo runs on the NUC only; this host is laptop"
    )


def test_the_microphone_feeds_the_capture_the_experience_records_with(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)
    assert rig.model.open()
    rig.audio.feed(8000)
    assert rig.model.mic.state == "live"
    assert rig.model.context.capture is rig.model.mic.capture
    assert rig.model.context.capture.level().peak == 8000


def test_closing_the_face_drops_the_experience_and_stops_its_sound(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)
    assert rig.model.open()
    rig.model.feed(heard("hey autobot", 1))
    face = rig.model.context
    face.capture.start_recording(5.0)
    face.play(fixture_clip(VOICE_FIXTURE))
    face.hold(1.0)
    rig.model.close()
    assert rig.model.experience is None
    assert not face.capture.recording
    # The recording that was stopped is dropped, not kept for later.
    assert face.capture.take_clip() is None
    assert rig.audio.stops == 1
    assert rig.audio.streams[0].closed
    assert not face.playing
    assert not rig.model.ticker.muted
