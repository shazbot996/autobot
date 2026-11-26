"""`autobot.listener.vad`: Silero VAD on fixtures, and the speech gate."""

from __future__ import annotations

import numpy as np
import pytest

from autobot.listener.detector import Frame
from autobot.listener.models import ModelMissingError
from autobot.listener.vad import SileroVad, SpeechGate, VadError

from .conftest import FRAME_SAMPLES, load_clip, silence


class ScriptedVad:
    """A `Vad` that returns the scores it was given, in order."""

    def __init__(self, scores: list[float]):
        self._scores = list(scores)
        self.resets = 0

    def probability(self, frame: Frame) -> float:
        return self._scores.pop(0)

    def reset(self) -> None:
        self.resets += 1


def numbered(count: int) -> list[Frame]:
    """Frames that can be told apart: frame n is filled with n."""
    return [np.full(FRAME_SAMPLES, n, dtype=np.int16) for n in range(count)]


def test_silero_scores_speech_high_and_room_noise_low(models: None) -> None:
    vad = SileroVad()
    speech = [vad.probability(frame) for frame in load_clip("hey_autobot")]
    vad.reset()
    noise = [vad.probability(frame) for frame in load_clip("room_noise")]
    assert all(0.0 <= score <= 1.0 for score in speech + noise)
    assert max(speech) > 0.9
    assert max(noise) < 0.3


def test_silero_reset_makes_a_clip_score_the_same_twice(models: None) -> None:
    vad = SileroVad()
    frames = load_clip("yo_autobot")
    first = [vad.probability(frame) for frame in frames]
    vad.reset()
    second = [vad.probability(frame) for frame in frames]
    assert first == second


def test_silero_carries_the_part_window_between_frames(models: None) -> None:
    # 1280 samples are 2.5 windows of 512: frames alternate two and three
    # windows, and a short frame completes none and repeats the last score.
    vad = SileroVad()
    frames = load_clip("hey_autobot")
    for frame in frames[:20]:
        vad.probability(frame)
    last = vad.probability(frames[20])
    assert vad.probability(np.zeros(100, dtype=np.int16)) == last


def test_silero_refuses_an_unsupported_sample_rate() -> None:
    with pytest.raises(VadError, match="8000 and 16000"):
        SileroVad(sample_rate=22050)


def test_silero_missing_model_is_one_clear_line(tmp_path: object) -> None:
    with pytest.raises(ModelMissingError) as error:
        SileroVad(model=f"{tmp_path}/silero_vad.onnx")
    assert "Silero VAD model file is missing" in str(error.value)
    assert "\n" not in str(error.value)


def test_gate_lets_no_silence_through() -> None:
    gate = SpeechGate(ScriptedVad([0.1] * 10))
    for frame in numbered(10):
        result = gate.push(frame)
        assert result.frames == ()
        assert not result.speech_ended


def test_gate_releases_pre_roll_then_speech_then_hangover() -> None:
    # Frames 0-4 quiet, 5-7 speech, 8-10 quiet (hangover of 3 ends at 10).
    scores = [0.1] * 5 + [0.9] * 3 + [0.1] * 3 + [0.1] * 2
    gate = SpeechGate(ScriptedVad(scores), pre_roll_frames=2, hangover_frames=3)
    released: list[int] = []
    ended_at: list[int] = []
    for number, frame in enumerate(numbered(len(scores))):
        result = gate.push(frame)
        released.extend(int(voiced[0]) for voiced in result.frames)
        if result.speech_ended:
            ended_at.append(number)
    # Pre-roll 3 and 4, speech 5-7, the three quiet frames that end it; then nothing.
    assert released == [3, 4, 5, 6, 7, 8, 9, 10]
    assert ended_at == [10]
    assert not gate.in_speech


def test_gate_keeps_a_short_pause_inside_one_stretch() -> None:
    scores = [0.9, 0.1, 0.1, 0.9, 0.1, 0.1, 0.1]
    gate = SpeechGate(ScriptedVad(scores), pre_roll_frames=0, hangover_frames=3)
    ends = [gate.push(frame).speech_ended for frame in numbered(len(scores))]
    assert ends == [False, False, False, False, False, False, True]


def test_gate_uses_the_lower_threshold_to_end_speech() -> None:
    # 0.4 is below the start threshold but not below the end threshold.
    scores = [0.9] + [0.4] * 6
    gate = SpeechGate(ScriptedVad(scores), pre_roll_frames=0, hangover_frames=3)
    assert not any(gate.push(frame).speech_ended for frame in numbered(len(scores)))
    assert gate.in_speech


def test_gate_reset_drops_pre_roll_and_resets_the_vad() -> None:
    vad = ScriptedVad([0.1, 0.1, 0.9])
    gate = SpeechGate(vad, pre_roll_frames=2)
    frames = numbered(3)
    gate.push(frames[0])
    gate.push(frames[1])
    gate.reset()
    assert vad.resets == 1
    result = gate.push(frames[2])
    assert [int(voiced[0]) for voiced in result.frames] == [2]


@pytest.mark.parametrize("clip", ["autobot", "hey_autobot", "yo_autobot"])
def test_gate_finds_one_stretch_of_speech_in_a_wake_clip(
    models: None, clip: str
) -> None:
    gate = SpeechGate(SileroVad())
    results = [gate.push(frame) for frame in [*load_clip(clip), *silence(12)]]
    assert sum(result.speech_ended for result in results) == 1
    voiced = sum(len(result.frames) for result in results)
    assert 0 < voiced < len(results)


def test_gate_lets_nothing_of_room_noise_through(models: None) -> None:
    gate = SpeechGate(SileroVad())
    results = [gate.push(frame) for frame in load_clip("room_noise")]
    assert all(result.frames == () for result in results)
