"""`autobot.agents.confirm`: a question asked aloud, answered yes, no or not at all."""

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np

from autobot.agents.confirm import NEITHER, Answer, Confirmation, answer
from autobot.audio_check import DeviceKind
from autobot.config import WordsConfig, parse_config
from autobot.tui.audio import BlockHandler, Clip, InputStream
from autobot.tui.face import FaceContext, FaceModel
from autobot.tui.face_reactions import ReactionSet
from autobot.tui.router import LISTEN_SECONDS
from autobot.tui.runner import CommandResult, FunctionTask, Task, listener_hold_argv

AT = "2026-11-27T07:00:00.000+00:00"
FRAME = 0.1
QUESTION = "This may cost a few dollars. Are you sure you want to run this?"
# The clip the fake voice says the question in.
SPOKEN = Clip(np.zeros(16000, dtype=np.int16), 16000)
CONFIG = parse_config(
    tomllib.loads(
        """
[host]
nuc_hostname = "nuc"

[audio]
input_device = "pipewire"
output_device = "pipewire"

[[wake_phrases]]
phrase = "autobot"
kind = "text_match"
target = "ack"
"""
    )
)
WORDS = CONFIG.words


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class FakeAudio:
    """An output device, `pipewire`, that plays nothing and records what it was given."""

    def __init__(self) -> None:
        self.played: list[Clip] = []

    def query_devices(self) -> list[Mapping[str, Any]]:
        return [{"name": "pipewire", "max_input_channels": 2, "max_output_channels": 2}]

    def supports(self, index: int, kind: DeviceKind, sample_rate: int) -> bool:
        return True

    def open_input(
        self, index: int, sample_rate: int, block_samples: int, on_block: BlockHandler
    ) -> InputStream:
        raise AssertionError("a confirmation opens no microphone")

    def play(self, clip: Clip, index: int) -> None:
        self.played.append(clip)

    def stop_playback(self) -> None:
        pass


class DoneTask:
    def poll(self) -> CommandResult | None:
        return CommandResult(0, "ok\n")

    def cancel(self) -> None:
        pass


class FakeRunner:
    """Records the commands started, the listener's hold among them, and answers ok."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def start(
        self, argv: Any, *, cwd: Path | None = None, timeout: float = 60.0
    ) -> Task:
        self.calls.append(list(argv))
        return DoneTask()


class Asks:
    """An experience that only asks `QUESTION` and ends with its outcome."""

    def __init__(self, words: WordsConfig) -> None:
        self.confirmation = Confirmation(QUESTION, words)

    @property
    def reactions(self) -> ReactionSet | None:
        return None

    @property
    def done(self) -> bool:
        return self.confirmation.done

    def start(self, face: FaceContext) -> None:
        pass

    def tick(self, face: FaceContext) -> None:
        self.confirmation.tick(face)


class Rig:
    """`FaceModel` running one confirmation on fakes and a fake clock."""

    def __init__(self, tmp_path: Path, *, error: Exception | None = None) -> None:
        self.clock = FakeClock()
        self.audio = FakeAudio()
        self.runner = FakeRunner()
        self.renders: list[str] = []
        self.asks: list[Asks] = []
        self.stretch = 0

        def render(text: str, voice: str, models_root: Path) -> Clip:
            self.renders.append(text)
            if error is not None:
                raise error
            return SPOKEN

        def make() -> Asks:
            made = Asks(WORDS)
            self.asks.append(made)
            return made

        self.model = FaceModel(
            CONFIG,
            experience=make,
            audio=self.audio,
            runner=self.runner,
            render=render,
            spawn=lambda function: FunctionTask(function, inline=True),
            repo=tmp_path,
            models_root=tmp_path,
            hostname="nuc",
            clock=self.clock,
        )

    @property
    def confirmation(self) -> Confirmation:
        return self.asks[-1].confirmation

    def wake(self) -> None:
        self.model.feed(
            [
                {
                    "event": "wake",
                    "phrase": "autobot",
                    "target": "ack",
                    "kind": "text_match",
                    "score": 1.0,
                    "at": AT,
                }
            ]
        )

    def say(self, text: str) -> None:
        self.stretch += 1
        self.model.feed(
            [
                {
                    "event": "transcript",
                    "phrase": "autobot",
                    "kind": "text_match",
                    "text": text,
                    "stretch": self.stretch,
                    "at": AT,
                }
            ]
        )

    def frames(self, seconds: float) -> None:
        for _ in range(round(seconds / FRAME)):
            self.clock.now += FRAME
            self.model.tick()

    def asked(self) -> None:
        """Frames until the question has been spoken."""
        self.wake()
        for _ in range(100):
            if not self.confirmation.asking:
                return
            self.frames(FRAME)
        raise AssertionError("the question was never spoken")


def test_a_yes_word_a_no_word_and_an_action_keyword_answer() -> None:
    assert answer("you betcha", WORDS) is Answer.YES
    assert answer("Sure, go ahead.", WORDS) is Answer.YES
    assert answer("nah", WORDS) is Answer.NO
    assert answer("cancel", WORDS) is Answer.NO
    assert answer("maybe later", WORDS) is Answer.NOTHING
    assert answer("nothing", WORDS) is Answer.NOTHING


def test_a_no_wins_over_a_yes_in_the_same_breath() -> None:
    assert answer("yes, no, never mind", WORDS) is Answer.NO
    assert answer("okay, cancel it", WORDS) is Answer.NO


def test_the_lists_are_the_configured_ones() -> None:
    words = WordsConfig(yes=("roger",), no=("scrub",), actions=("abort",))
    assert answer("roger that", words) is Answer.YES
    assert answer("yes", words) is Answer.NOTHING
    assert answer("abort", words) is Answer.NO


def test_the_question_is_spoken_once_holding_the_listener(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.asked()
    assert rig.model.summary.texts == [QUESTION]
    assert rig.renders == [QUESTION]
    assert rig.audio.played == [SPOKEN]
    assert rig.runner.calls == [listener_hold_argv(SPOKEN.seconds)]
    assert not rig.model.ticker.muted
    assert rig.model.eyes.name == "listening"
    # Waiting asks nothing again.
    rig.frames(3.0)
    assert rig.renders == [QUESTION]
    assert len(rig.audio.played) == 1
    assert len(rig.runner.calls) == 1
    assert not rig.confirmation.done


def test_sure_ends_it_yes(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.asked()
    rig.say("sure")
    rig.frames(FRAME)
    assert rig.confirmation.outcome == "yes"
    assert not rig.model.running


def test_nope_ends_it_no(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.asked()
    rig.say("Nope.")
    rig.frames(FRAME)
    assert rig.confirmation.outcome == "no"


def test_a_word_that_is_neither_counts_for_nothing(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.asked()
    rig.say("maybe later")
    rig.frames(FRAME)
    assert not rig.confirmation.done
    assert rig.model.detail.lines[-1] == NEITHER.format(text="maybe later")
    rig.say("yep")
    rig.frames(FRAME)
    assert rig.confirmation.outcome == "yes"


def test_ten_silent_seconds_time_it_out(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.asked()
    rig.frames(LISTEN_SECONDS - 0.5)
    assert not rig.confirmation.done
    # Ten seconds, and one frame for the sums of a tenth that fall just short.
    rig.frames(0.5 + FRAME)
    assert rig.confirmation.outcome == "timed out"
    assert not rig.model.running


def test_what_was_heard_before_the_question_was_spoken_does_not_answer(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)
    rig.wake()
    rig.model.context.heard.append("yes")
    rig.frames(FRAME)
    assert rig.confirmation.asking
    rig.asked()
    rig.frames(FRAME)
    assert not rig.confirmation.done


def test_a_question_that_cannot_be_spoken_is_shown_and_still_answered(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path, error=OSError("the voice is missing"))
    rig.asked()
    assert rig.model.summary.texts == [QUESTION]
    assert rig.model.detail.lines[-1] == "question: the voice is missing"
    assert rig.audio.played == []
    rig.say("no")
    rig.frames(FRAME)
    assert rig.confirmation.outcome == "no"
