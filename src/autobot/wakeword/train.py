"""Train the wake-word model for one phrase: `python -m autobot.wakeword.train`.

`make train-wakeword` runs this. It builds the small model that the
`openwakeword` engine scores audio with, on the CPU, and can take hours, so
every stage caches what it makes and a rerun carries on where the last one
stopped.

Stages, each cached under `models/wakeword/work/<phrase>/`:

1. **synth**: piper-tts clips, one shard file per voice and batch
   (`clips/<shard>.npz`): the phrase alone and in carrier sentences
   ("hey autobot, what time is it"), hard negatives (words that sound like
   it), and generic speech, with the voice, speaker, speed and expressiveness
   varied. The phoneme alignment gives the samples where the phrase starts
   and ends.
2. **feats**: each clip is placed in a window, given reverb, level, background
   noise and sometimes background speech, and turned into openWakeWord
   features (`feats/<shard>.npz`). A training window is 3 s: 28 embeddings,
   so 13 model inputs, each labelled by where the phrase ends in it.
3. **real**: the owner's recordings in the voice dataset (`data/voice/`,
   `autobot.wakeword.dataset`). The training side of the dataset's split is
   cut into the same 3 s windows, one as recorded and two through the
   augmentation, and featurised (`real/<manifest-sha1>.npz`). The held-out
   side is never cut into a training window: the held-out prompts, and the
   last fifth of every ambient recording, are what the evaluation scores.
4. **train**: a small network on the 16 x 96 features, against those windows
   and openWakeWord's general negative features. When there are real
   recordings, half of every batch of positives and a third of every batch
   of negatives are real, so a session of fifty clips is heard as often as
   the synthetic set. Checkpointed every 1000 steps
   (`ckpt-<hidden>-<steps>[-variant].pt`: one per network size, schedule,
   loss and kind of data, so trying another does not throw a finished run
   away).
5. **eval**: recall on voices never used in training, false accepts on hard
   negatives, and false accepts per hour on openWakeWord's validation
   features, for a sweep of thresholds. The hard negatives that sound the
   same as the phrase (`PhraseTable.train_excluded`) are reported on their
   own and are not counted with the others. Then, separately, the real
   figures: the exported ONNX is scored on the held-out real recordings by
   `autobot.wakeword.evaluate`, and the threshold is recommended on them
   when there is a held-out real positive to recommend it on.
6. **export**: the ONNX file and a `.json` of the metrics are written as a
   *candidate* (`models/wakeword/candidate.onnx` and `candidate.json`) with
   `os.replace`. Nothing here installs a model: the installed model file and
   `config/autobot.toml` are never touched. The interface (`make deploy`)
   compares the candidate with the installed model and installs it when the
   owner confirms (`autobot.wakeword.install`).

The hard negatives that sound the same as the phrase
(`PhraseTable.train_excluded`) are left out of the training by default: of
the models compared on the same cached features, the one trained without
them missed least at low false-accept rates. `--no-exclude-near-homophones`
trains on them again. `--hard-topk-weight` weights the loss on the hardest
hard negatives (0 drops it) and `--negative-weight` weights every negative
term. `--name` writes the result to `candidates/<name>.onnx` instead, for
experiments; the interface installs only `candidate.onnx`. `--dataset` reads
the recordings from another directory than `data/voice/`.

Shards are additive: a larger `--scale` adds shards and keeps every finished
one, so a short first run is not wasted. A shard is identified by its name
alone and a finished shard is never remade.

Nothing here opens a network connection (CONSTRAINTS.md CN-1). The voices and
data sets are fetched beforehand by `make fetch-models TRAIN=1`; a missing
one stops the run with one line saying so.

The training-only libraries (`torch`, `scipy`, `soundfile`, `onnx`) are the
`train` extra of `pyproject.toml` and are imported inside the functions that
use them, as is `faster_whisper`, so importing this module needs `numpy`
only and `autobot.wakeword.evaluate` can use what it shares with it.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import math
import os
import sys
import time
import warnings
import wave
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from autobot.config import ConfigError, load_config, resolve_repo_path
from autobot.listener.engines.openwakeword import (
    EMBEDDING_SIZE,
    FRAME_SAMPLES,
    MODEL_EMBEDDINGS,
    PRIME_NOISE_LEVEL,
    SAMPLE_RATE,
)
from autobot.listener.models import (
    FASTER_WHISPER_MODEL_DIR,
    MODELS_DIR,
    WAKEWORD_MODELS_DIR,
    enforce_offline,
)
from autobot.wakeword.data import (
    HELDOUT_VOICES,
    NAMES,
    PREFIXES,
    SENTENCES,
    SUFFIXES,
    TRAIN_VOICES,
    PhraseTable,
    phrase_table,
)
from autobot.wakeword.dataset import (
    HELDOUT,
    LABEL_AMBIENT,
    LABEL_NEGATIVE,
    LABEL_POSITIVE,
    VOICE_DIR,
    ClipRecord,
    DatasetError,
    VoiceDataset,
    ambient_slices,
    split,
)

type Int16 = npt.NDArray[np.int16]
type Float32 = npt.NDArray[np.float32]
type Int64 = npt.NDArray[np.int64]

SPLIT_TRAIN = "train"
SPLIT_HELDOUT = "heldout"
# A real ambient recording is on both sides: split by time, not by clip.
SPLIT_AMBIENT = "ambient"

# The file a training run writes, beside the installed model: the candidate.
CANDIDATE_NAME = "candidate.onnx"

# A training window is 3 s: 28 embeddings, 13 model inputs of 16 embeddings.
SEQUENCE_SAMPLES = 3 * SAMPLE_RATE
SEQUENCE_INPUTS = 13
# An evaluation window is 4 s: 41 embeddings, 26 model inputs.
EVAL_SAMPLES = 4 * SAMPLE_RATE
# Model input `t` of a window ends at this sample.
INPUT_END_SAMPLE = 31600
# A model input is positive when the phrase ended this long before its end.
POSITIVE_AFTER_SECONDS = (0.0, 0.5)

# Clips at scale 1.0: (positives, hard negatives, generic speech).
TRAIN_CLIPS = (9000, 9000, 5000)
HELDOUT_CLIPS = (2000, 2000, 800)
SHARD_CLIPS = 400
# What one training shard holds, in the proportions of `TRAIN_CLIPS`.
TRAIN_SHARD = (157, 156, 87)
# Shards of windows with noise and no speech.
NOISE_SHARD_WINDOWS = 1000
NOISE_SHARDS_PER_SCALE = 6
HELDOUT_NOISE_WINDOWS = 600

# Clip kinds, as stored in the shards.
KIND_POSITIVE = 0
KIND_HARD = 1
KIND_GENERIC = 2
KIND_NOISE = 3

# A training window gets other speech behind it this often.
BACKGROUND_SPEECH_CHANCE = 0.25

# One training batch: positives, negatives from the windows, general negatives.
POSITIVE_BATCH = 256
NEGATIVE_BATCH = 384
GENERAL_BATCH = 1536
# How many of each come from the real recordings, when there are any.
REAL_POSITIVE_BATCH = POSITIVE_BATCH // 2
REAL_NEGATIVE_BATCH = NEGATIVE_BATCH // 3

# Windows cut for each real training clip, and for each 3 s of real ambient
# audio: the first as recorded, the others through the `Augmenter`.
REAL_VARIANTS = 3
# Part of the name of the cached real features: raise it when the way they
# are cut or labelled changes, so an old cache is not taken for a new one.
REAL_FEATURES_VERSION = 1
# How long the phrase is taken to be when only its end is known. Erring long
# is safe: the model inputs that end inside the phrase are ignored, not
# trained as negatives.
REAL_PHRASE_SECONDS = 1.0
# Real windows featurised at a time, so an hour of ambient audio is never
# held in memory.
REAL_FEATURE_BATCH = 256

CHECKPOINT_EVERY = 1000
PROGRESS_EVERY = 250
# Detections closer than this many frames count as one (2 s).
REFRACTORY_FRAMES = 25
# Steps of 0.05 where a threshold is likely to be chosen: recall falls by
# several points from one to the next.
THRESHOLDS = (
    *(0.1, 0.2, 0.3, 0.4, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9),
    *(0.95, 0.97, 0.99),
)
# The recommended threshold keeps false accepts on the validation stream to
# this. That stream is 10.7 hours of unbroken speech, music and noise, which
# is more than a room produces in an hour; holding it to one every two hours
# instead costs about 15 points of recall.
TARGET_FALSE_ACCEPTS_PER_HOUR = 1.0
# Thresholds the log shows for the phrases that sound like the wake phrase;
# the metrics file has every threshold.
NEAR_LOG_THRESHOLDS = (0.5, 0.7, 0.9)
# Below this recall a threshold is not worth recommending.
MIN_USEFUL_RECALL = 0.65

ACAV_FEATURES = Path("data/features/acav100m_head.npy.raw")
ACAV_HEADER_BYTES = 128
VALIDATION_FEATURES = Path("data/features/validation_set_features.npy")
RIR_DIR = Path("data/rir")
NOISE_DIR = Path("data/noise/esc50")

FETCH_HINT = "run 'make fetch-models TRAIN=1'"

_PUNCTUATION = frozenset("^$_,.?!;:'\"-")


class TrainError(Exception):
    """The training cannot run. The message is one line."""


def log(message: str) -> None:
    """One timestamped, flushed line: the terminal user interface shows them live."""
    print(f"{time.strftime('%H:%M:%S')} {message}", flush=True)


def seed_of(name: str) -> int:
    """A stable seed from a name, so a shard is the same whenever it is made."""
    return int(hashlib.sha1(name.encode()).hexdigest()[:8], 16)


def slug(phrase: str) -> str:
    """The phrase as a file name: "Hey there" -> "hey_there"."""
    cleaned = "".join(c if c.isalnum() else " " for c in phrase.lower())
    return "_".join(cleaned.split()) or "phrase"


def save_npz(path: Path, **arrays: npt.NDArray[Any]) -> None:
    """Write atomically: a killed run never leaves a half-written shard."""
    temporary = path.with_name(path.name + ".tmp.npz")
    np.savez(temporary, **arrays)  # type: ignore[arg-type]
    os.replace(temporary, path)


# --- the plan ----------------------------------------------------------------


@dataclass(frozen=True)
class Shard:
    """One cached unit of work: the clips of one voice, or windows of noise."""

    name: str
    split: str
    # The piper voice; empty for a shard of noise.
    voice: str = ""
    positives: int = 0
    hard: int = 0
    generic: int = 0
    # Windows of noise without speech; such a shard has no clips.
    noise: int = 0

    @property
    def clips(self) -> int:
        return self.positives + self.hard + self.generic


def plan_shards(scale: float) -> list[Shard]:
    """Every shard a run at `scale` uses, in a fixed order.

    The plan for a larger scale holds every shard of a smaller one, unchanged:
    scale only adds training shards. The held-out shards do not depend on the
    scale, so models trained at different scales are evaluated on the same
    clips.
    """
    if scale <= 0:
        raise TrainError("--scale must be above 0")
    shards: list[Shard] = []
    total = sum(TRAIN_VOICES.values())
    for voice, share in TRAIN_VOICES.items():
        clips = sum(TRAIN_CLIPS) * scale * share / total
        positives, hard, generic = TRAIN_SHARD
        for index in range(max(1, round(clips / SHARD_CLIPS))):
            shards.append(
                Shard(
                    f"train-{voice}-{index:03d}",
                    SPLIT_TRAIN,
                    voice,
                    positives,
                    hard,
                    generic,
                )
            )
    total = sum(HELDOUT_VOICES.values())
    for voice, share in HELDOUT_VOICES.items():
        counts = [round(count * share / total) for count in HELDOUT_CLIPS]
        pieces = max(1, -(-sum(counts) // SHARD_CLIPS))
        for index in range(pieces):
            part = [
                count // pieces + (1 if index < count % pieces else 0)
                for count in counts
            ]
            shards.append(
                Shard(f"heldout-{voice}-{index:03d}", SPLIT_HELDOUT, voice, *part)
            )
    for index in range(max(1, round(NOISE_SHARDS_PER_SCALE * scale))):
        shards.append(
            Shard(
                f"noise-train-{NOISE_SHARD_WINDOWS}-{index}",
                SPLIT_TRAIN,
                noise=NOISE_SHARD_WINDOWS,
            )
        )
    shards.append(
        Shard(
            f"noise-heldout-{HELDOUT_NOISE_WINDOWS}-0",
            SPLIT_HELDOUT,
            noise=HELDOUT_NOISE_WINDOWS,
        )
    )
    return shards


def clip_path(work: Path, shard: Shard) -> Path:
    return work / "clips" / f"{shard.name}.npz"


def feat_path(work: Path, shard: Shard) -> Path:
    suffix = ".seq.npz" if shard.split == SPLIT_TRAIN else ".npz"
    return work / "feats" / f"{shard.name}{suffix}"


def pending(shards: Sequence[Shard], path_of: Callable[[Shard], Path]) -> list[Shard]:
    """The shards whose file does not exist yet: what a rerun still has to do."""
    return [shard for shard in shards if not path_of(shard).exists()]


# --- stage 1: synthesis ------------------------------------------------------

# (kind, text, first word of the key phrase, word after it, hard-negative id)
type Text = tuple[int, str, int, int, int]


def make_texts(
    rng: np.random.Generator, shard: Shard, table: PhraseTable
) -> list[Text]:
    """What the clips of a shard say. Deterministic for a given `rng`."""
    texts: list[Text] = []
    trained = table.trained
    # The held-out clips lean on the hard negatives training never saw.
    unseen = [text for text in table.hard if text in table.eval_only]
    hard = list(trained) if shard.split == SPLIT_TRAIN else [*trained, *unseen, *unseen]
    prefixes = [prefix for prefix in PREFIXES if prefix]
    suffixes = [suffix for suffix in SUFFIXES if suffix]

    def carrier(key: str) -> tuple[str, int, int]:
        prefix, suffix = str(rng.choice(PREFIXES)), str(rng.choice(SUFFIXES))
        prefix_mark = str(rng.choice(["", "", ","])) if prefix else ""
        key_mark = str(
            rng.choice([",", ",", "", "."])
            if suffix
            else rng.choice(["", ".", "?", "!"])
        )
        parts = (prefix + prefix_mark, key + key_mark, suffix)
        first = len(prefix.split())
        return " ".join(part for part in parts if part), first, first + len(key.split())

    for _ in range(shard.positives):
        texts.append((KIND_POSITIVE, *carrier(str(rng.choice(table.spellings))), -1))
    for _ in range(shard.hard):
        key = str(rng.choice(hard))
        texts.append((KIND_HARD, *carrier(key), table.hard.index(key)))
    for _ in range(shard.generic):
        draw = rng.random()
        if draw < 0.45:
            text = str(rng.choice(SENTENCES))
        elif draw < 0.7:
            # Carrier words with no key word between them.
            text = f"{rng.choice(prefixes)}, {rng.choice(suffixes)}"
        elif draw < 0.85:
            text = (
                f"{rng.choice(prefixes)} {rng.choice(NAMES)}, {rng.choice(SUFFIXES)}"
            ).strip(" ,")
        else:
            text = f"{rng.choice(SENTENCES)}, {rng.choice(SENTENCES)}"
        texts.append((KIND_GENERIC, text, -1, -1, -1))
    return texts


@dataclass(frozen=True)
class Task:
    """What a worker process needs to make one shard."""

    shard: Shard
    phrase: str
    models: str
    work: str


def _load_voice(path: Path) -> Any:
    """A piper voice on one thread, with phoneme alignments."""
    enforce_offline()
    import onnxruntime
    from piper import PiperVoice

    real_options = onnxruntime.SessionOptions

    def one_thread() -> Any:
        # piper builds its session with default options, which is every core.
        options = real_options()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        return options

    onnxruntime.SessionOptions = one_thread
    try:
        return PiperVoice.load(str(path), include_alignments=True)  # type: ignore[call-arg, unused-ignore]
    finally:
        onnxruntime.SessionOptions = real_options


def _key_samples(
    alignments: Sequence[Any] | None, first: int, after: int, text: str, total: int
) -> tuple[int, int] | None:
    """Where the key words start and end, in the voice's samples; None if unsure."""
    if not alignments:
        return None
    start = end = -1
    position = word = 0
    for phoneme in alignments:
        count = int(phoneme.num_samples)
        if phoneme.phoneme == " ":
            word += 1
        elif phoneme.phoneme not in _PUNCTUATION and first <= word < after:
            start = position if start < 0 else start
            end = position + count
        position += count
    words_agree = word == len(text.split()) - 1
    if start < 0 or not words_agree or abs(position - total) > 0.05 * total:
        return None
    return start, end


def synth_shard(task: Task) -> tuple[str, int, int]:
    """Synthesise one shard of clips. Returns (name, clips made, clips skipped)."""
    shard, work = task.shard, Path(task.work)
    out = clip_path(work, shard)
    if out.exists():
        return shard.name, 0, 0
    voice = _load_voice(Path(task.models) / "piper" / f"{shard.voice}.onnx")
    from piper.config import SynthesisConfig
    from scipy.signal import resample_poly

    rng = np.random.default_rng(seed_of(shard.name))
    rate = int(voice.config.sample_rate)
    speakers = int(voice.config.num_speakers)
    divisor = math.gcd(SAMPLE_RATE, rate)
    audio: list[Int16] = []
    lengths: list[int] = []
    meta: list[tuple[int, int, int, int, int]] = []
    skipped = 0
    for kind, text, first, after, hard_id in make_texts(
        rng, shard, phrase_table(task.phrase)
    ):
        speaker = int(rng.integers(speakers)) if speakers > 1 else None
        config = SynthesisConfig(
            speaker_id=speaker,
            length_scale=float(rng.uniform(0.8, 1.35)),
            noise_scale=float(rng.uniform(0.35, 0.9)),
            noise_w_scale=float(rng.uniform(0.5, 1.0)),
        )
        chunks = list(
            voice.synthesize(text, syn_config=config, include_alignments=True)
        )
        if len(chunks) != 1:
            skipped += 1
            continue
        samples = chunks[0].audio_float_array
        key_start = key_end = -1
        if kind != KIND_GENERIC:
            found = _key_samples(
                chunks[0].phoneme_alignments, first, after, text, len(samples)
            )
            if found is None:
                skipped += 1
                continue
            key_start = int(found[0] * SAMPLE_RATE / rate)
            key_end = int(found[1] * SAMPLE_RATE / rate)
        if rate != SAMPLE_RATE:
            samples = resample_poly(samples, SAMPLE_RATE // divisor, rate // divisor)
        clip = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
        audio.append(clip)
        lengths.append(len(clip))
        meta.append(
            (kind, key_start, key_end, hard_id, -1 if speaker is None else speaker)
        )
    save_npz(
        out,
        audio=np.concatenate(audio),
        lens=np.array(lengths),
        meta=np.array(meta, dtype=np.int64),
    )
    return shard.name, len(lengths), skipped


# --- stage 2: augmentation and features --------------------------------------


def build_banks(data: Path, work: Path) -> None:
    """Resample the noise clips and the impulse responses to 16 kHz, once."""
    noise_bank, rir_bank = work / "noise16k.npz", work / "rir16k.npz"
    if noise_bank.exists() and rir_bank.exists():
        return
    import soundfile
    from scipy.signal import resample_poly

    def mono(path: Path) -> Float32:
        samples, rate = soundfile.read(path, dtype="float32", always_2d=True)
        mixed = samples.mean(axis=1)
        if rate != SAMPLE_RATE:
            divisor = math.gcd(SAMPLE_RATE, int(rate))
            mixed = resample_poly(mixed, SAMPLE_RATE // divisor, int(rate) // divisor)
        return np.asarray(mixed, dtype=np.float32)

    if not noise_bank.exists():
        log("feats: building the noise bank (ESC-50 at 16 kHz)")
        clips: list[Int16] = []
        folds: list[int] = []
        for path in sorted((data / NOISE_DIR).glob("*.wav")):
            samples = mono(path)
            samples = np.pad(samples, (0, max(0, 5 * SAMPLE_RATE - len(samples))))
            clips.append(
                (np.clip(samples[: 5 * SAMPLE_RATE], -1, 1) * 32767).astype(np.int16)
            )
            # ESC-50 file names start with the fold number.
            folds.append(int(path.name[0]))
        save_npz(noise_bank, audio=np.stack(clips), fold=np.array(folds))
    if not rir_bank.exists():
        log("feats: building the impulse-response bank")
        responses: list[Float32] = []
        for path in sorted((data / RIR_DIR).glob("*.wav")):
            samples = mono(path)
            # Start at the direct path, so reverb does not move the phrase.
            peak = int(np.argmax(np.abs(samples)))
            samples = samples[max(0, peak - 8) : peak + SAMPLE_RATE // 2]
            energy = float(np.sqrt((samples**2).sum())) + 1e-9
            responses.append((samples / energy).astype(np.float32))
        longest = max(len(response) for response in responses)
        save_npz(
            rir_bank,
            rir=np.stack([np.pad(r, (0, longest - len(r))) for r in responses]),
        )


class Augmenter:
    """Reverb, level, background noise and a noise floor for one window."""

    def __init__(self, work: Path, split: str):
        from scipy.signal import fftconvolve

        self._convolve = fftconvolve
        bank = np.load(work / "noise16k.npz")
        # ESC-50 fold 5 and every tenth impulse response are for evaluation only.
        held = bank["fold"] == 5
        self.noise: Int16 = bank["audio"][held if split == SPLIT_HELDOUT else ~held]
        responses = np.load(work / "rir16k.npz")["rir"]
        tenth = np.arange(len(responses)) % 10 == 0
        self.responses: Float32 = responses[tenth if split == SPLIT_HELDOUT else ~tenth]

    def _noise_crop(self, rng: np.random.Generator, samples: int) -> Float32 | None:
        for _ in range(8):
            clip = self.noise[rng.integers(len(self.noise))]
            clip = np.tile(clip, -(-samples // len(clip)) + 1)
            start = int(rng.integers(0, len(clip) - samples))
            crop: Float32 = clip[start : start + samples].astype(
                np.float32
            ) / np.float32(32768)
            if float(np.sqrt((crop**2).mean())) > 1e-3:
                return crop
        return None

    def __call__(self, rng: np.random.Generator, window: Float32) -> Int16:
        """`window` in [-1, 1], zero where there is no speech -> augmented int16."""
        samples = len(window)
        if rng.random() < 0.5:
            response = self.responses[rng.integers(len(self.responses))]
            window = self._convolve(window, response)[:samples].astype(np.float32)
        active = window[np.abs(window) > 1e-4]
        level = float(np.sqrt((active**2).mean())) if len(active) else 0.0
        target = 10 ** (rng.uniform(-38, -15) / 20)
        if level > 0:
            window = window * (target / level)
        if rng.random() < 0.8:
            crop = self._noise_crop(rng, samples)
            if crop is not None:
                ratio = 10 ** (rng.uniform(0, 25) / 20)
                window = window + crop * (
                    target / ratio / float(np.sqrt((crop**2).mean()))
                )
        floor = 10 ** (rng.uniform(-75, -55) / 20)
        window = window + rng.normal(0, floor, samples).astype(np.float32)
        peak = float(np.abs(window).max())
        if peak > 0.99:
            window = window * (0.99 / peak)
        return (window * 32767).astype(np.int16)


def place(clip: Int16, end: int, samples: int) -> Float32:
    """`samples` samples ending at sample `end` of `clip`; zero outside the clip."""
    window = np.zeros(samples, dtype=np.float32)
    start = end - samples
    low, high = max(0, start), min(len(clip), end)
    if high > low:
        window[low - start : high - start] = clip[low:high].astype(np.float32) / 32768
    return window


def add_background_speech(
    rng: np.random.Generator, window: Float32, other: Int16
) -> Float32:
    """Put another clip behind the window's speech, 3 to 20 dB quieter."""
    samples = len(window)
    end = int(rng.integers(samples // 4, samples + len(other) // 2))
    behind = place(other, min(end, samples + len(other)), samples)
    active = window[np.abs(window) > 1e-4]
    behind_active = behind[np.abs(behind) > 1e-4]
    if not len(active) or not len(behind_active):
        return window
    level = float(np.sqrt((active**2).mean()))
    behind_level = float(np.sqrt((behind_active**2).mean()))
    ratio = 10 ** (rng.uniform(3, 20) / 20)
    mixed: Float32 = window + behind * (level / ratio / behind_level)
    return mixed


_CLIP_FEATURES: Any = None


def _features(clips: Int16) -> npt.NDArray[np.float16]:
    """openWakeWord features of equal-length clips, on one thread per process."""
    global _CLIP_FEATURES
    if _CLIP_FEATURES is None:
        from autobot.wakeword.features import ClipFeatures

        _CLIP_FEATURES = ClipFeatures()
    embedded: Float32 = _CLIP_FEATURES.embed_clips(clips)
    return embedded.astype(np.float16)


def feats_shard(task: Task) -> tuple[str, int, int]:
    """Augment one shard and compute its features. Returns (name, windows, 0).

    Window meta is (kind, hard-negative id, clip index, key start, key end),
    the last two in samples of the window, or -1.
    """
    shard, work = task.shard, Path(task.work)
    out = feat_path(work, shard)
    if out.exists():
        return shard.name, 0, 0
    enforce_offline()
    rng = np.random.default_rng(seed_of("feats" + shard.name))
    augment = Augmenter(work, shard.split)
    train = shard.split == SPLIT_TRAIN
    windows: list[Int16] = []
    meta: list[tuple[int, int, int, int, int]] = []
    if shard.noise:
        samples = SEQUENCE_SAMPLES if train else EVAL_SAMPLES
        for _ in range(shard.noise):
            windows.append(augment(rng, np.zeros(samples, dtype=np.float32)))
            meta.append((KIND_NOISE, -1, -1, -1, -1))
    else:
        stored = np.load(clip_path(work, shard))
        audio: Int16 = stored["audio"]
        clip_meta: Int64 = stored["meta"]
        offsets = np.concatenate([[0], np.cumsum(stored["lens"])])
        generic = np.flatnonzero(clip_meta[:, 0] == KIND_GENERIC)

        def clip_at(index: int) -> Int16:
            return audio[offsets[index] : offsets[index + 1]]

        for index, row in enumerate(clip_meta):
            kind, key_start, key_end, hard_id = (int(v) for v in row[:4])
            clip = clip_at(index)
            if not train:
                # One 4 s window; the key ends 1.8 to 3.4 s before its end.
                last = key_end if kind != KIND_GENERIC else len(clip)
                end = last + EVAL_SAMPLES - int(rng.uniform(1.8, 3.4) * SAMPLE_RATE)
                windows.append(augment(rng, place(clip, end, EVAL_SAMPLES)))
                meta.append((kind, hard_id, index, -1, -1))
                continue
            for _ in range(3 if kind == KIND_POSITIVE else 2):
                if kind == KIND_GENERIC:
                    low = min(SAMPLE_RATE, len(clip))
                    end = int(rng.uniform(low, len(clip) + 0.5 * SAMPLE_RATE))
                    window_start = window_end = -1
                else:
                    # The key word ends 1.3 to 2.9 s into the 3 s window.
                    window_end = int(rng.uniform(1.3, 2.9) * SAMPLE_RATE)
                    end = key_end + SEQUENCE_SAMPLES - window_end
                    window_start = window_end - (key_end - key_start)
                window = place(clip, end, SEQUENCE_SAMPLES)
                if len(generic) and rng.random() < BACKGROUND_SPEECH_CHANCE:
                    other = int(generic[rng.integers(len(generic))])
                    if other != index:
                        window = add_background_speech(rng, window, clip_at(other))
                windows.append(augment(rng, window))
                meta.append((kind, hard_id, index, window_start, window_end))
    features = _features(np.stack(windows))
    save_npz(out, X=features, meta=np.array(meta, dtype=np.int64))
    return shard.name, len(features), 0


def eta_minutes(started: float, done: int, total: int, now: float) -> float:
    """Minutes left, from the average time per finished item."""
    if done <= 0:
        return 0.0
    return (now - started) / done * (total - done) / 60


def run_pool(
    work: Callable[[Task], tuple[str, int, int]],
    tasks: Sequence[Task],
    threads: int,
    stage: str,
) -> None:
    """Run the tasks in worker processes, logging a line as each one finishes."""
    if not tasks:
        return
    import multiprocessing

    started = time.time()
    # spawn: each worker builds its own ONNX sessions.
    with multiprocessing.get_context("spawn").Pool(min(threads, len(tasks))) as pool:
        for done, (name, made, skipped) in enumerate(
            pool.imap_unordered(work, tasks), start=1
        ):
            left = eta_minutes(started, done, len(tasks), time.time())
            detail = f"{made} made" + (f", {skipped} skipped" if skipped else "")
            log(
                f"{stage} shard {done}/{len(tasks)} {name} ({detail}) "
                f"eta {left:.1f} min"
            )


# --- stage 3: training -------------------------------------------------------


def label_windows(meta: Int64) -> npt.NDArray[np.int8]:
    """Label every model input of every training window.

    `meta` is the window meta of `feats_shard`. Returns [windows, 13] of
    1 (the phrase has just ended: positive), 0 (negative) or -1 (ignored: more
    than half of the phrase has been heard, but it is not over, or it ended
    too long ago to call).
    """
    ends = INPUT_END_SAMPLE + FRAME_SAMPLES * np.arange(SEQUENCE_INPUTS)[np.newaxis, :]
    key_start, key_end = meta[:, 3:4], meta[:, 4:5]
    labels = np.zeros((len(meta), SEQUENCE_INPUTS), dtype=np.int8)
    positive = np.broadcast_to(meta[:, 0:1] == KIND_POSITIVE, labels.shape)
    since = (ends - key_end) / SAMPLE_RATE
    labels[positive & (ends > key_start + 0.5 * (key_end - key_start))] = -1
    low, high = POSITIVE_AFTER_SECONDS
    labels[positive & (since >= low) & (since <= high)] = 1
    return labels


def excluded_ids(table: PhraseTable) -> list[int]:
    """The hard-negative ids of `table.train_excluded`: every position of each."""
    return [i for i, text in enumerate(table.hard) if text in table.train_excluded]


def excluded_windows(meta: Int64, table: PhraseTable) -> npt.NDArray[np.bool_]:
    """Which windows hold a hard negative that sounds the same as the phrase.

    `meta` is the window meta of `feats_shard`. Only hard-negative windows
    are ever chosen: the ids of other kinds are not looked at.
    """
    chosen: npt.NDArray[np.bool_] = (meta[:, 0] == KIND_HARD) & np.isin(
        meta[:, 1], excluded_ids(table)
    )
    return chosen


def load_features(
    work: Path,
    shards: Sequence[Shard],
    split: str,
    keep: Callable[[Int64], npt.NDArray[np.bool_]] | None = None,
) -> tuple[npt.NDArray[np.float16], Int64]:
    """The features and window meta of every shard of a split, in plan order.

    `keep` takes a shard's window meta and says which windows to load.
    """
    features: list[npt.NDArray[np.float16]] = []
    meta: list[Int64] = []
    for shard in shards:
        if shard.split == split:
            stored = np.load(feat_path(work, shard))
            shard_meta: Int64 = stored["meta"]
            if keep is None:
                features.append(stored["X"])
                meta.append(shard_meta)
                continue
            chosen = keep(shard_meta)
            if chosen.any():
                features.append(stored["X"][chosen])
                meta.append(shard_meta[chosen])
    if not features:
        # Nothing was chosen: no windows, of no particular length.
        return (
            np.zeros((0, MODEL_EMBEDDINGS, EMBEDDING_SIZE), dtype=np.float16),
            np.zeros((0, 5), dtype=np.int64),
        )
    return np.concatenate(features), np.concatenate(meta)


def load_acav(data: Path) -> npt.NDArray[np.float16]:
    """openWakeWord's general negative features: [rows, 16, 96], memory-mapped.

    The file is the head of the upstream `.npy`, cut at a byte count: a
    128-byte header, then float16 rows.
    """
    path = _require(data / ACAV_FEATURES)
    row = MODEL_EMBEDDINGS * EMBEDDING_SIZE
    mapped = np.memmap(path, dtype=np.float16, mode="r", offset=ACAV_HEADER_BYTES)
    whole: npt.NDArray[np.float16] = mapped[: len(mapped) // row * row].reshape(
        -1, MODEL_EMBEDDINGS, EMBEDDING_SIZE
    )
    return whole


def build_net(hidden: int) -> Any:
    """The classifier: three small layers on the flattened 16 x 96 features."""
    import torch
    from torch import nn

    class Net(nn.Module):  # type: ignore[misc]
        def __init__(self) -> None:
            super().__init__()
            layers: list[Any] = [nn.Flatten()]
            width = MODEL_EMBEDDINGS * EMBEDDING_SIZE
            for _ in range(3):
                layers += [
                    nn.Linear(width, hidden),
                    nn.LayerNorm(hidden),
                    nn.ReLU(),
                    nn.Dropout(0.1),
                ]
                width = hidden
            self.body = nn.Sequential(*layers, nn.Linear(hidden, 1))

        def logits(self, x: Any) -> Any:
            # Raw features have a spread of about 16; the scaling is part of
            # the model, so the engine feeds raw features.
            return self.body(x / 10.0)

        def forward(self, x: Any) -> Any:
            return torch.sigmoid(self.logits(x))

    return Net()


def score_inputs(net: Any, inputs: npt.NDArray[Any], batch: int = 8192) -> Float32:
    """The model's score for each [16, 96] input."""
    import torch

    scores: list[Float32] = []
    net.eval()
    with torch.no_grad():
        for first in range(0, len(inputs), batch):
            part = np.ascontiguousarray(inputs[first : first + batch], dtype=np.float32)
            scores.append(net(torch.from_numpy(part))[:, 0].numpy())
    return np.concatenate(scores) if scores else np.zeros(0, dtype=np.float32)


def stream_inputs(features: npt.NDArray[Any]) -> npt.NDArray[Any]:
    """Every 16-embedding model input of a [T, 96] stream: [T - 15, 16, 96]."""
    view = np.lib.stride_tricks.sliding_window_view(features, MODEL_EMBEDDINGS, axis=0)
    return view.transpose(0, 2, 1)


def clip_scores(net: Any, features: npt.NDArray[Any]) -> Float32:
    """The peak score over each [T, 96] clip: what a streaming detector reaches."""
    count, length, _size = features.shape
    view = np.lib.stride_tricks.sliding_window_view(features, MODEL_EMBEDDINGS, axis=1)
    inputs = view.transpose(0, 1, 3, 2).reshape(-1, MODEL_EMBEDDINGS, EMBEDDING_SIZE)
    scores = score_inputs(net, inputs).reshape(count, length - MODEL_EMBEDDINGS + 1)
    peaks: Float32 = scores.max(axis=1)
    return peaks


def count_events(
    scores: npt.NDArray[Any], threshold: float, refractory: int = REFRACTORY_FRAMES
) -> int:
    """Detections in a score stream; hits within `refractory` frames are one."""
    events, last = 0, -(10**9)
    for hit in np.flatnonzero(scores >= threshold):
        if hit - last > refractory:
            events += 1
        last = int(hit)
    return events


@dataclass(frozen=True)
class Run:
    """One training run's settings."""

    phrase: str
    output: Path
    models: Path
    work: Path
    scale: float
    steps: int
    hidden: int
    threads: int
    # Leave the windows of `PhraseTable.train_excluded` out of the training.
    exclude_near: bool = True
    # Weight of the loss on the hardest 5 % of the hard negatives; 0 drops it.
    hard_topk: float = 1.0
    # Weight of every negative term of the loss.
    negative_weight: float = 1.0
    # The voice dataset's directory; None trains on synthetic clips alone.
    dataset: Path | None = None
    # `RealData.manifest` of the recordings the run trains on; empty when
    # there are none. `train` fills it in once it has read the dataset.
    real_manifest: str = ""

    @property
    def data(self) -> Path:
        return self.models / "wakeword"

    @property
    def checkpoint(self) -> Path:
        return self.work / f"ckpt-{self.hidden}-{self.steps}{self.variant}.pt"

    @property
    def variant(self) -> str:
        """What tells this run's training from the first model's; empty for that one.

        The first model was trained on the near homophones, with both loss
        weights at 1, on synthetic clips alone. A run on real recordings has
        its own checkpoint (`-real`), so it never overwrites the finished
        checkpoint of a synthetic-only run; which recordings it was is in the
        checkpoint's identity (`real_manifest`), not in its name.
        """
        settings = (self.exclude_near, self.hard_topk, self.negative_weight)
        loss = (
            ""
            if settings == (False, 1.0, 1.0)
            else f"-x{int(self.exclude_near)}-k{self.hard_topk:g}"
            f"-n{self.negative_weight:g}"
        )
        return loss + ("-real" if self.real_manifest else "")


# What a checkpoint written before these settings existed was trained with.
CHECKPOINT_DEFAULTS: dict[str, Any] = {
    "excluded": [],
    "hard_topk": 1.0,
    "negative_weight": 1.0,
    "real_manifest": "",
}


def checkpoint_matches(saved: dict[str, Any], wanted: dict[str, Any]) -> bool:
    """Whether a checkpoint was made by the same run: same data, schedule and loss."""
    saved = {**CHECKPOINT_DEFAULTS, **saved}
    return all(saved.get(key) == value for key, value in wanted.items())


def training_inputs(
    meta: Int64, table: PhraseTable, exclude_near: bool
) -> npt.NDArray[np.int8]:
    """`label_windows`, with every input of an excluded window ignored (-1)."""
    labels = label_windows(meta)
    if exclude_near:
        labels[excluded_windows(meta, table)] = -1
    return labels


@dataclass(frozen=True)
class BatchPlan:
    """How many inputs of one training batch come from where."""

    positives: int
    real_positives: int
    negatives: int
    real_negatives: int


def batch_plan(real_positives: int, real_negatives: int) -> BatchPlan:
    """The make-up of a batch, given how many real inputs of each kind exist.

    Half of the positives are real when there is any real positive input, and
    a third of the negatives are real when there is any real negative or
    ambient input. With neither, the batch is the first model's.
    """
    real_positive = REAL_POSITIVE_BATCH if real_positives > 0 else 0
    real_negative = REAL_NEGATIVE_BATCH if real_negatives > 0 else 0
    return BatchPlan(
        POSITIVE_BATCH - real_positive,
        real_positive,
        NEGATIVE_BATCH - real_negative,
        real_negative,
    )


def stage_train(
    run: Run, shards: Sequence[Shard], real: RealFeatures | None = None
) -> Any:
    import torch

    torch.set_num_threads(run.threads)
    torch.manual_seed(0)
    net = build_net(run.hidden)
    optimiser = torch.optim.AdamW(net.parameters(), lr=1e-3, weight_decay=1e-2)
    schedule = torch.optim.lr_scheduler.OneCycleLR(
        optimiser, max_lr=2e-3, total_steps=run.steps, pct_start=0.1
    )
    features, meta = load_features(run.work, shards, SPLIT_TRAIN)
    acav = load_acav(run.data)
    # The last 5 % of the general negatives are kept back for the evaluation.
    acav_rows = int(len(acav) * 0.95)
    table = phrase_table(run.phrase)
    identity = {
        "phrase": run.phrase,
        "steps": run.steps,
        "hidden": run.hidden,
        "sequences": len(features),
        "acav_rows": acav_rows,
        "excluded": sorted(table.train_excluded) if run.exclude_near else [],
        "hard_topk": run.hard_topk,
        "negative_weight": run.negative_weight,
        "real_manifest": run.real_manifest,
    }
    step = 0
    if run.checkpoint.exists():
        saved = torch.load(run.checkpoint, weights_only=False)
        if checkpoint_matches(saved, identity):
            net.load_state_dict(saved["net"])
            optimiser.load_state_dict(saved["opt"])
            schedule.load_state_dict(saved["sched"])
            step = int(saved["step"])
            log(f"train: resuming from the checkpoint at step {step}/{run.steps}")
        else:
            log("train: the checkpoint is from a different run; starting again")
    if step >= run.steps:
        log("train: already complete")
        return net

    labels = training_inputs(meta, table, run.exclude_near)
    if run.exclude_near:
        left_out = int(excluded_windows(meta, table).sum())
        log(
            f"train: left out {left_out} windows of {len(table.train_excluded)} hard "
            "negatives that sound the same as the phrase"
        )
    positive_window, positive_input = np.nonzero(labels == 1)
    negative_window, negative_input = np.nonzero(labels == 0)
    if not len(positive_window):
        raise TrainError("no positive training windows: the synthesis made none")
    offsets = np.arange(MODEL_EMBEDDINGS)[np.newaxis, :]

    def gather(source: npt.NDArray[np.float16], windows: Int64, inputs: Int64) -> Any:
        picked = source[windows[:, np.newaxis], inputs[:, np.newaxis] + offsets]
        return torch.from_numpy(picked.astype(np.float32))

    log(
        f"train: {len(features)} windows -> {len(positive_window)} positive and "
        f"{len(negative_window)} negative inputs, {acav_rows} general negatives "
        f"({acav_rows * 1.28 / 3600:.0f} h); {run.steps} steps on {run.threads} threads"
    )
    # The real recordings: their own inputs, drawn beside the synthetic ones.
    real_features = (
        real.features
        if real is not None
        else np.zeros((0, MODEL_EMBEDDINGS, EMBEDDING_SIZE), dtype=np.float16)
    )
    real_meta = real.meta if real is not None else np.zeros((0, 5), dtype=np.int64)
    real_labels = training_inputs(real_meta, table, run.exclude_near)
    real_positive_window, real_positive_input = np.nonzero(real_labels == 1)
    real_negative_window, real_negative_input = np.nonzero(real_labels == 0)
    plan = batch_plan(len(real_positive_window), len(real_negative_window))
    if len(real_meta):
        log(
            f"train: {len(real_features)} real windows -> {len(real_positive_window)} "
            f"positive and {len(real_negative_window)} negative inputs; each batch "
            f"takes {plan.real_positives} of its {POSITIVE_BATCH} positives and "
            f"{plan.real_negatives} of its {NEGATIVE_BATCH} negatives from them"
        )
    rng = np.random.default_rng(step + 1)
    loss_of = torch.nn.functional.binary_cross_entropy_with_logits
    started, first_step = time.time(), step
    net.train()
    while step < run.steps:
        # With no real inputs the draws below are the first model's, in its order.
        chosen = rng.integers(0, len(positive_window), plan.positives)
        positives = gather(features, positive_window[chosen], positive_input[chosen])
        if plan.real_positives:
            chosen = rng.integers(0, len(real_positive_window), plan.real_positives)
            mixed = gather(
                real_features, real_positive_window[chosen], real_positive_input[chosen]
            )
            positives = torch.cat([positives, mixed])
        chosen = rng.integers(0, len(negative_window), plan.negatives)
        negatives = gather(features, negative_window[chosen], negative_input[chosen])
        if plan.real_negatives:
            chosen = rng.integers(0, len(real_negative_window), plan.real_negatives)
            mixed = gather(
                real_features, real_negative_window[chosen], real_negative_input[chosen]
            )
            negatives = torch.cat([negatives, mixed])
        rows = np.sort(rng.integers(0, acav_rows, GENERAL_BATCH))
        general = torch.from_numpy(acav[rows].astype(np.float32))
        positive_loss = loss_of(net.logits(positives), torch.ones(POSITIVE_BATCH, 1))
        negative_loss = loss_of(
            net.logits(negatives), torch.zeros(NEGATIVE_BATCH, 1), reduction="none"
        )[:, 0]
        general_loss = loss_of(
            net.logits(general), torch.zeros(GENERAL_BATCH, 1), reduction="none"
        )[:, 0]
        # Negatives: the mean, plus the mean of the hardest 5 %, which keeps
        # the pressure on the rare false accept. With both weights at 1 this
        # is the first model's loss, to the bit.
        weight = run.negative_weight
        loss = positive_loss + weight * negative_loss.mean()
        if run.hard_topk > 0:
            loss = loss + (weight * run.hard_topk) * (
                negative_loss.topk(20).values.mean()
            )
        loss = (
            loss
            + (2 * weight) * general_loss.mean()
            + weight * general_loss.topk(77).values.mean()
        )
        optimiser.zero_grad()
        loss.backward()
        optimiser.step()
        schedule.step()
        step += 1
        if step % PROGRESS_EVERY == 0:
            left = eta_minutes(
                started, step - first_step, run.steps - first_step, time.time()
            )
            log(
                f"train step {step}/{run.steps} loss {loss.item():.4f} "
                f"(positive {positive_loss.item():.4f}, negative "
                f"{negative_loss.mean().item():.4f}, general "
                f"{general_loss.mean().item():.5f}) eta {left:.1f} min"
            )
        if step % CHECKPOINT_EVERY == 0 or step == run.steps:
            temporary = run.checkpoint.with_name(run.checkpoint.name + ".tmp")
            torch.save(
                {
                    **identity,
                    "net": net.state_dict(),
                    "opt": optimiser.state_dict(),
                    "sched": schedule.state_dict(),
                    "step": step,
                },
                temporary,
            )
            os.replace(temporary, run.checkpoint)
    return net


# --- the real recordings -----------------------------------------------------

# One word of a local transcript: (text, start, end), the times in seconds.
type Word = tuple[str, float, float]
# Finds where the phrase is said in a clip, as (start, end) seconds, or None:
# faster-whisper's word timestamps (`whisper_locator`), or a test fake.
type Locator = Callable[[Int16, ClipRecord], tuple[float, float] | None]


@dataclass(frozen=True)
class RealClip:
    """One clip of the voice dataset, with its side of the split."""

    record: ClipRecord
    # `SPLIT_TRAIN` or `SPLIT_HELDOUT` for a positive or a negative;
    # `SPLIT_AMBIENT` for an ambient recording, which is on both sides.
    split: str
    # The clip's length in samples.
    samples: int
    # For an ambient recording: its training and its held-out samples, as
    # (first, one past the last). They never overlap.
    train: tuple[int, int] = (0, 0)
    heldout: tuple[int, int] = (0, 0)


@dataclass(frozen=True)
class RealData:
    """The voice dataset's clips for one phrase, split as `dataset` splits them."""

    phrase: str
    # In a fixed order: by file name.
    clips: tuple[RealClip, ...]
    # A sha1 over every clip's manifest line and length; empty with no clips.
    # It names the cached features and is part of a checkpoint's identity.
    manifest: str

    def files(self, side: str, label: str | None = None) -> list[str]:
        """The file names on one side of the split, of one label or of all."""
        return [
            clip.record.file
            for clip in self.clips
            if clip.split == side and label in (None, clip.record.label)
        ]

    @property
    def ambient(self) -> list[RealClip]:
        return [clip for clip in self.clips if clip.split == SPLIT_AMBIENT]

    @property
    def heldout_ambient_minutes(self) -> float:
        held = sum(clip.heldout[1] - clip.heldout[0] for clip in self.ambient)
        return held / SAMPLE_RATE / 60

    @property
    def trained_ambient_minutes(self) -> float:
        trained = sum(clip.train[1] - clip.train[0] for clip in self.ambient)
        return trained / SAMPLE_RATE / 60

    def excluded(self, table: PhraseTable, exclude_near: bool) -> list[str]:
        """The training-side negatives a run with `exclude_near` leaves out.

        They are recordings of `PhraseTable.train_excluded`: the owner saying
        a phrase that sounds the same as the wake phrase. Like the synthetic
        clips of those phrases, they are not trained on as negatives.
        """
        if not exclude_near:
            return []
        return [
            clip.record.file
            for clip in self.clips
            if clip.split == SPLIT_TRAIN
            and clip.record.label == LABEL_NEGATIVE
            and spoken_text(clip.record.prompt) in table.train_excluded
        ]

    def describe(self, table: PhraseTable, exclude_near: bool) -> list[str]:
        """The log lines that say what the run has of real recordings."""
        if not self.clips:
            return [
                f"real: no real recordings for {self.phrase!r}: synthetic clips only"
            ]
        excluded = self.excluded(table, exclude_near)
        trained = len(self.files(SPLIT_TRAIN)) - len(excluded)
        positives = len(self.files(SPLIT_TRAIN, LABEL_POSITIVE))
        negatives = len(self.files(SPLIT_TRAIN, LABEL_NEGATIVE)) - len(excluded)
        summary = (
            f"real: {trained} training clips, {len(self.files(SPLIT_HELDOUT))} held "
            f"out, {self.heldout_ambient_minutes:.1f} min held-out ambient"
        )
        detail = (
            f"real: trained on {positives} positives, {negatives} negatives and "
            f"{self.trained_ambient_minutes:.1f} min of ambient; held out "
            f"{len(self.files(SPLIT_HELDOUT, LABEL_POSITIVE))} positives and "
            f"{len(self.files(SPLIT_HELDOUT, LABEL_NEGATIVE))} negatives"
        )
        lines = [summary, detail]
        if excluded:
            lines.append(
                f"real: left out {len(excluded)} negatives that sound the same as "
                "the phrase"
            )
        return lines


def spoken_text(text: str) -> str:
    """A prompt as its words: lower case, no punctuation, single spaces."""
    kept = "".join(c if c.isalnum() or c == "'" else " " for c in text.lower())
    return " ".join(kept.split())


def _letters(text: str) -> str:
    return "".join(c for c in text.lower() if c.isalnum())


def phrase_last(record: ClipRecord) -> bool:
    """Whether a clip's script prompt ends with the phrase.

    Read from the manifest alone: the prompt's text, without spaces or
    punctuation, ends with the phrase's. A clip with no script prompt (a
    live-monitor mark, an Audio-screen clip) is not known to end with it.
    """
    return bool(record.prompt_id) and _letters(record.prompt).endswith(
        _letters(record.phrase)
    )


def _wav_samples(path: Path) -> int:
    """A dataset clip's length in samples; `TrainError` unless 16 kHz mono 16-bit."""
    try:
        with wave.open(str(path), "rb") as wav:
            shape = (wav.getframerate(), wav.getnchannels(), wav.getsampwidth())
            frames = wav.getnframes()
    except (OSError, EOFError, wave.Error):
        raise TrainError(f"cannot read {path} as a WAV file") from None
    if shape != (SAMPLE_RATE, 1, 2):
        raise TrainError(f"{path} is not a {SAMPLE_RATE} Hz mono 16-bit WAV file")
    return frames


def read_samples(path: Path, first: int, count: int) -> Int16:
    """`count` samples of a WAV file from sample `first`: a part, not the whole."""
    try:
        with wave.open(str(path), "rb") as wav:
            wav.setpos(max(0, min(first, wav.getnframes())))
            frames = wav.readframes(max(0, count))
    except (OSError, EOFError, wave.Error):
        raise TrainError(f"cannot read {path} as a WAV file") from None
    return np.frombuffer(frames, dtype="<i2").astype(np.int16)


def load_real(dataset: VoiceDataset, phrase: str) -> RealData:
    """The manifest's clips for `phrase`, in a fixed order, each with its split.

    The split is `autobot.wakeword.dataset`'s and nothing else: `split` for a
    positive or a negative, `ambient_slices` for an ambient recording, whose
    training and held-out sample ranges meet at one sample and never overlap.
    """
    clips: list[RealClip] = []
    identity: list[object] = [REAL_FEATURES_VERSION]
    for record in sorted(dataset.for_phrase(phrase), key=lambda record: record.file):
        samples = _wav_samples(dataset.path(record))
        if record.label == LABEL_AMBIENT:
            (_, cut_seconds), _held = ambient_slices(record, samples / SAMPLE_RATE)
            cut = min(samples, round(cut_seconds * SAMPLE_RATE))
            clips.append(
                RealClip(record, SPLIT_AMBIENT, samples, (0, cut), (cut, samples))
            )
        else:
            side = SPLIT_HELDOUT if split(record) == HELDOUT else SPLIT_TRAIN
            clips.append(RealClip(record, side, samples))
        identity.append([asdict(record), samples])
    if not clips:
        return RealData(phrase, (), "")
    digest = hashlib.sha1(
        json.dumps(identity, sort_keys=True).encode(), usedforsecurity=False
    )
    return RealData(phrase, tuple(clips), digest.hexdigest())


def real_section(
    real: RealData, table: PhraseTable, exclude_near: bool
) -> dict[str, Any]:
    """The `real` part of the metrics file: what was trained on and what held out."""
    excluded = real.excluded(table, exclude_near)
    trained = [file for file in real.files(SPLIT_TRAIN) if file not in excluded]

    def seconds(clip: RealClip, part: tuple[int, int]) -> dict[str, Any]:
        return {
            "file": clip.record.file,
            "start": part[0] / SAMPLE_RATE,
            "end": part[1] / SAMPLE_RATE,
        }

    section: dict[str, Any] = {
        "trained": trained,
        "heldout": real.files(SPLIT_HELDOUT),
        # Training-side recordings of phrases that sound the same as the wake
        # phrase: neither trained on nor held out (`--exclude-near-homophones`).
        "excluded": excluded,
        "ambient_trained": [seconds(clip, clip.train) for clip in real.ambient],
        "ambient_heldout": [seconds(clip, clip.heldout) for clip in real.ambient],
        "counts": {
            "trained_positives": len(real.files(SPLIT_TRAIN, LABEL_POSITIVE)),
            "trained_negatives": len(real.files(SPLIT_TRAIN, LABEL_NEGATIVE))
            - len(excluded),
            "excluded_negatives": len(excluded),
            "heldout_positives": len(real.files(SPLIT_HELDOUT, LABEL_POSITIVE)),
            "heldout_negatives": len(real.files(SPLIT_HELDOUT, LABEL_NEGATIVE)),
            "ambient_clips": len(real.ambient),
            "ambient_trained_minutes": real.trained_ambient_minutes,
            "ambient_heldout_minutes": real.heldout_ambient_minutes,
        },
        "manifest": real.manifest,
    }
    if not real.clips:
        section["note"] = "no real recordings: synthetic clips only"
    return section


def voiced_span(samples: Int16) -> tuple[int, int]:
    """Where the speech of a clip starts and ends, in samples.

    Energy in 20 ms frames against the clip's own noise floor (its quietest
    tenth). Bursts closer than 200 ms are one stretch, so the pause before a
    final consonant does not end it, and a stretch with under 60 ms above the
    floor (a click) is not speech. A clip with nothing above its floor is
    taken whole.
    """
    frame = SAMPLE_RATE // 50
    count = len(samples) // frame
    if count < 3:
        return 0, len(samples)
    framed = samples[: count * frame].astype(np.float64).reshape(count, frame)
    level = 20 * np.log10(np.sqrt((framed**2).mean(axis=1)) + 1.0)
    floor = float(np.percentile(level, 10))
    peak = float(level.max())
    if peak - floor < 8.0:
        return 0, len(samples)
    loud = np.flatnonzero(level > floor + max(8.0, 0.3 * (peak - floor)))
    stretches = np.split(loud, np.flatnonzero(np.diff(loud) > 10) + 1)
    speech = [stretch for stretch in stretches if len(stretch) >= 3]
    if not speech:
        speech = [max(stretches, key=len)]
    return int(speech[0][0]) * frame, (int(speech[-1][-1]) + 1) * frame


def find_phrase(
    words: Sequence[Word], phrase: str, prompt: str = "", *, spelled_only: bool = False
) -> tuple[float, float] | None:
    """Where `phrase` is said among transcribed `words`: (start, end) seconds.

    The transcript is used to find the phrase, never to label the clip: the
    label is the prompt's. In order: one to three words in a row that spell
    the phrase; the run of words that spells something closest to it, when
    it is close; and, when the phrase itself was misheard, the word the
    prompt has after it, which the phrase ends just before (skipped with
    `spelled_only`). None when none applies.
    """
    wanted = _letters(phrase)
    spoken = [(_letters(text), start, end) for text, start, end in words]
    spoken = [word for word in spoken if word[0]]
    if not wanted or not spoken:
        return None
    best: tuple[float, float, float] | None = None
    for first in range(len(spoken)):
        for last in range(first, min(first + 3, len(spoken))):
            said = "".join(word[0] for word in spoken[first : last + 1])
            span = (spoken[first][1], spoken[last][2])
            if said == wanted:
                return span
            likeness = difflib.SequenceMatcher(None, said, wanted).ratio()
            if best is None or likeness > best[0]:
                best = (likeness, *span)
    if best is not None and best[0] >= 0.6:
        return best[1], best[2]
    if spelled_only:
        return None
    prompted = [_letters(word) for word in spoken_text(prompt).split()]
    size = len(spoken_text(phrase).split())
    for first in range(len(prompted) - size):
        if "".join(prompted[first : first + size]) != wanted:
            continue
        following = prompted[first + size]
        for text, start, _end in spoken[1:]:
            if text == following:
                return max(0.0, start - REAL_PHRASE_SECONDS), start
    return None


def whisper_locator(models: Path, phrase: str, threads: int) -> Locator:
    """faster-whisper `tiny.en` word timestamps, from the local model directory.

    Local like every other speech model (CONSTRAINTS.md CN-1): the directory
    under `models/` is loaded with `local_files_only`, and a missing one is a
    `TrainError`, never a download. A clip is transcribed plainly first and,
    when the phrase is not spelled in that, once more with the phrase given
    as the text before it, which makes the model more likely to spell it;
    only then is the word after the phrase used to place it.
    """
    directory = models / FASTER_WHISPER_MODEL_DIR.relative_to(MODELS_DIR)
    if not (directory / "model.bin").is_file():
        raise TrainError(f"faster-whisper model is missing: {directory}")
    enforce_offline()
    from faster_whisper import WhisperModel

    model: Any = WhisperModel(
        str(directory),
        device="cpu",
        compute_type="int8",
        cpu_threads=threads,
        local_files_only=True,
    )

    def words(samples: Int16, before: str | None) -> list[Word]:
        segments, _info = model.transcribe(
            samples.astype(np.float32) / 32768,
            language="en",
            beam_size=5,
            temperature=0.0,
            condition_on_previous_text=False,
            word_timestamps=True,
            vad_filter=False,
            initial_prompt=before,
        )
        return [
            (str(word.word), float(word.start), float(word.end))
            for segment in segments
            for word in (segment.words or [])
        ]

    def locate(samples: Int16, record: ClipRecord) -> tuple[float, float] | None:
        plain = words(samples, None)
        for heard in (plain, words(samples, f"{phrase.capitalize()}.")):
            found = find_phrase(heard, record.phrase, spelled_only=True)
            if found is not None:
                return found
        return find_phrase(plain, record.phrase, record.prompt)

    return locate


def key_span(
    samples: Int16, record: ClipRecord, locate: Locator | None
) -> tuple[int, int, str]:
    """Where the phrase is in a real positive: (start, end) samples, and how found.

    A prompt that ends with the phrase: the phrase ends where the speech does
    (`"span"`). Any other positive: the local transcript's word times, kept
    inside the speech (`"words"`), and the end of the speech when the phrase
    is not found in them or there is no transcriber (`"fallback"`). The
    start is only ever moved earlier, never later, than what was found: the
    model inputs that end inside the phrase are ignored, so a phrase taken
    too long costs nothing and one taken too short trains its first half as
    a negative.
    """
    voiced_start, voiced_end = voiced_span(samples)
    longest = int(REAL_PHRASE_SECONDS * SAMPLE_RATE)
    by_span = (max(voiced_start, voiced_end - longest), voiced_end)
    if phrase_last(record):
        return (*by_span, "span")
    if locate is not None:
        found = locate(samples, record)
        if found is not None:
            end = min(voiced_end, round(found[1] * SAMPLE_RATE))
            start = min(round(found[0] * SAMPLE_RATE), end - longest // 2)
            start = max(voiced_start, start)
            if end > start:
                return start, end, "words"
    return (*by_span, "fallback")


@dataclass(frozen=True)
class RealWindow:
    """One 3 s training window cut from a real clip."""

    # The clip's position in `RealData.clips`.
    clip: int
    kind: int
    # The position in `PhraseTable.hard` of what a negative says, or -1.
    hard_id: int
    # The window is samples [end - SEQUENCE_SAMPLES, end) of the clip; outside
    # the clip it is quiet.
    end: int
    # Where the phrase starts and ends in the window, in samples, or -1.
    key_start: int = -1
    key_end: int = -1
    # False for the window as recorded, True for one through the `Augmenter`.
    augment: bool = False

    @property
    def start(self) -> int:
        return self.end - SEQUENCE_SAMPLES


def plan_real_windows(
    real: RealData,
    table: PhraseTable,
    spans: Mapping[str, tuple[int, int]],
    rng: np.random.Generator,
) -> list[RealWindow]:
    """Every training window of the real recordings. Deterministic for an `rng`.

    `spans` gives, by file name, where the phrase is in each training
    positive. Held-out clips get no window, and an ambient recording's
    windows tile its training samples only: none reaches its held-out last
    fifth.
    """
    windows: list[RealWindow] = []
    hard_ids = {text: index for index, text in reversed(list(enumerate(table.hard)))}
    for index, clip in enumerate(real.clips):
        label = clip.record.label
        if clip.split == SPLIT_AMBIENT:
            first, last = clip.train
            for start in range(first, last - SEQUENCE_SAMPLES + 1, SEQUENCE_SAMPLES):
                for variant in range(REAL_VARIANTS):
                    windows.append(
                        RealWindow(
                            index,
                            KIND_NOISE,
                            -1,
                            start + SEQUENCE_SAMPLES,
                            augment=variant > 0,
                        )
                    )
        elif clip.split != SPLIT_TRAIN:
            continue
        elif label == LABEL_POSITIVE:
            key_start, key_end = spans[clip.record.file]
            for variant in range(REAL_VARIANTS):
                # The phrase ends 1.3 to 2.9 s into the 3 s window.
                window_end = int(rng.uniform(1.3, 2.9) * SAMPLE_RATE)
                windows.append(
                    RealWindow(
                        index,
                        KIND_POSITIVE,
                        -1,
                        key_end + SEQUENCE_SAMPLES - window_end,
                        window_end - (key_end - key_start),
                        window_end,
                        augment=variant > 0,
                    )
                )
        else:
            hard_id = hard_ids.get(spoken_text(clip.record.prompt), -1)
            kind = KIND_HARD if hard_id >= 0 else KIND_GENERIC
            for variant in range(REAL_VARIANTS):
                low = min(SAMPLE_RATE, clip.samples)
                end = int(rng.uniform(low, clip.samples + 0.5 * SAMPLE_RATE))
                windows.append(
                    RealWindow(index, kind, hard_id, end, augment=variant > 0)
                )
    return windows


def as_recorded(rng: np.random.Generator, clip: Int16, end: int) -> Int16:
    """The window ending at sample `end` of `clip`, unaugmented.

    Where the window reaches outside the clip it holds the near-silence the
    engine primes itself with, not digital zero: that is what the listener
    hears before a stretch of speech.
    """
    start = end - SEQUENCE_SAMPLES
    window = rng.normal(0.0, PRIME_NOISE_LEVEL, SEQUENCE_SAMPLES)
    low, high = max(0, start), min(len(clip), end)
    if high > low:
        window[low - start : high - start] = clip[low:high]
    return np.clip(window, -32768, 32767).astype(np.int16)


@dataclass(frozen=True)
class RealFeatures:
    """The featurised real training windows, as `feats_shard` stores a shard's."""

    # [windows, 28, 96]
    features: npt.NDArray[np.float16]
    # [windows, 5]: (kind, hard-negative id, clip index, key start, key end).
    meta: Int64
    # [windows, 3]: (clip index, first sample, one past the last sample) of
    # the window in its clip.
    source: Int64
    # The clips' file names, by clip index.
    files: tuple[str, ...]


def no_real_features() -> RealFeatures:
    return RealFeatures(
        np.zeros(
            (0, SEQUENCE_INPUTS + MODEL_EMBEDDINGS - 1, EMBEDDING_SIZE), np.float16
        ),
        np.zeros((0, 5), dtype=np.int64),
        np.zeros((0, 3), dtype=np.int64),
        (),
    )


def real_feats_path(run: Run, real: RealData) -> Path:
    return run.work / "real" / f"{real.manifest}.npz"


def real_feats(
    run: Run, real: RealData, locator: Callable[[], Locator] | None = None
) -> RealFeatures:
    """Cut, augment and featurise the training side of the real recordings.

    Cached as `work/real/<manifest-sha1>.npz`; the caches of earlier
    manifests are removed, since the dataset only grows. Speech clips are
    read whole; an ambient recording is read 3 s at a time and the windows
    are featurised `REAL_FEATURE_BATCH` at a time, so an hour of room audio
    needs no more memory than a minute. `locator` builds the transcriber for
    the positives whose prompt does not end with the phrase; the default is
    `whisper_locator`, and it is built only when such a clip exists.
    """
    if not real.clips or run.dataset is None:
        return no_real_features()
    out = real_feats_path(run, real)
    files = tuple(clip.record.file for clip in real.clips)
    if out.exists():
        stored = np.load(out)
        log(f"real: {len(stored['X'])} windows cached in {out.name}")
        return RealFeatures(stored["X"], stored["meta"], stored["source"], files)
    dataset = VoiceDataset(run.dataset)
    table = phrase_table(run.phrase)
    rng = np.random.default_rng(seed_of("real" + real.manifest))

    spans: dict[str, tuple[int, int]] = {}
    found = {"span": 0, "words": 0, "fallback": 0}
    locate: Locator | None = None
    tried = False
    for clip in real.clips:
        if clip.split != SPLIT_TRAIN or clip.record.label != LABEL_POSITIVE:
            continue
        if not phrase_last(clip.record) and not tried:
            tried = True
            try:
                locate = (
                    locator()
                    if locator is not None
                    else whisper_locator(run.models, run.phrase, run.threads)
                )
            except (TrainError, ModuleNotFoundError) as error:
                log(
                    f"real: no local transcriber ({error}); the phrase is taken to "
                    "end where the speech ends"
                )
        samples = dataset.load(clip.record).samples
        start, end, how = key_span(samples, clip.record, locate)
        spans[clip.record.file] = (start, end)
        found[how] += 1
        if how == "fallback":
            log(
                f"real: {clip.record.file}: the phrase was not found in the "
                "transcript; taken to end where the speech ends"
            )
    windows = plan_real_windows(real, table, spans, rng)
    build_banks(run.data, run.work)
    augment = Augmenter(run.work, SPLIT_TRAIN)
    features: list[npt.NDArray[np.float16]] = []
    batch: list[Int16] = []
    loaded: tuple[int, Int16] | None = None
    started = time.time()
    for number, window in enumerate(windows, start=1):
        clip = real.clips[window.clip]
        path = dataset.path(clip.record)
        if clip.split == SPLIT_AMBIENT:
            # Only the window's own 3 s are read: `end` is its end in the piece.
            audio = read_samples(path, window.start, SEQUENCE_SAMPLES)
            end = SEQUENCE_SAMPLES
        else:
            if loaded is None or loaded[0] != window.clip:
                loaded = (window.clip, dataset.load(clip.record).samples)
            audio, end = loaded[1], window.end
        if window.augment:
            batch.append(augment(rng, place(audio, end, SEQUENCE_SAMPLES)))
        else:
            batch.append(as_recorded(rng, audio, end))
        if len(batch) == REAL_FEATURE_BATCH or number == len(windows):
            features.append(_features(np.stack(batch)))
            batch = []
            log(
                f"real: featurised {number}/{len(windows)} windows, eta "
                f"{eta_minutes(started, number, len(windows), time.time()):.1f} min"
            )
    made = no_real_features()
    result = RealFeatures(
        np.concatenate(features) if features else made.features,
        np.array(
            [(w.kind, w.hard_id, w.clip, w.key_start, w.key_end) for w in windows],
            dtype=np.int64,
        ).reshape(-1, 5),
        np.array([(w.clip, w.start, w.end) for w in windows], dtype=np.int64).reshape(
            -1, 3
        ),
        files,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    for stale in out.parent.glob("*.npz"):
        stale.unlink()
    save_npz(
        out,
        X=result.features,
        meta=result.meta,
        source=result.source,
        files=np.array(files),
    )
    log(
        f"real: {len(windows)} windows from the training recordings; the phrase "
        f"ends with the speech in {found['span']} positives, was located by word "
        f"timestamps in {found['words']} and not found in {found['fallback']}"
    )
    return result


def stage_real(run: Run, real: RealData) -> RealFeatures:
    """The `real` stage: the features of the real training windows, cached."""
    return real_feats(run, real)


# --- evaluation and export ---------------------------------------------------


def recommend(rows: Sequence[dict[str, float]]) -> dict[str, float]:
    """The sweep row to run at: the best recall within the false-accept target.

    Thresholds under 0.3 are not offered. When no row meets the target the
    model is not good enough yet, and the strictest threshold that still
    hears the phrase `MIN_USEFUL_RECALL` of the time is returned (the
    strictest of all when none does).
    """
    good = [
        row
        for row in rows
        if row["val_fa_per_hour"] <= TARGET_FALSE_ACCEPTS_PER_HOUR
        and row["threshold"] >= 0.3
    ]
    if good:
        return max(good, key=lambda row: row["recall"])
    usable = [row for row in rows if row["recall"] >= MIN_USEFUL_RECALL]
    return max(usable or rows, key=lambda row: row["threshold"])


def stage_eval(
    run: Run,
    shards: Sequence[Shard],
    net: Any,
    model: Path | None = None,
    real: RealData | None = None,
) -> dict[str, Any]:
    """The metrics file's contents: the synthetic figures, then the real ones.

    `model` is the exported ONNX file, which is what the real recordings are
    scored with; without it, or without `real`, only the synthetic figures
    are made.
    """
    table = phrase_table(run.phrase)
    features, meta = load_features(run.work, shards, SPLIT_HELDOUT)
    peaks = clip_scores(net, features)
    kind, hard_id = meta[:, 0], meta[:, 1]
    unseen_ids = [i for i, text in enumerate(table.hard) if text in table.eval_only]
    unseen = np.isin(hard_id, unseen_ids)
    # Reported on their own whether or not this run trained on them, so runs
    # that did and runs that did not are compared on the same clips.
    near = excluded_windows(meta, table)
    groups = {
        "pos": kind == KIND_POSITIVE,
        "hard_seen": (kind == KIND_HARD) & ~unseen & ~near,
        "hard_unseen": (kind == KIND_HARD) & unseen & ~near,
        "hard_near": near,
        "generic": kind == KIND_GENERIC,
        "noise": kind == KIND_NOISE,
    }
    validation = np.load(_require(run.data / VALIDATION_FEATURES), mmap_mode="r")
    validation_scores = score_inputs(net, stream_inputs(validation))
    validation_hours = len(validation_scores) * 0.08 / 3600
    acav = load_acav(run.data)
    held_back = acav[int(len(acav) * 0.95) :]
    acav_scores = score_inputs(net, held_back)
    acav_hours = len(held_back) * 1.28 / 3600
    log(
        f"eval: {int(groups['pos'].sum())} held-out positives, "
        f"{int(groups['hard_seen'].sum())} + {int(groups['hard_unseen'].sum())} hard "
        f"negatives (trained on + never trained on), {int(groups['generic'].sum())} "
        f"generic, {int(groups['noise'].sum())} noise; validation stream "
        f"{validation_hours:.1f} h, held-back general negatives {acav_hours:.1f} h"
    )
    rows: list[dict[str, float]] = []
    log(
        "eval:  thr  recall  fa_hard_seen  fa_hard_unseen  fa_generic  fa_noise  "
        "val_fa/h  acav_fa/h"
    )
    for threshold in THRESHOLDS:
        row = {
            "threshold": threshold,
            "recall": _rate(peaks, groups["pos"], threshold),
            "fa_hard_seen": _rate(peaks, groups["hard_seen"], threshold),
            "fa_hard_unseen": _rate(peaks, groups["hard_unseen"], threshold),
            "fa_hard_near": _rate(peaks, groups["hard_near"], threshold),
            "fa_generic": _rate(peaks, groups["generic"], threshold),
            "fa_noise": _rate(peaks, groups["noise"], threshold),
            "val_fa_per_hour": count_events(validation_scores, threshold)
            / validation_hours,
            "acav_fa_per_hour": float((acav_scores >= threshold).sum()) / acav_hours,
        }
        rows.append(row)
        log(
            f"eval: {threshold:5.2f}  {row['recall']:.4f}  {row['fa_hard_seen']:12.4f}  "
            f"{row['fa_hard_unseen']:14.4f}  {row['fa_generic']:10.4f}  "
            f"{row['fa_noise']:8.4f}  {row['val_fa_per_hour']:8.2f}  "
            f"{row['acav_fa_per_hour']:9.2f}"
        )
    best = recommend(rows)
    near_rows = near_homophones(run, shards, net, table, peaks, meta)
    worst: list[tuple[float, int, str]] = []
    for identifier in np.unique(hard_id[(kind == KIND_HARD) & ~near]):
        chosen = (kind == KIND_HARD) & (hard_id == identifier)
        worst.append(
            (
                _rate(peaks, chosen, best["threshold"]),
                int(chosen.sum()),
                table.hard[int(identifier)],
            )
        )
    worst.sort(reverse=True)
    listed = ", ".join(f"{text!r} {rate:.2f}" for rate, _count, text in worst[:10])
    log(f"eval: hard negatives it fires on most at {best['threshold']}: {listed}")
    meets = best["val_fa_per_hour"] <= TARGET_FALSE_ACCEPTS_PER_HOUR
    log(
        f"eval: recommended threshold {best['threshold']}: recall "
        f"{best['recall']:.3f}, {best['val_fa_per_hour']:.2f} false accepts per hour "
        "on the validation stream"
        + (
            ""
            if meets
            else f" (above the target of {TARGET_FALSE_ACCEPTS_PER_HOUR}: "
            "train with more data)"
        )
    )
    real = real if real is not None else RealData(run.phrase, (), "")
    real_metrics = real_section(real, table, run.exclude_near)
    recommended, recommended_on = best["threshold"], "synthetic"
    if model is not None and real.clips and run.dataset is not None:
        # The exported file, with the runtime's libraries: what will run.
        from autobot.wakeword import evaluate

        scores = evaluate.score_real(model, VoiceDataset(run.dataset), run.phrase)
        for line in evaluate.format_sweep(scores):
            log(f"eval: real: {line}")
        real_metrics.update(scores.to_json())
        if scores.recommended is not None and scores.heldout_positives > 0:
            recommended, recommended_on = scores.recommended["threshold"], "real"
        else:
            log(
                "eval: real: no held-out real positive to recommend a threshold on; "
                "the synthetic recommendation stands"
            )
    at_recommended = next(row for row in rows if row["threshold"] == recommended)
    return {
        "phrase": run.phrase,
        "input": "[N,16,96] float32 openWakeWord embeddings",
        "output": "[N,1] score 0..1",
        "recommended_threshold": recommended,
        # Which figures the threshold was chosen on: "real" or "synthetic".
        "recommended_on": recommended_on,
        "synthetic_recommended_threshold": best["threshold"],
        "meets_false_accept_target": meets,
        # The synthetic figures at the recommended threshold.
        "metrics_at_threshold": at_recommended,
        "real": real_metrics,
        "sweep": rows,
        "worst_hard_negatives": [
            {"text": text, "false_accept_rate": rate, "clips": count}
            for rate, count, text in worst[:20]
        ],
        "near_homophones": {
            "trained_on": not run.exclude_near,
            "note": "hard negatives that sound the same as the phrase, not counted "
            "in fa_hard_seen; 'heldout' is clips in voices never trained on, "
            "'train_voices' is 3 s training windows in the training voices",
            "phrases": near_rows,
        },
        "scale": run.scale,
        "steps": run.steps,
        "hidden": run.hidden,
        "exclude_near_homophones": run.exclude_near,
        "hard_topk_weight": run.hard_topk,
        "negative_weight": run.negative_weight,
        "arch": "mlp",
        "trained": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


# A training window counts towards `near_homophones` when its key words end
# at least half a second before the last model input does, so the inputs that
# would fire on it are all in the window.
NEAR_LATEST_KEY_END = (
    INPUT_END_SAMPLE + FRAME_SAMPLES * (SEQUENCE_INPUTS - 1) - SAMPLE_RATE // 2
)


def near_homophones(
    run: Run,
    shards: Sequence[Shard],
    net: Any,
    table: PhraseTable,
    heldout_peaks: Float32,
    heldout_meta: Int64,
) -> list[dict[str, Any]]:
    """How often the model fires on each phrase of `table.train_excluded`.

    The held-out clips have few of these phrases, or none, so each is also
    scored on its training windows. Those are in the training voices: for a
    run with `exclude_near` the model never saw the phrase but knows the
    voice; for a run without, it was trained on these very windows.
    """
    if not table.train_excluded:
        return []

    def usable(meta: Int64) -> npt.NDArray[np.bool_]:
        early: npt.NDArray[np.bool_] = meta[:, 4] <= NEAR_LATEST_KEY_END
        return excluded_windows(meta, table) & early

    features, meta = load_features(run.work, shards, SPLIT_TRAIN, usable)
    peaks = (
        clip_scores(net, features) if len(features) else np.zeros(0, dtype=np.float32)
    )
    rows: list[dict[str, Any]] = []
    for text in sorted(table.train_excluded):
        identifier = table.hard.index(text)
        held = (heldout_meta[:, 0] == KIND_HARD) & (heldout_meta[:, 1] == identifier)
        seen = meta[:, 1] == identifier
        rows.append(
            {
                "text": text,
                "heldout_clips": int(held.sum()),
                "train_voice_windows": int(seen.sum()),
                "heldout": {
                    str(threshold): _rate(heldout_peaks, held, threshold)
                    for threshold in THRESHOLDS
                },
                "train_voices": {
                    str(threshold): _rate(peaks, seen, threshold)
                    for threshold in THRESHOLDS
                },
            }
        )
    trained = "trained on" if not run.exclude_near else "left out of the training"
    shown = "/".join(str(threshold) for threshold in NEAR_LOG_THRESHOLDS)
    log(
        f"eval: phrases that sound the same as {run.phrase!r} ({trained}), fired on "
        f"at {shown}: training voices, then held-out voices"
    )
    for row in rows:
        seen_rates = " ".join(
            f"{row['train_voices'][str(t)]:.3f}" for t in NEAR_LOG_THRESHOLDS
        )
        held_rates = " ".join(
            f"{row['heldout'][str(t)]:.3f}" for t in NEAR_LOG_THRESHOLDS
        )
        log(
            f"eval:   {row['text']!r:18s} {seen_rates} "
            f"({row['train_voice_windows']} windows)  {held_rates} "
            f"({row['heldout_clips']} clips)"
        )
    return rows


def _rate(peaks: Float32, chosen: npt.NDArray[np.bool_], threshold: float) -> float:
    """The share of the chosen clips whose peak reaches the threshold."""
    return float((peaks[chosen] >= threshold).mean()) if chosen.any() else 0.0


def export_onnx(net: Any, path: Path) -> float:
    """Write the model as ONNX; return its largest difference from the network."""
    import torch

    net.eval()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        # The legacy exporter: the new one needs more packages than a
        # three-layer network is worth.
        torch.onnx.export(
            net,
            torch.zeros(1, MODEL_EMBEDDINGS, EMBEDDING_SIZE),
            str(path),
            input_names=["x"],
            output_names=["score"],
            dynamic_axes={"x": {0: "n"}, "score": {0: "n"}},
            opset_version=13,
            dynamo=False,
        )
    enforce_offline()
    import onnxruntime

    session = onnxruntime.InferenceSession(
        str(path), providers=["CPUExecutionProvider"]
    )
    sample = (
        np.random.default_rng(0)
        .normal(2, 16, (4, MODEL_EMBEDDINGS, EMBEDDING_SIZE))
        .astype(np.float32)
    )
    with torch.no_grad():
        expected = net(torch.from_numpy(sample)).numpy()
    return float(np.abs(session.run(None, {"x": sample})[0] - expected).max())


def write_candidate(
    temporary_model: Path, metrics: dict[str, Any], output: Path
) -> None:
    """Move the exported model and its metrics to `output` and its `.json`.

    Each is written under a temporary name and moved with `os.replace`, so a
    reader never sees half a file. An earlier candidate of the same name is
    replaced: there is one candidate at a time. Nothing else is touched.
    """
    metrics_path = output.with_suffix(".json")
    writing = metrics_path.with_name(metrics_path.name + ".tmp")
    writing.write_text(json.dumps(metrics, indent=1) + "\n", encoding="utf-8")
    os.replace(temporary_model, output)
    os.replace(writing, metrics_path)


# --- the command line --------------------------------------------------------


def _require(path: Path) -> Path:
    if not path.exists():
        raise TrainError(f"training asset is missing: {path} ({FETCH_HINT})")
    return path


def check_assets(run: Run, to_synthesise: Sequence[Shard]) -> None:
    """Stop with one line when something the run needs has not been fetched."""
    for voice in sorted({shard.voice for shard in to_synthesise}):
        _require(run.models / "piper" / f"{voice}.onnx")
        _require(run.models / "piper" / f"{voice}.onnx.json")
    for name in ("melspectrogram.onnx", "embedding_model.onnx"):
        _require(run.models / "openwakeword" / name)
    banks = (run.work / "noise16k.npz", run.work / "rir16k.npz")
    for directory, bank in zip((NOISE_DIR, RIR_DIR), banks, strict=True):
        if not bank.exists() and not any((run.data / directory).glob("*.wav")):
            raise TrainError(
                f"training asset is missing: {run.data / directory}/*.wav ({FETCH_HINT})"
            )
    _require(run.data / ACAV_FEATURES)
    _require(run.data / VALIDATION_FEATURES)


def stage_synth(run: Run, shards: Sequence[Shard]) -> None:
    (run.work / "clips").mkdir(parents=True, exist_ok=True)
    with_clips = [shard for shard in shards if not shard.noise]
    # A shard whose features exist does not need its clips again.
    todo = [
        shard
        for shard in pending(with_clips, lambda shard: clip_path(run.work, shard))
        if not feat_path(run.work, shard).exists()
    ]
    log(
        f"synth: {len(with_clips) - len(todo)}/{len(with_clips)} shards cached, "
        f"{len(todo)} to do"
    )
    check_assets(run, todo)
    # Largest first, so the estimate of the time left does not grow.
    todo.sort(key=lambda shard: -shard.clips)
    run_pool(synth_shard, _tasks(run, todo), run.threads, "synth")


def stage_feats(run: Run, shards: Sequence[Shard]) -> None:
    (run.work / "feats").mkdir(parents=True, exist_ok=True)
    todo = pending(shards, lambda shard: feat_path(run.work, shard))
    log(
        f"feats: {len(shards) - len(todo)}/{len(shards)} shards cached, {len(todo)} to do"
    )
    if todo:
        build_banks(run.data, run.work)
    run_pool(feats_shard, _tasks(run, todo), run.threads, "feats")


def _tasks(run: Run, shards: Sequence[Shard]) -> list[Task]:
    return [Task(shard, run.phrase, str(run.models), str(run.work)) for shard in shards]


def train(run: Run, stage: str) -> None:
    """Run the stages. `stage` is `all`, or one stage to stop after."""
    run.work.mkdir(parents=True, exist_ok=True)
    shards = plan_shards(run.scale)
    started = time.time()
    table = phrase_table(run.phrase)
    real = RealData(run.phrase, (), "")
    if run.dataset is not None:
        real = load_real(VoiceDataset(run.dataset), run.phrase)
    run = replace(run, real_manifest=real.manifest)
    log(
        f"training the wake-word model for {run.phrase!r}: scale {run.scale:g} "
        f"({len(shards)} shards), {run.steps} steps, {run.threads} threads; "
        f"{len(set(table.trained))} hard negatives"
        + (
            f", {len(table.train_excluded)} of them left out of the training"
            if run.exclude_near
            else ""
        )
        + f"; hard top-k weight {run.hard_topk:g}, negative weight "
        f"{run.negative_weight:g}; work in {run.work}"
    )
    for line in real.describe(table, run.exclude_near):
        log(line)
    stage_synth(run, shards)
    if stage == "synth":
        return
    stage_feats(run, shards)
    if stage == "feats":
        return
    check_assets(run, [])
    real_features = stage_real(run, real)
    if stage == "real":
        return
    net = stage_train(run, shards, real_features)
    run.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = run.output.with_name(run.output.name + ".tmp")
    difference = export_onnx(net, temporary)
    if difference > 1e-4:
        temporary.unlink()
        raise TrainError(
            f"the exported model differs from the trained one by {difference:.1e}"
        )
    try:
        metrics = stage_eval(run, shards, net, temporary, real)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    write_candidate(temporary, metrics, run.output)
    log(
        f"export: wrote {run.output} ({run.output.stat().st_size // 1024} kB; "
        f"ONNX against the network: {difference:.1e}) and "
        f"{run.output.with_suffix('.json').name}; recommended threshold "
        f"{metrics['recommended_threshold']}, chosen on the "
        f"{metrics['recommended_on']} figures"
    )
    log(
        f"done in {(time.time() - started) / 60:.1f} min. {run.output.name} is a "
        "candidate, ready for the interface: 'make deploy' compares it with the "
        "installed model and installs it. The installed model and the "
        "configuration are unchanged."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m autobot.wakeword.train",
        description="Train the wake-word model for one phrase. Resumable.",
    )
    parser.add_argument(
        "--phrase",
        default=None,
        help="the wake phrase (default: the first [[wake_phrases]] entry)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="the .onnx file to write (default: models/wakeword/candidate.onnx, "
        "the candidate the interface installs)",
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="how much to synthesise: 1.0 is about 23,000 training clips",
    )
    parser.add_argument("--steps", type=int, default=10000, help="training steps")
    parser.add_argument("--hidden", type=int, default=128, help="width of the layers")
    parser.add_argument("--threads", type=int, default=6, help="worker processes")
    parser.add_argument(
        "--exclude-near-homophones",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="leave the hard negatives that sound the same as the phrase out of "
        "the training (they are still evaluated, on their own); the default, "
        "--no-exclude-near-homophones trains on them",
    )
    parser.add_argument(
        "--hard-topk-weight",
        type=float,
        default=1.0,
        help="weight of the loss on the hardest 5%% of the hard negatives; 0 drops it",
    )
    parser.add_argument(
        "--negative-weight",
        type=float,
        default=1.0,
        help="weight of every negative term of the loss",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="an experiment: write candidates/<name>.onnx instead of "
        "candidate.onnx (the interface installs only candidate.onnx)",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=None,
        help="the voice dataset's directory (default: data/voice in the checkout)",
    )
    parser.add_argument(
        "--work", type=Path, default=None, help="where the stages cache their work"
    )
    parser.add_argument(
        "--stage",
        default="all",
        choices=["all", "synth", "feats", "real"],
        help="stop after this stage (default: run everything)",
    )
    return parser


def resolve_run(args: argparse.Namespace) -> Run:
    """The run's settings, from the arguments and the configuration."""
    config = load_config()
    phrase = args.phrase or config.wake_phrases[0].phrase
    output: Path = args.output or resolve_repo_path(
        WAKEWORD_MODELS_DIR / CANDIDATE_NAME
    )
    if output.suffix != ".onnx":
        raise TrainError(f"the model file must end in .onnx, not {output.name!r}")
    if args.steps < 1 or args.hidden < 1 or args.threads < 1:
        raise TrainError("--steps, --hidden and --threads must be at least 1")
    if args.hard_topk_weight < 0 or args.negative_weight <= 0:
        raise TrainError(
            "--hard-topk-weight must be at least 0 and --negative-weight above 0"
        )
    if args.name is not None:
        if slug(args.name) != args.name:
            raise TrainError(
                f"--name must be lower-case letters, digits and '_', not {args.name!r}"
            )
        output = output.parent / "candidates" / f"{args.name}.onnx"
    work: Path = args.work or resolve_repo_path(
        WAKEWORD_MODELS_DIR / "work" / slug(phrase)
    )
    return Run(
        phrase=phrase,
        output=output.resolve(),
        models=resolve_repo_path(MODELS_DIR),
        work=work.resolve(),
        scale=args.scale,
        steps=args.steps,
        hidden=args.hidden,
        threads=min(args.threads, os.cpu_count() or 1),
        exclude_near=args.exclude_near_homophones,
        hard_topk=args.hard_topk_weight,
        negative_weight=args.negative_weight,
        dataset=(args.dataset or resolve_repo_path(VOICE_DIR)).resolve(),
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Before torch, onnxruntime or piper can be imported, here or in a worker.
    enforce_offline()
    try:
        train(resolve_run(args), args.stage)
    except (ConfigError, DatasetError, TrainError) as error:
        print(error, file=sys.stderr, flush=True)
        return 2
    except ModuleNotFoundError as error:
        print(
            f"missing Python module {error.name!r}: the training needs the 'train' "
            "extra ('make train-wakeword' installs it)",
            file=sys.stderr,
            flush=True,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
