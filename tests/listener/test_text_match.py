"""`autobot.listener.engines.text_match`: matching rule, buffering, fixtures."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest

from autobot.config import AudioConfig, ConfigError, WakePhraseConfig
from autobot.listener import detector as detector_module
from autobot.listener.detector import (
    Detector,
    Frame,
    UnknownKindError,
    WakeEvent,
    build_detector,
)
from autobot.listener.engines.text_match import (
    TextMatchDetector,
    Transcript,
    WhisperTranscriber,
    consonant_skeleton,
    contains_phrase,
    normalise,
    sounds_like,
)
from autobot.listener.models import ModelMissingError
from autobot.listener.service import Pipeline

from .conftest import (
    FIXED_TIME,
    FRAME_SAMPLES,
    NEGATIVE_CLIPS,
    POSITIVE_CLIPS,
    run_clip,
    silence,
)

AUDIO = AudioConfig(input_device="pipewire", output_device="pipewire")
FRAMES_PER_SECOND = 16000 // FRAME_SAMPLES  # 12.5, rounded down to 12


class FakeTranscriber:
    """A `Transcriber` that returns scripted transcripts and records its calls.

    Hinted calls take the scripted transcripts in order. An unhinted call (the
    confirmation pass) gets `unhinted` when it is set, else the text of the
    hinted transcript it follows.
    """

    def __init__(self, *transcripts: Transcript, unhinted: str | None = None):
        self._transcripts = list(transcripts)
        self._unhinted = unhinted
        self._last = Transcript(text="", score=0.0)
        # (samples, hint) of every hinted call; sample counts of the unhinted ones.
        self.calls: list[tuple[int, str]] = []
        self.confirmations: list[int] = []

    def transcribe(self, samples: npt.NDArray[np.float32], hint: str) -> Transcript:
        if not hint:
            self.confirmations.append(len(samples))
            text = self._last.text if self._unhinted is None else self._unhinted
            return Transcript(text=text, score=0.5)
        self.calls.append((len(samples), hint))
        if self._transcripts:
            self._last = self._transcripts.pop(0)
        else:
            self._last = Transcript(text="", score=0.0)
        return self._last


def detector_with(
    transcriber: FakeTranscriber, threshold: float = 0.5
) -> TextMatchDetector:
    return TextMatchDetector(
        "autobot", "ack", transcriber, threshold=threshold, clock=lambda: FIXED_TIME
    )


def feed(detector: Detector, frames: int) -> list[WakeEvent]:
    events = [detector.feed(frame) for frame in silence(frames)]
    return [event for event in events if event is not None]


# --- the matching rule -------------------------------------------------------


def test_normalise_lowers_case_and_drops_punctuation() -> None:
    assert normalise("  Hey, Auto-Bot!  It's  me. ") == "hey auto bot its me"


@pytest.mark.parametrize(
    "transcript",
    [
        "autobot",
        "Autobot.",
        "Hey, Autobot!",
        "yo autobot",
        "Hey auto bot.",
        "hey auto-bot",
        "AUTO  BOT",
        "So I said autobot, what time is it?",
        "the autobots are here",
        "autobot's turn",
    ],
)
def test_contains_phrase_matches(transcript: str) -> None:
    assert contains_phrase(transcript, "autobot")


@pytest.mark.parametrize(
    "transcript",
    [
        "",
        "auto",
        "Auto.",
        "hey auto",
        "auto parts",
        "a robot",
        "bot auto",
        "or to bolt",
        # The letters are there, but not from the start of a word.
        "plateauto bot",
        "The weather tomorrow looks fine for a walk in the park.",
    ],
)
def test_contains_phrase_does_not_match(transcript: str) -> None:
    assert not contains_phrase(transcript, "autobot")


def test_contains_phrase_takes_a_phrase_of_several_words() -> None:
    assert contains_phrase("OK, hey there computer!", "Hey there, computer")
    assert not contains_phrase("hey computer", "hey there computer")
    assert not contains_phrase("anything", " ")


def test_consonant_skeleton_folds_spelling_onto_rough_sounds() -> None:
    assert consonant_skeleton("autobot") == "tpt"
    assert consonant_skeleton("Auto butt.") == "tpt"
    assert consonant_skeleton("Hey, what about?") == "tpt"
    assert consonant_skeleton("or two") == "rt"


@pytest.mark.parametrize(
    "transcript",
    [
        "Autobot.",
        "or to bolt",
        "Auto butt.",
        "a lot of both",
        "Autopot",
        "Yo water bot",
    ],
)
def test_sounds_like_accepts_what_whisper_writes_for_the_phrase(
    transcript: str,
) -> None:
    assert sounds_like(transcript, "autobot")


@pytest.mark.parametrize(
    "transcript",
    [
        "",
        "Auto.",
        "Otto.",
        "or two.",
        "Bye.",
        "Hey, bottle.",
        "Oh, that's it",
        "Law 2.",
    ],
)
def test_sounds_like_rejects_what_whisper_writes_for_a_near_miss(
    transcript: str,
) -> None:
    assert not sounds_like(transcript, "autobot")


# --- buffering, with a fake transcriber --------------------------------------


def test_nothing_is_transcribed_until_speech_ends() -> None:
    transcriber = FakeTranscriber(Transcript("hey autobot", 0.8))
    detector = detector_with(transcriber)
    assert feed(detector, 10) == []
    assert transcriber.calls == []
    event = detector.end_of_speech()
    assert transcriber.calls == [(10 * FRAME_SAMPLES, "autobot")]
    assert event == WakeEvent(
        phrase="autobot", target="ack", kind="text_match", score=0.8, at=FIXED_TIME
    )


def test_a_transcript_without_the_phrase_yields_nothing() -> None:
    detector = detector_with(FakeTranscriber(Transcript("auto", 0.9)))
    feed(detector, 10)
    assert detector.end_of_speech() is None


def test_a_match_below_the_threshold_yields_nothing() -> None:
    detector = detector_with(FakeTranscriber(Transcript("autobot", 0.4)), threshold=0.5)
    feed(detector, 10)
    assert detector.end_of_speech() is None


def test_a_match_is_confirmed_by_a_second_pass_without_the_hint() -> None:
    transcriber = FakeTranscriber(Transcript("autobot", 0.9), unhinted="or to bolt")
    detector = detector_with(transcriber)
    feed(detector, 10)
    assert detector.end_of_speech() is not None
    assert transcriber.confirmations == [10 * FRAME_SAMPLES]


def test_a_match_the_unhinted_pass_does_not_back_up_yields_nothing() -> None:
    # The hint talked whisper into "autobot"; without it, it heard "Auto."
    transcriber = FakeTranscriber(Transcript("autobot", 0.9), unhinted="Auto.")
    detector = detector_with(transcriber)
    feed(detector, 10)
    assert detector.end_of_speech() is None
    assert transcriber.confirmations == [10 * FRAME_SAMPLES]


def test_the_second_pass_runs_only_on_a_match() -> None:
    transcriber = FakeTranscriber(Transcript("what a nice day", 0.9))
    detector = detector_with(transcriber)
    feed(detector, 10)
    assert detector.end_of_speech() is None
    assert transcriber.confirmations == []


def test_a_blip_shorter_than_the_minimum_is_not_transcribed() -> None:
    transcriber = FakeTranscriber()
    detector = detector_with(transcriber)
    feed(detector, 3)  # 0.24 s
    assert detector.end_of_speech() is None
    assert transcriber.calls == []


def test_long_speech_is_transcribed_at_the_two_second_cap_with_overlap() -> None:
    transcriber = FakeTranscriber(
        Transcript("so anyway", 0.9), Transcript("anyway autobot", 0.9)
    )
    detector = detector_with(transcriber)
    # 25 frames are exactly two seconds: the cap.
    assert feed(detector, 24) == []
    assert transcriber.calls == []
    assert feed(detector, 1) == []
    assert transcriber.calls == [(32000, "autobot")]
    # One second is kept; the next cap comes after one more second of audio.
    events = feed(detector, 13)
    assert len(transcriber.calls) == 2
    assert transcriber.calls[1][0] == 16000 + 13 * FRAME_SAMPLES
    assert [event.phrase for event in events] == ["autobot"]


def test_one_utterance_yields_one_event_when_the_cap_fires_mid_speech() -> None:
    transcriber = FakeTranscriber(
        Transcript("hey autobot", 0.9), Transcript("autobot again", 0.9)
    )
    detector = detector_with(transcriber)
    events = feed(detector, 25)
    assert len(events) == 1
    # The rest of the same stretch of speech is ignored, and so is its end.
    assert feed(detector, 12) == []
    assert detector.end_of_speech() is None
    assert len(transcriber.calls) == 1
    # The next stretch of speech is heard again.
    feed(detector, 10)
    assert detector.end_of_speech() is not None


def test_the_refractory_period_runs_out_during_very_long_speech() -> None:
    transcriber = FakeTranscriber(
        Transcript("autobot", 0.9), Transcript("autobot", 0.9)
    )
    detector = detector_with(transcriber)
    assert len(feed(detector, 25)) == 1
    # Two seconds are ignored, then two more fill the buffer again.
    assert len(feed(detector, 25 + 25)) == 1


def test_no_second_transcription_at_the_end_when_nothing_new_was_said() -> None:
    transcriber = FakeTranscriber(Transcript("so anyway", 0.9))
    detector = detector_with(transcriber)
    feed(detector, 25)
    feed(detector, 2)  # less than the minimum since the capped transcription
    assert detector.end_of_speech() is None
    assert len(transcriber.calls) == 1


def test_reset_forgets_buffered_audio() -> None:
    transcriber = FakeTranscriber(Transcript("autobot", 0.9))
    detector = detector_with(transcriber)
    feed(detector, 10)
    detector.reset()
    assert detector.end_of_speech() is None
    assert transcriber.calls == []


def test_text_match_refuses_a_sample_rate_whisper_cannot_take() -> None:
    with pytest.raises(ConfigError, match="16000 Hz"):
        TextMatchDetector("autobot", "ack", FakeTranscriber(), sample_rate=8000)


def test_missing_whisper_model_is_one_clear_line(tmp_path: Path) -> None:
    with pytest.raises(ModelMissingError) as error:
        WhisperTranscriber(model_dir=tmp_path)
    assert "faster-whisper model file is missing" in str(error.value)
    assert "never downloaded" in str(error.value)
    assert "\n" not in str(error.value)


# --- the factory -------------------------------------------------------------


def test_build_detector_finds_the_engine_module_by_kind(models: None) -> None:
    phrase = WakePhraseConfig(phrase="autobot", kind="text_match", target="ack")
    assert isinstance(build_detector(phrase, AUDIO), TextMatchDetector)


def test_build_detector_names_a_kind_with_no_engine() -> None:
    phrase = WakePhraseConfig(phrase="autobot", kind="no_such_kind", target="ack")
    with pytest.raises(UnknownKindError, match="no_such_kind"):
        build_detector(phrase, AUDIO)


def test_a_registered_builder_wins() -> None:
    class Never:
        def feed(self, frame: Frame) -> WakeEvent | None:
            return None

        def end_of_speech(self) -> WakeEvent | None:
            return None

        def reset(self) -> None:
            return None

    never = Never()
    detector_module.register("fake_kind", lambda phrase, audio: never)
    try:
        phrase = WakePhraseConfig(phrase="autobot", kind="fake_kind", target="ack")
        assert build_detector(phrase, AUDIO) is never
    finally:
        detector_module.unregister("fake_kind")


# --- the fixtures, through the service's frame path --------------------------


@pytest.mark.parametrize("clip", POSITIVE_CLIPS)
def test_a_wake_clip_yields_exactly_one_event(
    text_match_pipeline: Pipeline, clip: str
) -> None:
    events = run_clip(text_match_pipeline, clip)
    assert len(events) == 1, f"{clip}: {events}"
    event = events[0]
    assert (event.phrase, event.target, event.kind) == ("autobot", "ack", "text_match")
    assert 0.5 <= event.score <= 1.0
    assert event.at.tzinfo is not None


@pytest.mark.parametrize("clip", NEGATIVE_CLIPS)
def test_a_clip_without_the_phrase_yields_no_event(
    text_match_pipeline: Pipeline, clip: str
) -> None:
    assert run_clip(text_match_pipeline, clip) == []


def test_clips_played_back_to_back_yield_one_event_each(
    text_match_pipeline: Pipeline,
) -> None:
    order = ("hey_autobot", "unrelated_speech", "yo_autobot", "near_miss_auto")
    counts = [len(run_clip(text_match_pipeline, clip)) for clip in order]
    assert counts == [1, 0, 1, 0]
