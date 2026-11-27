"""Score a wake-word model on the held-out real recordings: the bar of TS-7.

`python -m autobot.wakeword.evaluate models/wakeword/candidate.onnx` prints
what the interface's Candidate screen shows: for a sweep of thresholds, how
many of the held-out real positives the model detects, how many of the
held-out real negatives it fires on, and how often it fires in the held-out
room audio, with the counts and minutes those figures rest on and the
threshold recommended on them.

What is held out is decided by `autobot.wakeword.dataset` and nowhere else:
one script prompt in five (every take of it), one unprompted clip in five,
and the last fifth of every ambient recording. A training run never cuts a
window from any of it (`autobot.wakeword.train`), so these figures are for
audio the model has not heard.

How a clip is scored: with about a second of near-silence before it, as the
engine primes itself before a stretch of speech, the whole clip is turned
into openWakeWord features and the model scores every position; the clip's
score is the peak. A clip counts as detected at a threshold its peak
reaches. The held-out ambient slices are scored at every 80 ms position,
half a minute at a time, and scores at or over a threshold within two
seconds of each other are one false accept (`train.count_events`).

False accepts per hour is a rate only from `MIN_RATE_MINUTES` of held-out
ambient: scaled up from a minute or two it would look precise and mean
nothing, so below that the count is all there is, and the output says so.

`score_kind` scores a detector kind instead of a model file: it builds the
listener's own frame path (`autobot.listener.service.build_pipeline`) for the
phrase with that kind and runs the same held-out clips and ambient slices
through it, frame by frame, as the microphone would deliver them. That is how
the `sherpa_kws` baseline, which has no model of ours and no score, gets
figures on the same real set (TS-14): at one threshold, by counting the wake
events.

This module runs with the runtime dependencies only (`onnxruntime` and
`numpy`): no torch, no scipy, no transcription. The listener's frame path and
whatever a kind's engine needs (`sherpa_onnx`, or `faster_whisper` for
`text_match`) are imported inside `score_kind`, when it is called.
`enforce_offline()` runs before `onnxruntime` is imported, here as
everywhere. Nothing is sent anywhere (CONSTRAINTS.md CN-1).

Exit status of the command: 0 when it printed an evaluation, and also when
the dataset holds no recordings for the phrase (it says so in one line: there
is nothing wrong, only nothing to score); 2 with one line on a missing or
unusable model, dataset or configuration.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

from autobot.config import (
    KIND_OPENWAKEWORD,
    KIND_SHERPA_KWS,
    Config,
    ConfigError,
    load_config,
    resolve_repo_path,
)
from autobot.listener.engines.openwakeword import (
    EMBEDDING_SIZE,
    FRAME_SAMPLES,
    MODEL_EMBEDDINGS,
    PRIME_NOISE_LEVEL,
    SAMPLE_RATE,
    onnx_session,
)
from autobot.listener.models import ModelMissingError, enforce_offline
from autobot.wakeword.dataset import (
    LABEL_NEGATIVE,
    LABEL_POSITIVE,
    VOICE_DIR,
    DatasetError,
    HeldoutSize,
    VoiceDataset,
)
from autobot.wakeword.features import ClipFeatures, embedding_count
from autobot.wakeword.train import (
    EVAL_SAMPLES,
    MIN_USEFUL_RECALL,
    SPLIT_HELDOUT,
    TARGET_FALSE_ACCEPTS_PER_HOUR,
    THRESHOLDS,
    TrainError,
    count_events,
    load_real,
    read_samples,
    stream_inputs,
)

__all__ = [
    "BASELINE_KIND",
    "BASELINE_THRESHOLD",
    "MIN_RATE_MINUTES",
    "MIN_VERDICT",
    "OFFER_INSTALL",
    "OFFER_JUDGMENT",
    "OFFER_NONE",
    "THRESHOLDS",
    "TOO_LITTLE_AMBIENT",
    "TOO_SMALL",
    "Comparison",
    "EvaluateError",
    "KindScores",
    "RealScores",
    "baseline_note",
    "compare",
    "format_sweep",
    "main",
    "recommend_real",
    "score_kind",
    "score_real",
]

type Int16 = npt.NDArray[np.int16]
type Float32 = npt.NDArray[np.float32]
# One row of a sweep: see `RealScores.rows`.
type Row = dict[str, Any]

# False accepts per hour is a rate only from this many minutes of held-out
# ambient audio: the held-out fifth of one sixty-minute recording (TS-7).
MIN_RATE_MINUTES = 12
# The held-out set a verdict needs: positives, negatives, ambient minutes (TS-8).
MIN_VERDICT = HeldoutSize(positives=20, negatives=20, ambient_minutes=12.0)
# How many clips or events worse a candidate may be on a measure and still be
# at least as good (TS-8): a difference of one is not a difference.
TOLERANCE = 1
# Thresholds under this are not recommended, as in `train.recommend`.
MIN_RECOMMENDED_THRESHOLD = 0.3

TOO_LITTLE_AMBIENT = "too little held-out ambient to judge false accepts per hour"
TOO_SMALL = "the held-out set is too small to tell the models apart"

# What `Comparison.offer` says the interface may offer.
OFFER_INSTALL = "install"
OFFER_JUDGMENT = "judgment"
OFFER_NONE = "none"

# Near-silence put before and after a clip, in seconds: the engine primes
# itself with the same before every stretch of speech.
LEAD_SECONDS = 1.0
TAIL_SECONDS = 0.5
# Ambient audio is scored this many 80 ms frames at a time. The features of
# a block of `k` frames give `k - 24` model inputs, so blocks that overlap by
# 24 frames give one score for every 80 ms with nothing lost between them.
AMBIENT_BLOCK_FRAMES = 400
BLOCK_OVERLAP_FRAMES = 24
# Model inputs scored per call.
SCORE_BATCH = 4096

# The no-training baseline the trained model is shown beside (TS-14), and the
# one threshold it is scored at: the kind's default. sherpa-onnx gives no
# score for a clip, so a sweep would be one whole pass for each threshold.
BASELINE_KIND = KIND_SHERPA_KWS
BASELINE_THRESHOLD = 0.5
# Frames of silence fed after a clip in `score_kind`, so the speech gate ends
# the stretch and the detector decodes what is left of it: more than the
# widest hangover a detector asks for (10 frames, `openwakeword`).
KIND_TAIL_FRAMES = 12


class EvaluateError(Exception):
    """The evaluation cannot run. The message is one line."""


# --- scoring -----------------------------------------------------------------


class ModelScorer:
    """One wake-word ONNX model on openWakeWord's features, for whole clips."""

    def __init__(self, model: Path, features: ClipFeatures | None = None):
        if not model.is_file():
            raise EvaluateError(f"wake-word model file is missing: {model}")
        try:
            self._features = features or ClipFeatures()
            self._session: Any = onnx_session(model)
        except ModelMissingError as error:
            raise EvaluateError(str(error)) from None
        # onnxruntime's load errors share no base class but `Exception`.
        except Exception as error:  # noqa: BLE001
            raise EvaluateError(
                f"{model} is not a wake-word model this engine can run: "
                f"{str(error).splitlines()[0] if str(error) else type(error).__name__}"
            ) from None
        model_input = self._session.get_inputs()[0]
        if tuple(model_input.shape[1:]) != (MODEL_EMBEDDINGS, EMBEDDING_SIZE):
            raise EvaluateError(
                f"{model} is not a wake-word model this engine can run: its input is "
                f"{tuple(model_input.shape[1:])}, not ({MODEL_EMBEDDINGS}, "
                f"{EMBEDDING_SIZE})"
            )
        self._input: str = model_input.name
        self._quiet = np.random.default_rng(0)

    def _scores(self, audio: Int16) -> Float32:
        """The model's score at every 80 ms position of `audio`."""
        # Whole 80 ms frames only: what the feature extractor counts in.
        audio = audio[: len(audio) // FRAME_SAMPLES * FRAME_SAMPLES]
        if embedding_count(len(audio)) < MODEL_EMBEDDINGS:
            return np.zeros(0, dtype=np.float32)
        embeddings = self._features.embed_clips(audio[np.newaxis, :])[0]
        inputs = stream_inputs(embeddings)
        scores: list[Float32] = []
        for first in range(0, len(inputs), SCORE_BATCH):
            part = np.ascontiguousarray(
                inputs[first : first + SCORE_BATCH], dtype=np.float32
            )
            scores.append(self._session.run(None, {self._input: part})[0].reshape(-1))
        return np.concatenate(scores)

    def _silence(self, samples: int) -> Int16:
        return self._quiet.normal(0.0, PRIME_NOISE_LEVEL, samples).astype(np.int16)

    def clip_peak(self, clip: Int16) -> float:
        """The highest score the model reaches on a clip heard after quiet."""
        lead = int(LEAD_SECONDS * SAMPLE_RATE)
        tail = int(TAIL_SECONDS * SAMPLE_RATE)
        # At least the 4 s the synthetic evaluation scores a clip in.
        lead += max(0, EVAL_SAMPLES - lead - len(clip) - tail)
        audio = np.concatenate((self._silence(lead), clip, self._silence(tail)))
        scores = self._scores(audio)
        return float(scores.max()) if len(scores) else 0.0

    def stream_scores(self, path: Path, first: int, last: int) -> Float32:
        """Scores for samples [first, last) of a WAV file, read a block at a time."""
        block = AMBIENT_BLOCK_FRAMES * FRAME_SAMPLES
        hop = (AMBIENT_BLOCK_FRAMES - BLOCK_OVERLAP_FRAMES) * FRAME_SAMPLES
        scores: list[Float32] = []
        position = first
        while position < last:
            count = min(block, last - position)
            scores.append(self._scores(read_samples(path, position, count)))
            if position + count >= last:
                break
            position += hop
        return np.concatenate(scores) if scores else np.zeros(0, dtype=np.float32)


@dataclass(frozen=True)
class RealScores:
    """One model's figures on the held-out real recordings (TS-7)."""

    model: Path
    phrase: str
    # What the figures rest on.
    heldout_positives: int
    heldout_negatives: int
    heldout_ambient_minutes: float
    # One row for each threshold, in rising order. Each row is a dict:
    #   "threshold"              float
    #   "detected"               int: held-out positives whose peak reaches it
    #   "recall"                 float: detected / held-out positives (0.0 of none)
    #   "false_accepts"          int: held-out negatives whose peak reaches it
    #   "fa_rate"                float: false_accepts / held-out negatives
    #   "ambient_false_accepts"  int: events in the held-out ambient audio
    #   "ambient_fa_per_hour"    float, or None below `MIN_RATE_MINUTES`
    rows: tuple[Row, ...]
    # The row `recommend_real` picks; None when there is nothing held out.
    recommended: Row | None
    # The peak score of each held-out clip, by file name.
    positive_peaks: tuple[tuple[str, float], ...] = ()
    negative_peaks: tuple[tuple[str, float], ...] = ()

    @property
    def size(self) -> HeldoutSize:
        return HeldoutSize(
            self.heldout_positives,
            self.heldout_negatives,
            self.heldout_ambient_minutes,
        )

    @property
    def empty(self) -> bool:
        """Whether there was nothing held out to score."""
        size = self.size
        return not (size.positives or size.negatives or size.ambient_minutes)

    @property
    def has_rate(self) -> bool:
        """Whether there is enough held-out ambient for false accepts per hour."""
        return self.heldout_ambient_minutes >= MIN_RATE_MINUTES

    @property
    def recommended_threshold(self) -> float | None:
        return (
            None if self.recommended is None else float(self.recommended["threshold"])
        )

    def row_at(self, threshold: float) -> Row | None:
        """The sweep row for a threshold, or None when it is not in the sweep."""
        for row in self.rows:
            if abs(row["threshold"] - threshold) < 1e-9:
                return row
        return None

    def to_json(self) -> dict[str, Any]:
        """The real-set fields `train.stage_eval` adds to the metrics file's `real`."""
        return {
            "sweep": list(self.rows),
            "recommended_threshold": self.recommended_threshold,
            "metrics_at_threshold": self.recommended,
            "heldout_positives": self.heldout_positives,
            "heldout_negatives": self.heldout_negatives,
            "minutes_ambient": self.heldout_ambient_minutes,
            "positive_peaks": dict(self.positive_peaks),
            "negative_peaks": dict(self.negative_peaks),
        }


def sweep_rows(
    positive_peaks: Sequence[float],
    negative_peaks: Sequence[float],
    ambient_scores: npt.NDArray[Any],
    ambient_minutes: float,
    thresholds: Sequence[float] = THRESHOLDS,
) -> tuple[Row, ...]:
    """The sweep, from the clips' peak scores and the ambient score stream."""
    positives = np.asarray(positive_peaks, dtype=np.float64)
    negatives = np.asarray(negative_peaks, dtype=np.float64)
    rows: list[Row] = []
    for threshold in thresholds:
        detected = int((positives >= threshold).sum())
        false_accepts = int((negatives >= threshold).sum())
        events = count_events(ambient_scores, threshold)
        rows.append(
            {
                "threshold": float(threshold),
                "detected": detected,
                "recall": detected / len(positives) if len(positives) else 0.0,
                "false_accepts": false_accepts,
                "fa_rate": false_accepts / len(negatives) if len(negatives) else 0.0,
                "ambient_false_accepts": events,
                "ambient_fa_per_hour": events / (ambient_minutes / 60)
                if ambient_minutes >= MIN_RATE_MINUTES
                else None,
            }
        )
    return tuple(rows)


def recommend_real(rows: Sequence[Row]) -> Row | None:
    """The sweep row to run at, chosen on the real figures; None for no rows.

    `train.recommend`'s rule with the held-out ambient in the validation
    stream's place: the best recall among the thresholds, from 0.3 up, that
    keep ambient false accepts to one an hour, or, where there is too little
    ambient for a rate, to none at all in the minutes there are. A held-out
    set is small, so many thresholds detect the same clips; among those the
    ones with the fewest false accepts on the held-out negatives are kept,
    and of the thresholds that are then equal on everything measured, the
    middle one is recommended: it leaves as much room below the scores of
    the positives as above the scores of what must not fire. (The lowest
    would invite false accepts that a minute of ambient cannot show, and the
    highest would leave a model that scores 1.0 on every held-out positive
    no margin at all for a voice across the room.) When no threshold keeps
    the ambient quiet, the strictest one that still hears the phrase
    `MIN_USEFUL_RECALL` of the time (the strictest of all when none does).
    """
    if not rows:
        return None

    def quiet(row: Row) -> bool:
        rate = row["ambient_fa_per_hour"]
        if rate is None:
            return bool(row["ambient_false_accepts"] == 0)
        return bool(rate <= TARGET_FALSE_ACCEPTS_PER_HOUR)

    good = [
        row
        for row in rows
        if quiet(row) and row["threshold"] >= MIN_RECOMMENDED_THRESHOLD
    ]
    if good:
        best = max((row["recall"], -row["false_accepts"]) for row in good)
        equal = sorted(
            (row for row in good if (row["recall"], -row["false_accepts"]) == best),
            key=lambda row: row["threshold"],
        )
        return equal[len(equal) // 2]
    usable = [row for row in rows if row["recall"] >= MIN_USEFUL_RECALL]
    return max(usable or rows, key=lambda row: row["threshold"])


def score_real(
    model: Path,
    dataset: VoiceDataset,
    phrase: str,
    thresholds: Sequence[float] = THRESHOLDS,
) -> RealScores:
    """Score an ONNX model on the dataset's held-out clips and ambient slices.

    The held-out set is read from the dataset as it is now, so two models
    scored one after the other are scored on the same clips. With nothing
    held out the result is `empty` and its `recommended` is None. Raises
    `EvaluateError` for a missing or unusable model and `DatasetError` for an
    unreadable dataset, each with one line.
    """
    try:
        real = load_real(dataset, phrase)
    except TrainError as error:
        raise EvaluateError(str(error)) from None
    scorer = ModelScorer(model)
    peaks: dict[str, list[tuple[str, float]]] = {LABEL_POSITIVE: [], LABEL_NEGATIVE: []}
    ambient: list[Float32] = []
    for clip in real.clips:
        record = clip.record
        if clip.split == SPLIT_HELDOUT:
            samples = dataset.load(record).samples
            peaks[record.label].append((record.file, scorer.clip_peak(samples)))
        elif clip.heldout[1] > clip.heldout[0]:
            ambient.append(scorer.stream_scores(dataset.path(record), *clip.heldout))
    # The dataset's own figure, so that the twelve-minute line is drawn in
    # one place: the interface's `heldout_size` and this agree to the digit.
    minutes = dataset.heldout_size(phrase).ambient_minutes
    rows = sweep_rows(
        [peak for _, peak in peaks[LABEL_POSITIVE]],
        [peak for _, peak in peaks[LABEL_NEGATIVE]],
        np.concatenate(ambient) if ambient else np.zeros(0, dtype=np.float32),
        minutes,
        thresholds,
    )
    nothing = not (peaks[LABEL_POSITIVE] or peaks[LABEL_NEGATIVE] or minutes)
    return RealScores(
        model=model,
        phrase=phrase,
        heldout_positives=len(peaks[LABEL_POSITIVE]),
        heldout_negatives=len(peaks[LABEL_NEGATIVE]),
        heldout_ambient_minutes=minutes,
        rows=rows,
        recommended=None if nothing else recommend_real(rows),
        positive_peaks=tuple(peaks[LABEL_POSITIVE]),
        negative_peaks=tuple(peaks[LABEL_NEGATIVE]),
    )


# --- scoring a detector kind -------------------------------------------------


@dataclass(frozen=True)
class KindScores:
    """One detector kind's figures on the held-out real recordings, at one threshold.

    What `score_kind` returns. A kind is scored by the wake events it gives,
    so there is one row, not a sweep: `row` has the shape of a `RealScores`
    row and goes beside the models' rows in `compare`'s table.
    """

    kind: str
    phrase: str
    threshold: float
    heldout_positives: int
    heldout_negatives: int
    heldout_ambient_minutes: float
    # Held-out positives the kind gave a wake event for.
    detected: int
    # Held-out negatives it gave a wake event for.
    false_accepts: int
    # Wake events in the held-out ambient audio.
    ambient_false_accepts: int

    @property
    def size(self) -> HeldoutSize:
        return HeldoutSize(
            self.heldout_positives,
            self.heldout_negatives,
            self.heldout_ambient_minutes,
        )

    @property
    def empty(self) -> bool:
        """Whether there was nothing held out to score."""
        size = self.size
        return not (size.positives or size.negatives or size.ambient_minutes)

    @property
    def row(self) -> Row:
        """The figures as a sweep row: see `RealScores.rows`."""
        positives, negatives = self.heldout_positives, self.heldout_negatives
        minutes = self.heldout_ambient_minutes
        return {
            "threshold": self.threshold,
            "detected": self.detected,
            "recall": self.detected / positives if positives else 0.0,
            "false_accepts": self.false_accepts,
            "fa_rate": self.false_accepts / negatives if negatives else 0.0,
            "ambient_false_accepts": self.ambient_false_accepts,
            "ambient_fa_per_hour": self.ambient_false_accepts / (minutes / 60)
            if minutes >= MIN_RATE_MINUTES
            else None,
        }


class FramePath(Protocol):
    """What `score_kind` needs of the listener's `Pipeline`."""

    def process(self, frame: Int16) -> Sequence[object]: ...

    def reset(self) -> None: ...


def _listener_pipeline(config: Config) -> FramePath:
    # Imported here: the listener's frame path loads the VAD and the engine.
    from autobot.listener.service import build_pipeline

    return build_pipeline(config)


def _events(pipeline: FramePath, audio: Int16, frame_samples: int) -> int:
    """The wake events for `audio` fed in whole frames; the last one is padded."""
    events = 0
    for first in range(0, len(audio), frame_samples):
        frame = audio[first : first + frame_samples]
        if len(frame) < frame_samples:
            frame = np.concatenate(
                (frame, np.zeros(frame_samples - len(frame), dtype=np.int16))
            )
        events += len(pipeline.process(frame))
    return events


def _tail_events(pipeline: FramePath, frame_samples: int) -> int:
    """The wake events the silence after the audio brings out."""
    silence = np.zeros(frame_samples, dtype=np.int16)
    return sum(len(pipeline.process(silence)) for _ in range(KIND_TAIL_FRAMES))


def score_kind(
    kind: str,
    config: Config,
    dataset: VoiceDataset,
    *,
    phrase: str | None = None,
    threshold: float = BASELINE_THRESHOLD,
    build: Callable[[Config], FramePath] | None = None,
) -> KindScores:
    """Score a detector kind on the dataset's held-out clips and ambient slices.

    The phrase (the first configured one unless `phrase` names another) is
    given the kind and the threshold, and the listener's own frame path is
    built for it alone: the speech gate, then the detector. `model` is kept
    for `openwakeword` and emptied for the kinds that ignore it. Every
    held-out clip is fed in frames from a fresh start, followed by
    `KIND_TAIL_FRAMES` of silence; a clip counts as detected (a positive) or
    as a false accept (a negative) when the detector gave any wake event. The
    held-out ambient slices are fed the same way and every wake event in them
    is a false accept. There is no sweep: a kind is scored at `threshold`.

    `build` stands in for `autobot.listener.service.build_pipeline` in tests.
    Raises `EvaluateError` with one line when the kind cannot be built (its
    model files are missing, the phrase cannot be spelled, the kind is not
    known) and `DatasetError` for an unreadable dataset.
    """
    # The listener's own errors, imported here with its detector registry.
    from autobot.listener.detector import UnknownKindError

    if phrase is None:
        wanted = config.wake_phrases[0]
    else:
        matches = [
            entry
            for entry in config.wake_phrases
            if entry.phrase.strip().lower() == phrase.strip().lower()
        ]
        if not matches:
            raise EvaluateError(f"no wake phrase {phrase!r} is configured")
        wanted = matches[0]
    one = dataclasses.replace(
        wanted,
        kind=kind,
        model=wanted.model if kind == KIND_OPENWAKEWORD else "",
        threshold=threshold,
    )
    try:
        real = load_real(dataset, one.phrase)
    except TrainError as error:
        raise EvaluateError(str(error)) from None
    frame_samples = config.audio.frame_samples
    # Before a speech library can be imported.
    enforce_offline()
    try:
        pipeline = (build or _listener_pipeline)(
            dataclasses.replace(config, wake_phrases=(one,))
        )
    except ModelMissingError:
        raise EvaluateError(
            f"{kind}: its model files are missing ('make fetch-models' fetches them)"
        ) from None
    except (ConfigError, UnknownKindError) as error:
        raise EvaluateError(f"{kind}: {error}") from None
    # A speech library that is not installed, or a model it cannot load.
    except Exception as error:  # noqa: BLE001
        raise EvaluateError(
            f"{kind} cannot be run: "
            f"{str(error).splitlines()[0] if str(error) else type(error).__name__}"
        ) from None
    hits = {LABEL_POSITIVE: 0, LABEL_NEGATIVE: 0}
    seen = {LABEL_POSITIVE: 0, LABEL_NEGATIVE: 0}
    ambient_events = 0
    block = AMBIENT_BLOCK_FRAMES * frame_samples
    for clip in real.clips:
        record = clip.record
        if clip.split == SPLIT_HELDOUT:
            loaded = dataset.load(record)
            if loaded.sample_rate != config.audio.sample_rate:
                raise EvaluateError(
                    f"{dataset.path(record)} is at {loaded.sample_rate} Hz, not the "
                    f"configured {config.audio.sample_rate}"
                )
            pipeline.reset()
            events = _events(pipeline, loaded.samples, frame_samples)
            events += _tail_events(pipeline, frame_samples)
            seen[record.label] += 1
            hits[record.label] += bool(events)
        elif clip.heldout[1] > clip.heldout[0]:
            pipeline.reset()
            first, last = clip.heldout
            try:
                for position in range(first, last, block):
                    count = min(block, last - position)
                    ambient_events += _events(
                        pipeline,
                        read_samples(dataset.path(record), position, count),
                        frame_samples,
                    )
            except TrainError as error:
                raise EvaluateError(str(error)) from None
            ambient_events += _tail_events(pipeline, frame_samples)
    return KindScores(
        kind=kind,
        phrase=one.phrase,
        threshold=threshold,
        heldout_positives=seen[LABEL_POSITIVE],
        heldout_negatives=seen[LABEL_NEGATIVE],
        # The dataset's own figure, as in `score_real`.
        heldout_ambient_minutes=dataset.heldout_size(one.phrase).ambient_minutes,
        detected=hits[LABEL_POSITIVE],
        false_accepts=hits[LABEL_NEGATIVE],
        ambient_false_accepts=ambient_events,
    )


def baseline_note(scores: KindScores) -> str:
    """One line under the table that says what the baseline column is."""
    return (
        f"baseline: {scores.kind}, untrained, at its default threshold "
        f"{scores.threshold:g} only"
    )


# --- comparing ---------------------------------------------------------------


@dataclass(frozen=True)
class Comparison:
    """A candidate beside the installed model, on the same held-out set (TS-8)."""

    # True: the candidate is at least as good. False: it is worse. None: no
    # verdict, because nothing is installed, the held-out set is below
    # `MIN_VERDICT`, or a model has no figures.
    verdict: bool | None
    # Whether there was no installed model to compare with: nothing to beat.
    nothing_installed: bool
    # Whether the held-out set reaches `MIN_VERDICT`.
    large_enough: bool
    # The held-out set the figures rest on.
    size: HeldoutSize
    # One line that says what the verdict is and why.
    summary: str
    # For a worse candidate, each measure it is worse on, one line each.
    reasons: tuple[str, ...]
    # The side-by-side table, a line per element: the held-out counts and
    # minutes, each model at its own recommended threshold and, below
    # `MIN_RATE_MINUTES`, the `TOO_LITTLE_AMBIENT` line with the counts.
    lines: tuple[str, ...]
    # The rows compared: each model's recommended row, or None.
    candidate_row: Row | None
    installed_row: Row | None

    @property
    def table(self) -> str:
        """`lines` as one string, without the summary."""
        return "\n".join(self.lines)

    @property
    def offer(self) -> str:
        """What the interface may offer.

        `OFFER_INSTALL`: nothing is installed, or the candidate is at least
        as good. `OFFER_JUDGMENT`: a model is installed and there is no
        verdict (the held-out set is too small, or empty), so the owner may
        install on his own judgment, after the `TOO_SMALL` warning.
        `OFFER_NONE`: the candidate is worse.
        """
        if self.nothing_installed or self.verdict is True:
            return OFFER_INSTALL
        return OFFER_JUDGMENT if self.verdict is None else OFFER_NONE


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def describe_size(size: HeldoutSize) -> str:
    """ "11 positives, 6 negatives, 1.0 min ambient"."""
    return (
        f"{_count(size.positives, 'positive')}, {_count(size.negatives, 'negative')}, "
        f"{size.ambient_minutes:.1f} min ambient"
    )


def _column(row: Row | None, size: HeldoutSize) -> list[str]:
    if row is None:
        return ["-"] * 6
    rate = row["ambient_fa_per_hour"]
    return [
        f"{row['threshold']:g}",
        f"{row['detected']} of {size.positives}",
        f"{row['recall']:.3f}",
        f"{row['false_accepts']} of {size.negatives}",
        str(row["ambient_false_accepts"]),
        "-" if rate is None else f"{rate:.2f}",
    ]


_MEASURES = (
    "threshold (recommended)",
    "positives detected",
    "recall",
    "false accepts on negatives",
    "false accepts in ambient",
    "false accepts per hour",
)


def compare(
    candidate: RealScores,
    installed: RealScores | None,
    size: HeldoutSize,
    *,
    labels: tuple[str, str] = ("candidate", "installed"),
    baseline: KindScores | None = None,
) -> Comparison:
    """Compare a candidate with the installed model, each at its own threshold.

    `installed` is None when no model is installed: there is nothing to beat
    and no verdict is needed. Otherwise a verdict is given only when `size`
    reaches `MIN_VERDICT`. With a verdict, the candidate is at least as good
    when it detects no more than one fewer held-out positive, makes no more
    than one more false accept on the held-out negatives, and no more than
    one more in the held-out ambient audio; a tie on every measure is good.
    The comparison is of counts, not rates: one clip or one event is the
    unit (TS-8). `size` is the dataset's `heldout_size(phrase)`, the set
    both models were scored on. `labels` names the two columns.

    `baseline` adds a last column to the table: a detector kind's figures on
    the same set (`score_kind`), headed by its one threshold, with
    `baseline_note` under the table. It is there to be read. It takes no part
    in the verdict, the summary or the offer.
    """
    candidate_row = candidate.recommended
    installed_row = None if installed is None else installed.recommended
    large_enough = (
        size.positives >= MIN_VERDICT.positives
        and size.negatives >= MIN_VERDICT.negatives
        and size.ambient_minutes >= MIN_VERDICT.ambient_minutes
    )
    reasons: list[str] = []
    verdict: bool | None = None
    if installed is None:
        summary = "nothing is installed: the candidate has nothing to beat"
    elif candidate_row is None or installed_row is None:
        summary = "there are no held-out real recordings to compare the models on"
    elif not large_enough:
        summary = (
            f"{TOO_SMALL}: a verdict needs {MIN_VERDICT.positives} held-out "
            f"positives, {MIN_VERDICT.negatives} negatives and "
            f"{MIN_VERDICT.ambient_minutes:g} min of ambient"
        )
    else:
        fewer = installed_row["detected"] - candidate_row["detected"]
        if fewer > TOLERANCE:
            reasons.append(f"it detects {fewer} fewer held-out positives")
        more = candidate_row["false_accepts"] - installed_row["false_accepts"]
        if more > TOLERANCE:
            reasons.append(f"it makes {more} more false accepts on the negatives")
        more = (
            candidate_row["ambient_false_accepts"]
            - installed_row["ambient_false_accepts"]
        )
        if more > TOLERANCE:
            reasons.append(f"it makes {more} more false accepts in the ambient audio")
        verdict = not reasons
        summary = (
            f"the {labels[0]} is at least as good as the {labels[1]} model"
            if verdict
            else f"the {labels[0]} is worse than the {labels[1]} model: "
            + "; ".join(reasons)
        )

    columns = [_column(candidate_row, size)]
    heads = [labels[0]]
    if installed is not None:
        columns.append(_column(installed_row, size))
        heads.append(labels[1])
    if baseline is not None:
        columns.append(_column(baseline.row, size))
        heads.append(f"baseline {baseline.threshold:g}")
    width = max(12, *(len(text) for column in (*columns, heads) for text in column))
    name_width = max(len(measure) for measure in _MEASURES)
    lines = [
        f"held-out real recordings: {describe_size(size)}",
        " " * name_width + "".join(f"  {head:>{width}}" for head in heads),
    ]
    for index, measure in enumerate(_MEASURES):
        cells = "".join(f"  {column[index]:>{width}}" for column in columns)
        lines.append(f"{measure:<{name_width}}{cells}")
    if size.ambient_minutes < MIN_RATE_MINUTES:
        counts = " and ".join(
            str(row["ambient_false_accepts"])
            for row in (candidate_row, installed_row)
            if row is not None
        )
        lines.append(
            f"{TOO_LITTLE_AMBIENT}: {counts or 'no'} false accepts in "
            f"{size.ambient_minutes:.1f} min"
        )
    if baseline is not None:
        lines.append(baseline_note(baseline))
    return Comparison(
        verdict=verdict,
        nothing_installed=installed is None,
        large_enough=large_enough,
        size=size,
        summary=summary,
        reasons=tuple(reasons),
        lines=tuple(lines),
        candidate_row=candidate_row,
        installed_row=installed_row,
    )


# --- the command line --------------------------------------------------------


def format_sweep(scores: RealScores) -> list[str]:
    """The real sweep as lines: what the command prints and the training logs."""
    size = scores.size
    lines = [
        f"held out: {describe_size(size)}",
        "  thr  detected  recall  false accepts  fa rate  ambient fa  ambient fa/h",
    ]
    for row in scores.rows:
        rate = row["ambient_fa_per_hour"]
        detected = f"{row['detected']}/{size.positives}"
        false_accepts = f"{row['false_accepts']}/{size.negatives}"
        lines.append(
            f"{row['threshold']:5.2f}  {detected:>8}  {row['recall']:6.3f}  "
            f"{false_accepts:>13}  {row['fa_rate']:7.3f}  "
            f"{row['ambient_false_accepts']:10d}  "
            + (f"{'-':>12}" if rate is None else f"{rate:12.2f}")
        )
    best = scores.recommended
    if best is None:
        lines.append("nothing is held out: no threshold can be recommended")
        return lines
    if scores.has_rate:
        ambient = (
            f"{best['ambient_fa_per_hour']:.2f} false accepts per hour in "
            f"{size.ambient_minutes:.1f} min of held-out ambient"
        )
    else:
        ambient = (
            f"{_count(best['ambient_false_accepts'], 'false accept')} in "
            f"{size.ambient_minutes:.1f} min of held-out ambient"
        )
        lines.append(f"{TOO_LITTLE_AMBIENT}: {ambient} at the recommended threshold")
    lines.append(
        f"recommended threshold {best['threshold']:g}: detects {best['detected']} of "
        f"{size.positives} held-out positives (recall {best['recall']:.3f}), "
        f"{best['false_accepts']} of {size.negatives} false accepts on the held-out "
        f"negatives, {ambient}"
    )
    return lines


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m autobot.wakeword.evaluate",
        description="Score a wake-word model on the held-out real recordings.",
    )
    parser.add_argument("model", type=Path, help="the .onnx file to score")
    parser.add_argument(
        "--phrase",
        default=None,
        help="the wake phrase (default: the first [[wake_phrases]] entry)",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=None,
        help="the voice dataset's directory (default: data/voice in the checkout)",
    )
    parser.add_argument(
        "--against",
        type=Path,
        default=None,
        help="another .onnx file to score on the same recordings and compare "
        "with, as the interface compares a candidate with the installed model",
    )
    parser.add_argument(
        "--baseline",
        action="store_true",
        help=f"also score the {BASELINE_KIND} baseline, which needs no training, "
        f"on the same recordings at threshold {BASELINE_THRESHOLD:g}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Before onnxruntime can be imported.
    enforce_offline()
    try:
        phrase = args.phrase or load_config().wake_phrases[0].phrase
        root = (args.dataset or resolve_repo_path(VOICE_DIR)).resolve()
        dataset = VoiceDataset(root)
        if not dataset.for_phrase(phrase):
            print(f"no real recordings for {phrase!r} in {root}: nothing to evaluate")
            return 0
        scores = score_real(args.model, dataset, phrase)
        print(f"real-set evaluation of {args.model} for {phrase!r} (dataset {root})")
        for line in format_sweep(scores):
            print(line)
        baseline: KindScores | None = None
        if args.baseline:
            try:
                baseline = score_kind(
                    BASELINE_KIND, load_config(), dataset, phrase=phrase
                )
            except EvaluateError as error:
                # The baseline is beside the point of the command: say so, go on.
                print(f"baseline not scored: {error}")
        if args.against is not None or baseline is not None:
            other = (
                None
                if args.against is None
                else score_real(args.against, dataset, phrase)
            )
            comparison = compare(
                scores,
                other,
                dataset.heldout_size(phrase),
                labels=(
                    args.model.stem,
                    "installed" if args.against is None else args.against.stem,
                ),
                baseline=baseline,
            )
            print()
            print(comparison.table)
            if other is not None:
                print(comparison.summary)
    except (ConfigError, DatasetError, EvaluateError) as error:
        print(error, file=sys.stderr, flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
