"""`autobot.listener.engines.openwakeword`: firing rule, scorer, margins, fixtures."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from autobot.config import AudioConfig, ConfigError, WakePhraseConfig
from autobot.listener.detector import Frame, WakeEvent, build_detector
from autobot.listener.engines.openwakeword import (
    FRAME_SAMPLES,
    GATE_MARGINS,
    OnnxScorer,
    OpenWakeWordDetector,
)
from autobot.listener.models import ModelMissingError
from autobot.listener.service import Pipeline
from autobot.listener.vad import SpeechGate

from .conftest import (
    FIXED_TIME,
    NEGATIVE_CLIPS,
    POSITIVE_CLIPS,
    WAKEWORD_MODEL,
    load_clip,
    run_clip,
    silence,
)

AUDIO = AudioConfig(input_device="pipewire", output_device="pipewire")


class FakeScorer:
    """A `Scorer` that returns scripted scores, then zeros, and counts resets."""

    def __init__(self, *scores: float):
        self._scores = list(scores)
        self.fed = 0
        self.resets = 0

    def score(self, frame: Frame) -> float:
        self.fed += 1
        return self._scores.pop(0) if self._scores else 0.0

    def reset(self) -> None:
        self.resets += 1


def detector_with(
    scorer: FakeScorer, threshold: float = 0.5, refractory_seconds: float = 0.4
) -> OpenWakeWordDetector:
    # 0.4 s is five 80 ms frames.
    return OpenWakeWordDetector(
        "autobot",
        "ack",
        scorer,
        threshold=threshold,
        refractory_seconds=refractory_seconds,
        clock=lambda: FIXED_TIME,
    )


def feed(detector: OpenWakeWordDetector, frames: int) -> list[WakeEvent | None]:
    return [detector.feed(frame) for frame in silence(frames)]


# --- the firing rule ---------------------------------------------------------


def test_it_fires_when_the_score_reaches_the_threshold() -> None:
    detector = detector_with(FakeScorer(0.1, 0.49, 0.5, 0.2))
    events = feed(detector, 4)
    assert [event is not None for event in events] == [False, False, True, False]
    assert events[2] == WakeEvent("autobot", "ack", "openwakeword", 0.5, FIXED_TIME)


def test_the_event_carries_the_models_score() -> None:
    detector = detector_with(FakeScorer(0.98765), threshold=0.9)
    (event,) = feed(detector, 1)
    assert event is not None
    assert event.score == 0.988
    assert event.kind == "openwakeword"


def test_one_utterance_yields_one_event() -> None:
    # The score stays high for several frames after the phrase ends.
    detector = detector_with(FakeScorer(0.9, 0.95, 0.99, 0.9, 0.8, 0.7))
    events = feed(detector, 8)
    assert [event is not None for event in events] == [True] + [False] * 7


def test_it_fires_again_once_the_refractory_period_is_over() -> None:
    # Fires on frame 0; frames 1-5 are refractory; frame 6 fires again.
    detector = detector_with(FakeScorer(*[0.9] * 8))
    events = feed(detector, 8)
    assert [index for index, event in enumerate(events) if event] == [0, 6]


def test_every_frame_is_scored_even_while_refractory() -> None:
    # The scorer's buffers must follow the audio whether or not it may fire.
    scorer = FakeScorer(0.9)
    feed(detector_with(scorer), 4)
    assert scorer.fed == 4


def test_a_pause_does_not_let_one_utterance_fire_twice() -> None:
    scorer = FakeScorer(0.9, 0.9, 0.9, 0.9)
    detector = detector_with(scorer)
    assert detector.feed(silence(1)[0]) is not None
    assert detector.end_of_speech() is None
    # A new stretch right away: still inside the refractory period.
    assert feed(detector, 3) == [None, None, None]


def test_the_end_of_speech_returns_the_scorer_to_its_start() -> None:
    scorer = FakeScorer(0.2, 0.4)
    detector = detector_with(scorer)
    feed(detector, 2)
    assert detector.last_peak is None
    assert detector.end_of_speech() is None
    assert scorer.resets == 1
    assert detector.last_peak == 0.4


def test_reset_forgets_the_refractory_period() -> None:
    scorer = FakeScorer(0.9, 0.9)
    detector = detector_with(scorer)
    assert detector.feed(silence(1)[0]) is not None
    detector.reset()
    assert scorer.resets == 1
    # The stretch the reset cut short is still reported to the debug log.
    assert detector.last_peak == 0.9
    assert detector.feed(silence(1)[0]) is not None


def test_a_higher_threshold_holds_it_back() -> None:
    assert feed(detector_with(FakeScorer(0.89), threshold=0.9), 1) == [None]


# --- building ----------------------------------------------------------------


def test_a_missing_model_is_one_clear_line(tmp_path: Path) -> None:
    phrase = WakePhraseConfig(
        phrase="autobot",
        kind="openwakeword",
        target="ack",
        model=str(tmp_path / "autobot.onnx"),
    )
    with pytest.raises(ModelMissingError) as error:
        build_detector(phrase, AUDIO)
    assert "wake-word model file for 'autobot' is missing" in str(error.value)
    assert "make train-wakeword" in str(error.value)
    assert "\n" not in str(error.value)


def test_it_refuses_frames_the_feature_models_cannot_take() -> None:
    phrase = WakePhraseConfig(
        phrase="autobot", kind="openwakeword", target="ack", model=str(WAKEWORD_MODEL)
    )
    audio = AudioConfig(input_device="pipewire", output_device="pipewire", frame_ms=20)
    with pytest.raises(ConfigError, match="1280-sample frames at 16000 Hz"):
        build_detector(phrase, audio)


def test_build_detector_finds_the_engine_and_passes_the_threshold(
    wakeword_threshold: float,
) -> None:
    phrase = WakePhraseConfig(
        phrase="autobot",
        kind="openwakeword",
        target="ack",
        model=str(WAKEWORD_MODEL),
        threshold=0.85,
    )
    detector = build_detector(phrase, AUDIO)
    assert isinstance(detector, OpenWakeWordDetector)
    assert detector.threshold == 0.85
    assert detector.gate_margins == GATE_MARGINS


# --- the scorer --------------------------------------------------------------


def scores_of(scorer: OnnxScorer, clip: str) -> list[float]:
    return [scorer.score(frame) for frame in load_clip(clip)]


def test_the_scorer_scores_from_its_first_frame(wakeword_threshold: float) -> None:
    # It starts primed, not empty: the model's window is full from the start.
    scorer = OnnxScorer(WAKEWORD_MODEL)
    score = scorer.score(silence(1)[0])
    assert 0.0 <= score < 0.5


def test_the_scorer_hears_the_phrase_and_reset_starts_it_afresh(
    wakeword_threshold: float,
) -> None:
    scorer = OnnxScorer(WAKEWORD_MODEL)
    first = scores_of(scorer, "hey_autobot")
    assert max(first) >= wakeword_threshold
    assert max(scores_of(scorer, "unrelated_speech")) < wakeword_threshold
    scorer.reset()
    # Nothing of the clips in between is left: the scores repeat exactly.
    assert scores_of(scorer, "hey_autobot") == first


def test_an_unprimed_scorer_gives_zero_until_its_window_is_full(
    wakeword_threshold: float,
) -> None:
    scorer = OnnxScorer(WAKEWORD_MODEL, prime_frames=0)
    scores = [scorer.score(frame) for frame in silence(16)]
    assert scores[:15] == [0.0] * 15
    assert len(silence(1)[0]) == FRAME_SAMPLES


# --- gate margins, through the service's frame path --------------------------


class ScriptedVad:
    def __init__(self, scores: list[float]):
        self._scores = list(scores)
        self.calls = 0

    def probability(self, frame: Frame) -> float:
        self.calls += 1
        return self._scores.pop(0)

    def reset(self) -> None:
        return None


class Recorder:
    """A detector that records the number stamped in each frame it is fed."""

    def __init__(self, gate_margins: tuple[int, int] | None = None):
        if gate_margins is not None:
            self.gate_margins = gate_margins
        self.fed: list[int] = []
        self.ends = 0

    def feed(self, frame: Frame) -> WakeEvent | None:
        self.fed.append(int(frame[0]))
        return None

    def end_of_speech(self) -> WakeEvent | None:
        self.ends += 1
        return None

    def reset(self) -> None:
        self.fed = []


def numbered(count: int) -> list[Frame]:
    return [np.full(FRAME_SAMPLES, number, dtype=np.int16) for number in range(count)]


def test_a_detector_gets_the_margins_it_asks_for_from_one_vad() -> None:
    # Frames 0-5 quiet, 6-7 speech, then quiet.
    scores = [0.1] * 6 + [0.9] * 2 + [0.1] * 8
    vad = ScriptedVad(scores)
    plain, wide = Recorder(), Recorder(gate_margins=(4, 5))
    gate = SpeechGate(vad, pre_roll_frames=1, hangover_frames=2)
    pipeline = Pipeline(gate, [plain, wide])
    for frame in numbered(len(scores)):
        pipeline.process(frame)
    # The VAD scored each frame once, for both detectors.
    assert vad.calls == len(scores)
    assert plain.fed == [5, 6, 7, 8, 9]
    assert wide.fed == [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    assert (plain.ends, wide.ends) == (1, 1)
    assert pipeline.gate is gate
    assert pipeline.stretches == 1
    assert pipeline.voiced_frames == len(plain.fed)


def test_the_pipeline_counts_through_the_only_detectors_gate() -> None:
    scores = [0.1] * 3 + [0.9] + [0.1] * 4
    wide = Recorder(gate_margins=(2, 3))
    pipeline = Pipeline(SpeechGate(ScriptedVad(scores)), [wide])
    in_speech = []
    for frame in numbered(len(scores)):
        pipeline.process(frame)
        in_speech.append(pipeline.gate.in_speech)
    assert wide.fed == [1, 2, 3, 4, 5, 6]
    assert in_speech == [False, False, False, True, True, True, False, False]
    assert pipeline.voiced_frames == 6
    pipeline.reset()
    assert wide.fed == []


# --- the fixtures, through the service's frame path --------------------------


@pytest.mark.parametrize("clip", POSITIVE_CLIPS)
def test_a_wake_clip_yields_exactly_one_event(
    openwakeword_pipeline: Pipeline, wakeword_threshold: float, clip: str
) -> None:
    events = run_clip(openwakeword_pipeline, clip)
    assert len(events) == 1, f"{clip}: {events}"
    event = events[0]
    assert (event.phrase, event.target, event.kind) == (
        "autobot",
        "ack",
        "openwakeword",
    )
    assert wakeword_threshold <= event.score <= 1.0
    assert event.at.tzinfo is not None


@pytest.mark.parametrize("clip", NEGATIVE_CLIPS)
def test_a_clip_without_the_phrase_yields_no_event(
    openwakeword_pipeline: Pipeline, clip: str
) -> None:
    assert run_clip(openwakeword_pipeline, clip) == []


def test_clips_played_back_to_back_yield_one_event_each(
    openwakeword_pipeline: Pipeline,
) -> None:
    order = ("hey_autobot", "unrelated_speech", "yo_autobot", "near_miss_auto")
    counts = [len(run_clip(openwakeword_pipeline, clip)) for clip in order]
    assert counts == [1, 0, 1, 0]
