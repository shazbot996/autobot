"""`autobot.wakeword.evaluate`: a model scored on the held-out real recordings.

The committed fixtures stand in for a tiny voice dataset, and the model is the
one `make train-wakeword` left at `models/wakeword/autobot.onnx`. Nothing is
downloaded and nothing is skipped: a missing model file fails the test with
the file's name.
"""

from __future__ import annotations

import subprocess
import sys
import wave
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pytest

from autobot.config import (
    AckConfig,
    AudioConfig,
    Config,
    ConfigError,
    HostConfig,
    WakePhraseConfig,
    resolve_repo_path,
)
from autobot.listener.engines.sherpa_kws import SHERPA_KWS_DIR, SHERPA_KWS_FILES
from autobot.listener.models import (
    WAKEWORD_MODELS_DIR,
    ModelMissingError,
    enforce_offline,
    require_dir,
)
from autobot.tui.audio import Clip
from autobot.wakeword import evaluate
from autobot.wakeword.dataset import (
    HELDOUT,
    LABEL_AMBIENT,
    LABEL_NEGATIVE,
    LABEL_POSITIVE,
    TRAIN,
    ClipRecord,
    HeldoutSize,
    VoiceDataset,
    split,
)
from autobot.wakeword.evaluate import (
    BASELINE_THRESHOLD,
    KIND_TAIL_FRAMES,
    MIN_RATE_MINUTES,
    MIN_VERDICT,
    OFFER_INSTALL,
    OFFER_JUDGMENT,
    OFFER_NONE,
    THRESHOLDS,
    TOO_LITTLE_AMBIENT,
    TOO_SMALL,
    EvaluateError,
    KindScores,
    RealScores,
    compare,
    recommend_real,
    sweep_rows,
)

# Before anything here can import onnxruntime.
enforce_offline()

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "audio"
RATE = 16000
SPEAKER = "fixture"

type Int16 = npt.NDArray[np.int16]


@pytest.fixture(scope="module")
def model() -> Path:
    """The trained model the tests score; a missing file fails with its name."""
    path = resolve_repo_path(WAKEWORD_MODELS_DIR / "autobot.onnx")
    if not path.is_file():
        pytest.fail(
            f"wake-word model file is missing: {path} (train it with "
            "'make train-wakeword'; models are never downloaded by a test)"
        )
    return path


def fixture_audio(name: str) -> Int16:
    path = FIXTURES / f"{name}.wav"
    with wave.open(str(path), "rb") as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (
            RATE,
            1,
            2,
        )
        frames = wav.readframes(wav.getnframes())
    return np.frombuffer(frames, dtype="<i2").astype(np.int16)


def record_for(label: str, prompt: str, prompt_id: str, number: int) -> ClipRecord:
    return ClipRecord(
        file="",
        phrase="autobot",
        prompt=prompt,
        prompt_id=prompt_id,
        label=label,
        source="session",
        speaker=SPEAKER,
        device="test",
        recorded_at=f"2026-11-26T19:00:{number:02d}+00:00",
    )


def prompt_id_on(side: str, prefix: str) -> str:
    """A prompt identifier the dataset's split puts on `side` for `SPEAKER`."""
    number = 0
    while split(record_for(LABEL_POSITIVE, "", f"{prefix}-{number:03d}", 0)) != side:
        number += 1
    return f"{prefix}-{number:03d}"


def fixture_dataset(root: Path, side: str = HELDOUT) -> VoiceDataset:
    """The fixtures as a dataset: two positives, two negatives, one ambient clip."""
    dataset = VoiceDataset(root)
    clips = (
        (LABEL_POSITIVE, "autobot", "autobot"),
        (LABEL_POSITIVE, "hey autobot", "hey_autobot"),
        (LABEL_NEGATIVE, "auto", "near_miss_auto"),
        (
            LABEL_NEGATIVE,
            "the weather tomorrow looks fine for a walk in the park",
            "unrelated_speech",
        ),
    )
    for number, (label, prompt, name) in enumerate(clips):
        record = record_for(label, prompt, prompt_id_on(side, name), number)
        dataset.add(Clip(fixture_audio(name), RATE), record)
    dataset.add(
        Clip(fixture_audio("room_noise"), RATE),
        record_for(LABEL_AMBIENT, "say nothing", "ambient-01", 50),
    )
    return dataset


# --- scoring the fixtures ----------------------------------------------------


def test_the_fixtures_score_as_a_tiny_held_out_set(tmp_path: Path, model: Path) -> None:
    dataset = fixture_dataset(tmp_path)
    scores = evaluate.score_real(model, dataset, "autobot")
    assert (scores.heldout_positives, scores.heldout_negatives) == (2, 2)
    # The last fifth of the 3 s of room noise.
    assert scores.heldout_ambient_minutes == pytest.approx(0.6 / 60)
    assert scores.size == dataset.heldout_size("autobot")
    assert not scores.empty
    assert [row["threshold"] for row in scores.rows] == list(THRESHOLDS)
    half = scores.row_at(0.5)
    assert half is not None
    assert (half["detected"], half["recall"]) == (2, 1.0)
    assert (half["false_accepts"], half["fa_rate"]) == (0, 0.0)
    assert half["ambient_false_accepts"] == 0
    # A tiny set gives a count for the ambient audio, never a rate.
    assert not scores.has_rate
    assert all(row["ambient_fa_per_hour"] is None for row in scores.rows)
    assert dict(scores.positive_peaks).keys() | dict(scores.negative_peaks).keys() == {
        record.file for record in dataset.records() if record.label != LABEL_AMBIENT
    }
    assert min(dict(scores.positive_peaks).values()) >= 0.5
    assert max(dict(scores.negative_peaks).values()) < 0.5
    best = scores.recommended
    assert best is not None and best["detected"] == 2 and best["false_accepts"] == 0
    assert scores.recommended_threshold == best["threshold"] >= 0.5
    # What the training writes into the metrics file's `real`.
    stored = scores.to_json()
    assert stored["sweep"] == list(scores.rows)
    assert stored["recommended_threshold"] == scores.recommended_threshold
    assert (stored["heldout_positives"], stored["heldout_negatives"]) == (2, 2)
    assert stored["minutes_ambient"] == scores.heldout_ambient_minutes


def test_only_the_held_out_side_is_scored(tmp_path: Path, model: Path) -> None:
    # The same clips, on the training side of the split: nothing to score
    # but the last fifth of the ambient clip.
    scores = evaluate.score_real(model, fixture_dataset(tmp_path, TRAIN), "autobot")
    assert (scores.heldout_positives, scores.heldout_negatives) == (0, 0)
    assert scores.positive_peaks == () and scores.negative_peaks == ()
    assert all(row["detected"] == 0 and row["recall"] == 0.0 for row in scores.rows)
    assert scores.heldout_ambient_minutes > 0


def test_ambient_false_accepts_come_from_the_last_fifth_only(
    tmp_path: Path, model: Path
) -> None:
    noise = np.tile(fixture_audio("room_noise"), 7)[: 20 * RATE]
    phrase = fixture_audio("autobot")

    def with_phrase_at(second: float) -> Int16:
        audio = noise.copy()
        start = int(second * RATE)
        audio[start : start + len(phrase)] = phrase
        return audio

    # The phrase said in the held-out last fifth (16 to 20 s) is a false accept.
    late = VoiceDataset(tmp_path / "late")
    late.add(
        Clip(with_phrase_at(16.6), RATE),
        record_for(LABEL_AMBIENT, "say nothing", "ambient-01", 1),
    )
    scores = evaluate.score_real(model, late, "autobot")
    assert scores.heldout_ambient_minutes == pytest.approx(4 / 60)
    half = scores.row_at(0.5)
    assert half is not None
    assert half["ambient_false_accepts"] == 1
    assert half["ambient_fa_per_hour"] is None
    # Said in the first four fifths, which are trained on, it is not counted.
    early = VoiceDataset(tmp_path / "early")
    early.add(
        Clip(with_phrase_at(5.0), RATE),
        record_for(LABEL_AMBIENT, "say nothing", "ambient-01", 1),
    )
    scores = evaluate.score_real(model, early, "autobot")
    assert all(row["ambient_false_accepts"] == 0 for row in scores.rows)


def test_long_ambient_audio_is_scored_in_blocks_without_losing_a_position(
    tmp_path: Path, model: Path
) -> None:
    # 70 s of ambient: more than two blocks of 32 s.
    audio = np.tile(fixture_audio("room_noise"), 24)[: 70 * RATE]
    path = tmp_path / "long.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(audio.astype("<i2").tobytes())
    scorer = evaluate.ModelScorer(model)
    scores = scorer.stream_scores(path, 0, len(audio))
    # One score for every 80 ms frame, less the 24 the first window needs.
    assert len(scores) == 70 * RATE // 1280 - evaluate.BLOCK_OVERLAP_FRAMES
    assert float(scores.max()) < 0.5
    assert len(scorer.stream_scores(path, 0, 0)) == 0


def test_nothing_held_out_is_an_empty_result(tmp_path: Path, model: Path) -> None:
    scores = evaluate.score_real(model, VoiceDataset(tmp_path), "autobot")
    assert scores.empty
    assert scores.recommended is None and scores.recommended_threshold is None
    assert scores.size == HeldoutSize(0, 0, 0.0)
    comparison = compare(scores, scores, scores.size)
    # No verdict can be given: the owner's own judgment, as below the minimum.
    assert comparison.verdict is None and comparison.offer == OFFER_JUDGMENT
    assert "no held-out real recordings" in comparison.summary
    assert compare(scores, None, scores.size).offer == OFFER_INSTALL


def test_a_missing_or_unusable_model_is_one_line(tmp_path: Path) -> None:
    dataset = fixture_dataset(tmp_path / "voice")
    with pytest.raises(
        evaluate.EvaluateError, match=r"missing: .*nowhere\.onnx"
    ) as error:
        evaluate.score_real(tmp_path / "nowhere.onnx", dataset, "autobot")
    assert "\n" not in str(error.value)
    junk = tmp_path / "junk.onnx"
    junk.write_bytes(b"not a model")
    with pytest.raises(evaluate.EvaluateError, match="not a wake-word model") as error:
        evaluate.score_real(junk, dataset, "autobot")
    assert "\n" not in str(error.value)


# --- the sweep and the recommendation ----------------------------------------


def test_the_sweep_counts_clips_and_events() -> None:
    stream = np.zeros(1000)
    stream[[100, 105, 400]] = 0.8
    rows = sweep_rows([0.9, 0.6, 0.2], [0.7, 0.1], stream, 2.0, (0.5, 0.75))
    assert rows[0] == {
        "threshold": 0.5,
        "detected": 2,
        "recall": pytest.approx(2 / 3),
        "false_accepts": 1,
        "fa_rate": 0.5,
        # 100 and 105 are within two seconds: one event; 400 is another.
        "ambient_false_accepts": 2,
        "ambient_fa_per_hour": None,
    }
    assert (rows[1]["detected"], rows[1]["false_accepts"]) == (1, 0)
    # From twelve minutes of held-out ambient the count becomes a rate.
    assert MIN_RATE_MINUTES == 12
    rated = sweep_rows([0.9], [], stream, 12.0, (0.5,))
    assert rated[0]["ambient_fa_per_hour"] == pytest.approx(10.0)
    assert rated[0]["fa_rate"] == 0.0
    below = sweep_rows([0.9], [], stream, 11.9, (0.5,))
    assert below[0]["ambient_fa_per_hour"] is None
    # No clips at all: zeros, not a division by zero.
    assert sweep_rows([], [], np.zeros(0), 0.0, (0.5,))[0]["recall"] == 0.0


def row(
    threshold: float,
    detected: int,
    false_accepts: int = 0,
    ambient: int = 0,
    per_hour: float | None = None,
    positives: int = 20,
) -> dict[str, Any]:
    return {
        "threshold": threshold,
        "detected": detected,
        "recall": detected / positives,
        "false_accepts": false_accepts,
        "fa_rate": false_accepts / 20,
        "ambient_false_accepts": ambient,
        "ambient_fa_per_hour": per_hour,
    }


def test_the_real_recommendation_is_the_best_recall_with_quiet_ambient() -> None:
    # With a rate: one false accept an hour is the target, as for the
    # validation stream.
    rows = [
        row(0.2, 20, per_hour=0.0),
        row(0.5, 19, ambient=3, per_hour=3.0),
        row(0.7, 18, ambient=1, per_hour=1.0),
        row(0.9, 15, per_hour=0.0),
    ]
    best = recommend_real(rows)
    # 0.2 is under the lowest threshold offered; 0.5 is over the target.
    assert best is not None and best["threshold"] == 0.7


def test_below_twelve_minutes_the_recommendation_wants_no_ambient_false_accept() -> (
    None
):
    rows = [row(0.5, 20, ambient=1), row(0.7, 18), row(0.9, 15)]
    best = recommend_real(rows)
    assert best is not None and best["threshold"] == 0.7


def test_thresholds_that_detect_the_same_clips_are_settled_by_the_negatives() -> None:
    rows = [
        row(0.5, 12, false_accepts=2),
        row(0.6, 12, false_accepts=1),
        row(0.7, 12, false_accepts=1),
        row(0.75, 12, false_accepts=1),
        row(0.8, 11),
    ]
    # The same twelve detected at four thresholds: the fewest false accepts
    # first, and of the three that are then equal, the middle one.
    best = recommend_real(rows)
    assert best is not None and best["threshold"] == 0.7
    # A model that scores 1.0 on every held-out positive is equal at every
    # threshold: neither the lowest nor the highest is recommended.
    flat = [row(threshold, 12, false_accepts=3) for threshold in THRESHOLDS]
    best = recommend_real(flat)
    assert best is not None and best["threshold"] == 0.75


def test_a_model_never_quiet_keeps_a_usable_recall() -> None:
    rows = [row(0.5, 19, ambient=4), row(0.9, 14, ambient=2), row(0.99, 6, ambient=1)]
    best = recommend_real(rows)
    assert best is not None and best["threshold"] == 0.9
    assert recommend_real([]) is None


# --- the comparison ----------------------------------------------------------

LARGE = HeldoutSize(positives=20, negatives=20, ambient_minutes=12.0)
SMALL = HeldoutSize(positives=11, negatives=6, ambient_minutes=1.0)


def scores(
    detected: int,
    false_accepts: int = 0,
    ambient: int = 0,
    size: HeldoutSize = LARGE,
    threshold: float = 0.8,
) -> RealScores:
    per_hour = (
        ambient / (size.ambient_minutes / 60)
        if size.ambient_minutes >= MIN_RATE_MINUTES
        else None
    )
    best = row(threshold, detected, false_accepts, ambient, per_hour, size.positives)
    return RealScores(
        model=Path("model.onnx"),
        phrase="autobot",
        heldout_positives=size.positives,
        heldout_negatives=size.negatives,
        heldout_ambient_minutes=size.ambient_minutes,
        rows=(best,),
        recommended=best,
    )


def test_the_minimum_size_for_a_verdict() -> None:
    assert MIN_VERDICT == HeldoutSize(20, 20, 12.0)


def test_below_the_minimum_size_there_is_no_verdict() -> None:
    for size in (
        SMALL,
        HeldoutSize(19, 20, 12.0),
        HeldoutSize(20, 19, 12.0),
        HeldoutSize(20, 20, 11.9),
    ):
        # Even a candidate that looks far worse gets no verdict.
        comparison = compare(scores(2, 5, 3, size), scores(10, 0, 0, size), size)
        assert comparison.verdict is None
        assert not comparison.large_enough and not comparison.nothing_installed
        assert comparison.summary.startswith(TOO_SMALL)
        assert comparison.offer == OFFER_JUDGMENT
        assert comparison.reasons == ()


def test_a_tie_is_at_least_as_good() -> None:
    comparison = compare(scores(17, 1, 1), scores(17, 1, 1), LARGE)
    assert comparison.verdict is True and comparison.large_enough
    assert comparison.offer == OFFER_INSTALL
    assert (
        comparison.summary == "the candidate is at least as good as the installed model"
    )


def test_one_clip_or_one_event_worse_is_still_good() -> None:
    installed = scores(17, 1, 1)
    assert compare(scores(16, 1, 1), installed, LARGE).verdict is True
    assert compare(scores(17, 2, 1), installed, LARGE).verdict is True
    assert compare(scores(17, 1, 2), installed, LARGE).verdict is True
    # One worse on every measure at once is within the tolerance too.
    assert compare(scores(16, 2, 2), installed, LARGE).verdict is True
    # Each model at its own recommended threshold, whatever they are.
    assert compare(scores(16, threshold=0.95), scores(17, threshold=0.5), LARGE).verdict


def test_two_worse_on_any_measure_is_worse() -> None:
    installed = scores(17, 1, 1)
    fewer = compare(scores(15, 1, 1), installed, LARGE)
    assert fewer.verdict is False and fewer.offer == OFFER_NONE
    assert fewer.reasons == ("it detects 2 fewer held-out positives",)
    assert fewer.summary == (
        "the candidate is worse than the installed model: it detects 2 fewer "
        "held-out positives"
    )
    assert compare(scores(17, 3, 1), installed, LARGE).reasons == (
        "it makes 2 more false accepts on the negatives",
    )
    assert compare(scores(17, 1, 3), installed, LARGE).reasons == (
        "it makes 2 more false accepts in the ambient audio",
    )
    # Better on one measure does not buy back another.
    assert compare(scores(20, 4, 0), installed, LARGE).verdict is False


def test_with_nothing_installed_there_is_nothing_to_beat() -> None:
    for size in (SMALL, LARGE):
        comparison = compare(scores(3, 2, 1, size), None, size)
        assert comparison.verdict is None
        assert comparison.nothing_installed
        assert comparison.offer == OFFER_INSTALL
        assert comparison.installed_row is None
        assert comparison.summary == (
            "nothing is installed: the candidate has nothing to beat"
        )
        # One column of figures.
        assert "installed" not in comparison.table


def test_the_table_shows_both_models_and_what_the_figures_rest_on() -> None:
    comparison = compare(
        scores(18, 1, 2, threshold=0.85), scores(16, 0, 1, threshold=0.8), LARGE
    )
    assert comparison.table == "\n".join(comparison.lines)
    assert comparison.lines == (
        "held-out real recordings: 20 positives, 20 negatives, 12.0 min ambient",
        "                               candidate     installed",
        "threshold (recommended)             0.85           0.8",
        "positives detected              18 of 20      16 of 20",
        "recall                             0.900         0.800",
        "false accepts on negatives       1 of 20       0 of 20",
        "false accepts in ambient               2             1",
        "false accepts per hour             10.00          5.00",
    )
    assert TOO_LITTLE_AMBIENT not in comparison.table


def test_below_twelve_minutes_the_table_shows_counts_and_says_so() -> None:
    comparison = compare(scores(10, 0, 1, SMALL), scores(9, 1, 0, SMALL), SMALL)
    assert comparison.lines[0] == (
        "held-out real recordings: 11 positives, 6 negatives, 1.0 min ambient"
    )
    assert comparison.lines[-2].split() == ["false", "accepts", "per", "hour", "-", "-"]
    assert comparison.lines[-1] == (
        "too little held-out ambient to judge false accepts per hour: 1 and 0 "
        "false accepts in 1.0 min"
    )
    alone = compare(scores(10, 0, 1, SMALL), None, SMALL)
    assert alone.lines[-1].endswith(": 1 false accepts in 1.0 min")


# --- the command line and what it needs --------------------------------------


def test_the_command_prints_the_sweep_the_counts_and_the_threshold(
    tmp_path: Path, model: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture_dataset(tmp_path)
    assert evaluate.main([str(model), "--dataset", str(tmp_path)]) == 0
    printed = capsys.readouterr().out.splitlines()
    assert printed[0].startswith(f"real-set evaluation of {model} for 'autobot'")
    assert printed[1] == "held out: 2 positives, 2 negatives, 0.0 min ambient"
    assert printed[2].split() == [
        *("thr", "detected", "recall", "false", "accepts", "fa", "rate"),
        *("ambient", "fa", "ambient", "fa/h"),
    ]
    assert len(printed) == 3 + len(THRESHOLDS) + 2
    assert printed[3].split()[:2] == ["0.10", "2/2"]
    # A count in the minutes there are, in place of a rate.
    assert printed[-2].startswith(
        f"{TOO_LITTLE_AMBIENT}: 0 false accepts in 0.0 min of held-out ambient"
    )
    assert printed[-1].startswith("recommended threshold ")
    assert "detects 2 of 2 held-out positives" in printed[-1]


def test_the_command_compares_with_another_model(
    tmp_path: Path, model: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture_dataset(tmp_path)
    arguments = [str(model), "--dataset", str(tmp_path), "--against", str(model)]
    assert evaluate.main(arguments) == 0
    printed = capsys.readouterr().out.splitlines()
    assert (
        "held-out real recordings: 2 positives, 2 negatives, 0.0 min ambient" in printed
    )
    assert printed[-1].startswith(TOO_SMALL)


def test_an_empty_dataset_is_said_in_one_line_and_is_not_an_error(
    tmp_path: Path, model: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert evaluate.main([str(model), "--dataset", str(tmp_path / "voice")]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == (
        f"no real recordings for 'autobot' in {tmp_path / 'voice'}: "
        "nothing to evaluate\n"
    )


def test_the_command_reports_a_missing_model_in_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture_dataset(tmp_path)
    missing = tmp_path / "candidate.onnx"
    assert evaluate.main([str(missing), "--dataset", str(tmp_path)]) == 2
    captured = capsys.readouterr()
    assert captured.err == f"wake-word model file is missing: {missing}\n"


def test_the_evaluation_needs_the_runtime_libraries_only(model: Path) -> None:
    # In a fresh interpreter: importing loads no speech library at all, and
    # scoring loads onnxruntime with its telemetry switched off, and nothing
    # of the training extra or the transcriber.
    script = (
        "import os, sys\n"
        "from pathlib import Path\n"
        "import autobot.wakeword.evaluate as evaluate\n"
        "heavy = {'onnxruntime', 'torch', 'scipy', 'faster_whisper', 'soundfile',\n"
        "         'sounddevice', 'piper'}\n"
        "assert not heavy & set(sys.modules), heavy & set(sys.modules)\n"
        "evaluate.ModelScorer(Path(sys.argv[1]))\n"
        "assert os.environ['ORT_DISABLE_TELEMETRY'] == '1'\n"
        "heavy.discard('onnxruntime')\n"
        "assert 'onnxruntime' in sys.modules\n"
        "assert not heavy & set(sys.modules), heavy & set(sys.modules)\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", script, str(model)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr


# --- scoring a detector kind (the baseline of TS-14) -------------------------

CONFIG = Config(
    host=HostConfig("nuc"),
    audio=AudioConfig("pipewire", "pipewire"),
    wake_phrases=(
        WakePhraseConfig(
            "autobot", "openwakeword", "ack", "models/wakeword/autobot.onnx", 0.85
        ),
    ),
    ack=AckConfig(),
)
FRAME = CONFIG.audio.frame_samples


class LoudPipeline:
    """A frame path that fires once for a clip with a loud frame in it.

    It fires when the silence after the audio comes, as a detector that
    decodes at the end of a stretch does, and never twice before `reset`.
    """

    def __init__(self, level: int) -> None:
        self.level = level
        self.loud = False
        self.fired = False
        self.resets = 0
        self.frames: list[int] = []

    def process(self, frame: Int16) -> list[str]:
        self.frames.append(len(frame))
        if np.abs(frame.astype(np.int32)).max() >= self.level:
            self.loud = True
            return []
        if self.loud and not self.fired and not frame.any():
            self.fired = True
            return ["wake"]
        return []

    def reset(self) -> None:
        self.resets += 1
        self.loud = False
        self.fired = False


def test_a_kind_is_scored_through_the_frame_path_clip_by_clip(tmp_path: Path) -> None:
    dataset = fixture_dataset(tmp_path)
    built: list[Config] = []
    # Louder than the near miss and the room, not louder than the rest.
    level = int(np.abs(fixture_audio("near_miss_auto").astype(np.int32)).max()) + 1
    pipeline = LoudPipeline(level)

    def build(config: Config) -> LoudPipeline:
        built.append(config)
        return pipeline

    scores = evaluate.score_kind("sherpa_kws", CONFIG, dataset, build=build)
    # The phrase alone, as the kind, with no model and the baseline's threshold.
    assert [
        (entry.phrase, entry.kind, entry.model, entry.threshold)
        for entry in built[0].wake_phrases
    ] == [("autobot", "sherpa_kws", "", BASELINE_THRESHOLD)]
    assert (scores.kind, scores.phrase, scores.threshold) == (
        "sherpa_kws",
        "autobot",
        0.5,
    )
    assert scores.size == dataset.heldout_size("autobot")
    loud = [
        name
        for name in ("autobot", "hey_autobot", "unrelated_speech")
        if np.abs(fixture_audio(name).astype(np.int32)).max() >= level
    ]
    positives = len([name for name in loud if "autobot" in name])
    assert scores.detected == positives
    assert scores.false_accepts == len(loud) - positives
    assert scores.ambient_false_accepts == 0
    # A fresh start for each of the four clips and for the ambient slice.
    assert pipeline.resets == 5
    # Whole frames only, and the silence after each clip.
    assert set(pipeline.frames) == {FRAME}
    whole = sum(
        -(-len(fixture_audio(name)) // FRAME)
        for name in ("autobot", "hey_autobot", "near_miss_auto", "unrelated_speech")
    )
    ambient = -(-int(0.6 * RATE) // FRAME)
    assert len(pipeline.frames) == whole + ambient + 5 * KIND_TAIL_FRAMES
    row = scores.row
    assert (row["threshold"], row["detected"]) == (0.5, positives)
    assert row["recall"] == positives / 2
    # A tiny set: a count for the ambient audio, never a rate.
    assert row["ambient_fa_per_hour"] is None


def test_a_kind_scores_only_what_is_held_out(tmp_path: Path) -> None:
    dataset = fixture_dataset(tmp_path, side=TRAIN)
    pipeline = LoudPipeline(1)
    scores = evaluate.score_kind(
        "sherpa_kws", CONFIG, dataset, build=lambda config: pipeline
    )
    assert (scores.heldout_positives, scores.heldout_negatives) == (0, 0)
    assert (scores.detected, scores.false_accepts) == (0, 0)
    # The ambient slice is the only audio fed.
    assert pipeline.resets == 1
    assert not scores.empty
    assert evaluate.score_kind(
        "sherpa_kws", CONFIG, VoiceDataset(tmp_path / "none"), build=lambda c: pipeline
    ).empty


def test_ambient_events_become_a_rate_from_twelve_minutes() -> None:
    scores = KindScores("sherpa_kws", "autobot", 0.5, 20, 20, 12.0, 15, 1, 3)
    assert scores.row["ambient_fa_per_hour"] == pytest.approx(15.0)
    assert scores.row["fa_rate"] == pytest.approx(0.05)
    short = KindScores("sherpa_kws", "autobot", 0.5, 20, 20, 11.9, 15, 1, 3)
    assert short.row["ambient_fa_per_hour"] is None


def test_a_kind_that_cannot_be_built_is_one_line(tmp_path: Path) -> None:
    dataset = fixture_dataset(tmp_path)

    def missing(config: Config) -> LoudPipeline:
        raise ModelMissingError("model file is missing: /somewhere/tokens.txt (...)")

    with pytest.raises(EvaluateError) as caught:
        evaluate.score_kind("sherpa_kws", CONFIG, dataset, build=missing)
    assert str(caught.value) == (
        "sherpa_kws: its model files are missing ('make fetch-models' fetches them)"
    )

    def unspellable(config: Config) -> LoudPipeline:
        raise ConfigError("wake phrase 'r2d2': the sherpa_kws model cannot spell '2'")

    with pytest.raises(EvaluateError, match="^sherpa_kws: wake phrase 'r2d2'"):
        evaluate.score_kind("sherpa_kws", CONFIG, dataset, build=unspellable)
    # A kind there is no engine for, through the real builder.
    with pytest.raises(EvaluateError) as caught:
        evaluate.score_kind("no_such_kind", CONFIG, dataset)
    assert "\n" not in str(caught.value)
    with pytest.raises(EvaluateError, match="no wake phrase 'computer'"):
        evaluate.score_kind("sherpa_kws", CONFIG, dataset, phrase="computer")


def test_the_sherpa_baseline_scores_the_fixtures(tmp_path: Path) -> None:
    try:
        require_dir(SHERPA_KWS_DIR, "sherpa-onnx keyword spotter", SHERPA_KWS_FILES)
    except ModelMissingError as error:
        pytest.fail(f"{error}. A test run never downloads one.", pytrace=False)
    dataset = fixture_dataset(tmp_path)
    scores = evaluate.score_kind(evaluate.BASELINE_KIND, CONFIG, dataset)
    # What the engine's own fixture tests measure at 0.5: both positives
    # heard, neither negative, nothing in the room noise.
    assert (scores.detected, scores.heldout_positives) == (2, 2)
    assert (scores.false_accepts, scores.heldout_negatives) == (0, 2)
    assert scores.ambient_false_accepts == 0
    assert scores.size == dataset.heldout_size("autobot")


def test_the_baseline_is_a_column_that_changes_no_verdict() -> None:
    baseline = KindScores("sherpa_kws", "autobot", 0.5, 20, 20, 12.0, 14, 2, 1)
    for candidate_detected, installed_detected in ((19, 20), (17, 20)):
        candidate = scores(candidate_detected, threshold=0.6)
        installed = scores(installed_detected, threshold=0.85)
        plain = compare(candidate, installed, LARGE)
        beside = compare(candidate, installed, LARGE, baseline=baseline)
        assert (beside.verdict, beside.offer, beside.summary, beside.reasons) == (
            plain.verdict,
            plain.offer,
            plain.summary,
            plain.reasons,
        )
        assert beside.candidate_row == plain.candidate_row
    # Three columns; the header states the one threshold.
    assert beside.lines[1].split() == ["candidate", "installed", "baseline", "0.5"]
    detected = next(line for line in beside.lines if line.startswith("positives"))
    assert detected.split("  ")[-1].strip() == "14 of 20"
    assert beside.lines[-1] == (
        "baseline: sherpa_kws, untrained, at its default threshold 0.5 only"
    )
    # Within the narrowest screen's panel (72 columns, less the frame).
    assert max(len(line) for line in beside.lines[1:]) <= 68
    # With nothing installed: the candidate and the baseline.
    alone = compare(candidate, None, LARGE, baseline=baseline)
    assert alone.lines[1].split() == ["candidate", "baseline", "0.5"]
    assert alone.offer == OFFER_INSTALL


def test_scoring_a_kind_needs_the_runtime_libraries_only(tmp_path: Path) -> None:
    fixture_dataset(tmp_path)
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "import autobot.wakeword.evaluate as evaluate\n"
        "from autobot.config import load_config\n"
        "from autobot.wakeword.dataset import VoiceDataset\n"
        "scores = evaluate.score_kind(evaluate.BASELINE_KIND, load_config(),\n"
        "                             VoiceDataset(Path(sys.argv[1])))\n"
        "assert scores.heldout_positives == 2, scores\n"
        "heavy = {'torch', 'scipy', 'faster_whisper', 'soundfile', 'sounddevice'}\n"
        "assert not heavy & set(sys.modules), heavy & set(sys.modules)\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
