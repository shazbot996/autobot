"""`autobot.listener.engines.sherpa_kws`: firing rule, keyword, spotter, fixtures.

What the baseline does on the six fixture clips, through the service's frame
path (real VAD, `GATE_MARGINS`), by `threshold`. Measured on the NUC with
sherpa-onnx 2.0.3; 1 is one `WakeEvent`, 0 none:

    threshold  autobot  hey_autobot  yo_autobot  room_noise  unrelated  near_miss
    0.02-0.80     1          1           1           0           0          0
    0.90          1          0           1           0           0          0
    1.00          1          0           1           0           0          0

The fixture tests below run at the default threshold, 0.5.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autobot.config import (
    KIND_SHERPA_KWS,
    WAKE_PHRASE_KINDS,
    AudioConfig,
    ConfigError,
    WakePhraseConfig,
)
from autobot.listener.detector import Frame, WakeEvent, build_detector
from autobot.listener.engines import sherpa_kws
from autobot.listener.engines.sherpa_kws import (
    GATE_MARGINS,
    SherpaKwsDetector,
    SherpaSpotter,
    keyword_pieces,
    keywords_line,
    spelled,
)
from autobot.listener.models import ModelMissingError
from autobot.listener.service import Pipeline

from .conftest import (
    FIXED_TIME,
    NEGATIVE_CLIPS,
    POSITIVE_CLIPS,
    load_clip,
    make_config,
    run_clip,
    silence,
)

AUDIO = AudioConfig(input_device="pipewire", output_device="pipewire")
AUTOBOT_PIECES = ("▁A", "U", "T", "O", "B", "O", "T")


class FakeSpotter:
    """A `Spotter` that hears the phrase on scripted frames, and counts calls."""

    def __init__(self, *heard: bool, at_flush: bool = False):
        self._heard = list(heard)
        self._at_flush = at_flush
        self.accepted = 0
        self.flushes = 0
        self.resets = 0

    def accept(self, frame: Frame) -> bool:
        self.accepted += 1
        return self._heard.pop(0) if self._heard else False

    def flush(self) -> bool:
        self.flushes += 1
        heard, self._at_flush = self._at_flush, False
        return heard

    def reset(self) -> None:
        self.resets += 1


def detector_with(
    spotter: FakeSpotter, threshold: float = 0.5, refractory_seconds: float = 0.4
) -> SherpaKwsDetector:
    # 0.4 s is five 80 ms frames.
    return SherpaKwsDetector(
        "autobot",
        "ack",
        spotter,
        threshold=threshold,
        refractory_seconds=refractory_seconds,
        clock=lambda: FIXED_TIME,
    )


def feed(detector: SherpaKwsDetector, frames: int) -> list[WakeEvent | None]:
    return [detector.feed(frame) for frame in silence(frames)]


# --- the firing rule ---------------------------------------------------------


def test_it_fires_when_the_spotter_hears_the_phrase() -> None:
    detector = detector_with(FakeSpotter(False, False, True, False))
    events = feed(detector, 4)
    assert [event is not None for event in events] == [False, False, True, False]
    assert events[2] == WakeEvent("autobot", "ack", "sherpa_kws", 0.5, FIXED_TIME)


def test_the_events_score_is_the_threshold_it_passed() -> None:
    # sherpa-onnx says that it heard the phrase, not how sure it was.
    detector = detector_with(FakeSpotter(True), threshold=0.35)
    (event,) = feed(detector, 1)
    assert event is not None
    assert event.score == 0.35
    assert event.kind == KIND_SHERPA_KWS
    # No per-stretch score: the listener reports the score it fired at.
    assert not hasattr(detector, "last_peak")
    assert not hasattr(detector, "last_transcript")


def test_one_utterance_yields_one_event() -> None:
    detector = detector_with(FakeSpotter(True, True, False, True))
    events = feed(detector, 6)
    assert [event is not None for event in events] == [True] + [False] * 5


def test_it_fires_again_once_the_refractory_period_is_over() -> None:
    # Fires on frame 0; frames 1-5 are refractory; frame 6 fires again.
    detector = detector_with(FakeSpotter(*[True] * 8))
    events = feed(detector, 8)
    assert [index for index, event in enumerate(events) if event] == [0, 6]


def test_every_frame_reaches_the_spotter_even_while_refractory() -> None:
    spotter = FakeSpotter(True)
    feed(detector_with(spotter), 4)
    assert spotter.accepted == 4


def test_the_end_of_speech_decodes_what_is_left_and_may_fire() -> None:
    # The decoder runs behind the audio: a phrase at the very end of a stretch
    # is heard when the stretch is flushed.
    spotter = FakeSpotter(False, False, at_flush=True)
    detector = detector_with(spotter)
    assert feed(detector, 2) == [None, None]
    event = detector.end_of_speech()
    assert event == WakeEvent("autobot", "ack", "sherpa_kws", 0.5, FIXED_TIME)
    assert spotter.flushes == 1
    assert detector.end_of_speech() is None


def test_the_rest_of_the_stretch_cannot_fire_again() -> None:
    spotter = FakeSpotter(True, True, at_flush=True)
    detector = detector_with(spotter)
    assert detector.feed(silence(1)[0]) is not None
    assert detector.feed(silence(1)[0]) is None
    # Heard again in the flush: still the same utterance.
    assert detector.end_of_speech() is None
    assert spotter.flushes == 1


def test_the_refractory_period_ends_with_the_stretch() -> None:
    # Silence is not fed, so the next stretch may be seconds later: the
    # phrase said again must fire again.
    spotter = FakeSpotter(True, True)
    detector = detector_with(spotter, refractory_seconds=2.0)
    assert detector.feed(silence(1)[0]) is not None
    assert detector.end_of_speech() is None
    assert detector.feed(silence(1)[0]) is not None


def test_reset_forgets_the_stream_and_the_refractory_period() -> None:
    spotter = FakeSpotter(True, True)
    detector = detector_with(spotter)
    assert detector.feed(silence(1)[0]) is not None
    detector.reset()
    assert spotter.resets == 1
    assert detector.feed(silence(1)[0]) is not None


# --- the phrase as a keyword -------------------------------------------------


def test_the_phrase_is_spelled_in_the_models_alphabet() -> None:
    assert spelled("Hey, autobot!") == "HEY AUTOBOT"
    assert spelled("it's  me") == "IT'S ME"
    assert spelled("42 ...") == ""


def test_a_word_the_model_never_saw_is_spelled_in_smaller_pieces(
    sherpa_kws_model: Path,
) -> None:
    assert keyword_pieces("autobot", sherpa_kws_model) == AUTOBOT_PIECES
    assert keyword_pieces("Autobot.", sherpa_kws_model) == AUTOBOT_PIECES


def test_the_keyword_follows_the_configured_phrase(sherpa_kws_model: Path) -> None:
    pieces = keyword_pieces("hey computer", sherpa_kws_model)
    assert "".join(pieces).replace("▁", " ").strip() == "HEY COMPUTER"
    assert pieces != AUTOBOT_PIECES
    known = sherpa_kws.known_tokens(sherpa_kws_model / sherpa_kws.TOKENS_FILE)
    assert set(pieces) <= known


@pytest.mark.parametrize("phrase", ["42", "...", "r2d2"])
def test_a_phrase_the_model_cannot_spell_is_one_clear_line(
    sherpa_kws_model: Path, phrase: str
) -> None:
    with pytest.raises(ConfigError) as error:
        keyword_pieces(phrase, sherpa_kws_model)
    assert f"wake phrase {phrase!r}" in str(error.value)
    assert "\n" not in str(error.value)


def test_the_keywords_line_carries_the_pieces_the_boost_and_the_threshold() -> None:
    # `threshold` is halved: autobot's default 0.5 is sherpa-onnx's default 0.25.
    assert keywords_line(AUTOBOT_PIECES, 0.5) == "▁A U T O B O T :1.0 #0.2500 @WAKE"
    assert keywords_line(("▁HE", "Y"), 0.8).endswith(" #0.4000 @WAKE")


# --- building ----------------------------------------------------------------


def test_the_kind_is_configured_without_a_model() -> None:
    assert KIND_SHERPA_KWS in WAKE_PHRASE_KINDS
    phrase = make_config("sherpa_kws", threshold=0.4).wake_phrases[0]
    assert (phrase.kind, phrase.model, phrase.threshold) == ("sherpa_kws", "", 0.4)


def test_a_missing_model_is_one_clear_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sherpa_kws, "SHERPA_KWS_DIR", tmp_path / "sherpa-kws")
    phrase = WakePhraseConfig(phrase="autobot", kind="sherpa_kws", target="ack")
    with pytest.raises(ModelMissingError) as error:
        build_detector(phrase, AUDIO)
    assert "sherpa-onnx keyword spotter model file is missing" in str(error.value)
    assert "make fetch-models" in str(error.value)
    assert "\n" not in str(error.value)


def test_it_refuses_audio_the_model_was_not_trained_on() -> None:
    phrase = WakePhraseConfig(phrase="autobot", kind="sherpa_kws", target="ack")
    audio = AudioConfig(
        input_device="pipewire", output_device="pipewire", sample_rate=8000
    )
    with pytest.raises(ConfigError, match="sherpa_kws needs audio at 16000 Hz"):
        build_detector(phrase, audio)


def test_build_detector_finds_the_engine_and_passes_the_threshold(
    sherpa_kws_model: Path,
) -> None:
    phrase = WakePhraseConfig(
        phrase="autobot", kind="sherpa_kws", target="ack", threshold=0.3
    )
    detector = build_detector(phrase, AUDIO)
    assert isinstance(detector, SherpaKwsDetector)
    assert detector.threshold == 0.3
    assert detector.gate_margins == GATE_MARGINS


# --- the spotter -------------------------------------------------------------


def hears(spotter: SherpaSpotter, clip: str) -> list[bool]:
    heard = [spotter.accept(frame) for frame in load_clip(clip)]
    return [*heard, spotter.flush()]


def test_the_spotter_hears_the_phrase_and_starts_afresh(
    sherpa_kws_model: Path,
) -> None:
    spotter = SherpaSpotter(sherpa_kws_model, AUTOBOT_PIECES, 0.5)
    first = hears(spotter, "hey_autobot")
    assert first.count(True) == 1
    assert not any(hears(spotter, "unrelated_speech"))
    # Half a clip, dropped: nothing of it is left in the next stream.
    for frame in load_clip("yo_autobot")[:20]:
        spotter.accept(frame)
    spotter.reset()
    assert hears(spotter, "hey_autobot") == first


def test_the_spotter_listens_for_the_phrase_it_was_given(
    sherpa_kws_model: Path,
) -> None:
    # Not hard-coded: a spotter built for another phrase is deaf to "autobot".
    pieces = keyword_pieces("the weather tomorrow", sherpa_kws_model)
    spotter = SherpaSpotter(sherpa_kws_model, pieces, 0.5)
    assert not any(hears(spotter, "hey_autobot"))
    assert hears(spotter, "unrelated_speech").count(True) == 1


# --- the fixtures, through the service's frame path --------------------------


@pytest.mark.parametrize("clip", POSITIVE_CLIPS)
def test_a_wake_clip_yields_exactly_one_event(
    sherpa_kws_pipeline: Pipeline, clip: str
) -> None:
    events = run_clip(sherpa_kws_pipeline, clip)
    assert len(events) == 1, f"{clip}: {events}"
    event = events[0]
    assert (event.phrase, event.target, event.kind) == ("autobot", "ack", "sherpa_kws")
    assert event.score == 0.5
    assert event.at.tzinfo is not None


@pytest.mark.parametrize("clip", NEGATIVE_CLIPS)
def test_a_clip_without_the_phrase_yields_no_event(
    sherpa_kws_pipeline: Pipeline, clip: str
) -> None:
    assert run_clip(sherpa_kws_pipeline, clip) == []


def test_clips_played_back_to_back_yield_one_event_each(
    sherpa_kws_pipeline: Pipeline,
) -> None:
    order = ("hey_autobot", "unrelated_speech", "yo_autobot", "near_miss_auto")
    counts = [len(run_clip(sherpa_kws_pipeline, clip)) for clip in order]
    assert counts == [1, 0, 1, 0]


def test_muting_mid_phrase_leaves_nothing_behind(
    sherpa_kws_pipeline: Pipeline,
) -> None:
    # The listener resets the pipeline after the acknowledgement plays.
    for frame in load_clip("hey_autobot")[:18]:
        sherpa_kws_pipeline.process(frame)
    sherpa_kws_pipeline.reset()
    assert run_clip(sherpa_kws_pipeline, "near_miss_auto") == []
    assert len(run_clip(sherpa_kws_pipeline, "autobot")) == 1
