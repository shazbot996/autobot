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
3. **train**: a small network on the 16 x 96 features, against those windows
   and openWakeWord's general negative features. Checkpointed every 1000
   steps (`ckpt-<hidden>-<steps>.pt`: one per network size, schedule and
   loss, so trying another does not throw a finished run away).
4. **eval**: recall on voices never used in training, false accepts on hard
   negatives, and false accepts per hour on openWakeWord's validation
   features, for a sweep of thresholds. The hard negatives that sound the
   same as the phrase (`PhraseTable.train_excluded`) are reported on their
   own and are not counted with the others.
5. **install**: the ONNX file and a `.json` of the metrics are moved into
   place with `os.replace`, so a running listener never reads half a file.
   The model that was there is kept as `<name>.prev.onnx`.

Three options change what the network is trained against, for comparing
models on the same cached features: `--exclude-near-homophones` leaves the
windows of `PhraseTable.train_excluded` out of the training,
`--hard-topk-weight` weights the loss on the hardest hard negatives (0 drops
it) and `--negative-weight` weights every negative term. `--name` writes the
result to `candidates/<name>.onnx` beside the installed model and leaves the
installed model alone.

Shards are additive: a larger `--scale` adds shards and keeps every finished
one, so a short first run is not wasted. A shard is identified by its name
alone and a finished shard is never remade.

Nothing here opens a network connection (CONSTRAINTS.md CN-1). The voices and
data sets are fetched beforehand by `make fetch-models TRAIN=1`; a missing
one stops the run with one line saying so.

The training-only libraries (`torch`, `scipy`, `soundfile`, `onnx`) are the
`train` extra of `pyproject.toml` and are imported inside the functions that
use them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
import time
import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from autobot.config import ConfigError, load_config, resolve_repo_path
from autobot.listener.engines.openwakeword import (
    EMBEDDING_SIZE,
    FRAME_SAMPLES,
    MODEL_EMBEDDINGS,
    SAMPLE_RATE,
)
from autobot.listener.models import (
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

type Int16 = npt.NDArray[np.int16]
type Float32 = npt.NDArray[np.float32]
type Int64 = npt.NDArray[np.int64]

SPLIT_TRAIN = "train"
SPLIT_HELDOUT = "heldout"

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
    exclude_near: bool = False
    # Weight of the loss on the hardest 5 % of the hard negatives; 0 drops it.
    hard_topk: float = 1.0
    # Weight of every negative term of the loss.
    negative_weight: float = 1.0
    # A candidate is written beside the installed model, not over it.
    candidate: bool = False

    @property
    def data(self) -> Path:
        return self.models / "wakeword"

    @property
    def checkpoint(self) -> Path:
        return self.work / f"ckpt-{self.hidden}-{self.steps}{self.variant}.pt"

    @property
    def variant(self) -> str:
        """What tells this run's loss from the first one's; empty for that one."""
        settings = (self.exclude_near, self.hard_topk, self.negative_weight)
        if settings == (False, 1.0, 1.0):
            return ""
        return (
            f"-x{int(self.exclude_near)}-k{self.hard_topk:g}-n{self.negative_weight:g}"
        )


# What a checkpoint written before these settings existed was trained with.
CHECKPOINT_DEFAULTS: dict[str, Any] = {
    "excluded": [],
    "hard_topk": 1.0,
    "negative_weight": 1.0,
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


def stage_train(run: Run, shards: Sequence[Shard]) -> Any:
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

    def gather(windows: Int64, inputs: Int64) -> Any:
        picked = features[windows[:, np.newaxis], inputs[:, np.newaxis] + offsets]
        return torch.from_numpy(picked.astype(np.float32))

    log(
        f"train: {len(features)} windows -> {len(positive_window)} positive and "
        f"{len(negative_window)} negative inputs, {acav_rows} general negatives "
        f"({acav_rows * 1.28 / 3600:.0f} h); {run.steps} steps on {run.threads} threads"
    )
    rng = np.random.default_rng(step + 1)
    loss_of = torch.nn.functional.binary_cross_entropy_with_logits
    started, first_step = time.time(), step
    net.train()
    while step < run.steps:
        chosen = rng.integers(0, len(positive_window), 256)
        positives = gather(positive_window[chosen], positive_input[chosen])
        chosen = rng.integers(0, len(negative_window), 384)
        negatives = gather(negative_window[chosen], negative_input[chosen])
        rows = np.sort(rng.integers(0, acav_rows, 1536))
        general = torch.from_numpy(acav[rows].astype(np.float32))
        positive_loss = loss_of(net.logits(positives), torch.ones(256, 1))
        negative_loss = loss_of(
            net.logits(negatives), torch.zeros(384, 1), reduction="none"
        )[:, 0]
        general_loss = loss_of(
            net.logits(general), torch.zeros(1536, 1), reduction="none"
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


# --- stages 4 and 5: evaluation, export and install ---------------------------


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


def stage_eval(run: Run, shards: Sequence[Shard], net: Any) -> dict[str, Any]:
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
    return {
        "phrase": run.phrase,
        "input": "[N,16,96] float32 openWakeWord embeddings",
        "output": "[N,1] score 0..1",
        "recommended_threshold": best["threshold"],
        "meets_false_accept_target": meets,
        "metrics_at_threshold": best,
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


def previous_path(path: Path) -> Path:
    """Where the file `path` replaces is kept: `autobot.onnx` -> `autobot.prev.onnx`."""
    return path.with_name(f"{path.stem}.prev{path.suffix}")


def install(temporary_model: Path, metrics: dict[str, Any], output: Path) -> None:
    """Move the new model and its metrics into place, keeping the old ones.

    Every step is a copy to a temporary name and an `os.replace`, so the
    model file is whole at every moment: a listener starting meanwhile loads
    the old model or the new one, never half of either.
    """
    metrics_path = output.with_suffix(".json")
    for current in (output, metrics_path):
        if current.exists():
            kept = previous_path(current)
            copying = kept.with_name(kept.name + ".tmp")
            shutil.copy2(current, copying)
            os.replace(copying, kept)
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
    stage_synth(run, shards)
    if stage == "synth":
        return
    stage_feats(run, shards)
    if stage == "feats":
        return
    check_assets(run, [])
    net = stage_train(run, shards)
    run.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = run.output.with_name(run.output.name + ".tmp")
    difference = export_onnx(net, temporary)
    if difference > 1e-4:
        temporary.unlink()
        raise TrainError(
            f"the exported model differs from the trained one by {difference:.1e}"
        )
    metrics = stage_eval(run, shards, net)
    replaced = run.output.exists()
    install(temporary, metrics, run.output)
    log(
        f"export: wrote {run.output} ({run.output.stat().st_size // 1024} kB; "
        f"ONNX against the network: {difference:.1e})"
        + (
            f"; the model it replaced is kept as {previous_path(run.output).name}"
            if replaced
            else ""
        )
    )
    if run.candidate:
        log(
            f"done in {(time.time() - started) / 60:.1f} min. {run.output.name} is a "
            "candidate: the installed model and the configuration are unchanged."
        )
        return
    log(
        f"done in {(time.time() - started) / 60:.1f} min. Set threshold = "
        f"{metrics['recommended_threshold']} for {run.phrase!r} in "
        "config/autobot.toml and restart the listener ('make down; make up')."
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
        help="the .onnx file to write (default: the phrase's 'model' in the "
        "configuration, else models/wakeword/<phrase>.onnx)",
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
        default=False,
        help="leave the hard negatives that sound the same as the phrase out of "
        "the training (they are still evaluated, on their own)",
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
        help="train a candidate: write candidates/<name>.onnx beside the model "
        "file and leave the installed model alone",
    )
    parser.add_argument(
        "--work", type=Path, default=None, help="where the stages cache their work"
    )
    parser.add_argument(
        "--stage",
        default="all",
        choices=["all", "synth", "feats"],
        help="stop after this stage (default: run everything)",
    )
    return parser


def resolve_run(args: argparse.Namespace) -> Run:
    """The run's settings, from the arguments and the configuration."""
    config = load_config()
    phrase = args.phrase or config.wake_phrases[0].phrase
    output: Path | None = args.output
    if output is None:
        for entry in config.wake_phrases:
            if entry.phrase.lower() == phrase.lower() and entry.model_path is not None:
                output = entry.model_path
        if output is None:
            output = resolve_repo_path(WAKEWORD_MODELS_DIR / f"{slug(phrase)}.onnx")
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
        candidate=args.name is not None,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Before torch, onnxruntime or piper can be imported, here or in a worker.
    enforce_offline()
    try:
        train(resolve_run(args), args.stage)
    except (ConfigError, TrainError) as error:
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
