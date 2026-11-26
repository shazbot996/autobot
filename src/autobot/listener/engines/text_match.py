"""The `text_match` engine: the bootstrap detector.

It buffers a stretch of speech, transcribes it locally with faster-whisper
(`tiny.en`, CPU, int8) and fires when the transcript contains the configured
phrase. "Hey autobot" and "yo autobot" both match because the test is
containment, not equality.

It is the expensive kind: every stretch of speech in the room costs a
transcription. The `openwakeword` kind replaces it for 24x7 use; this one
stays as the fallback and as the second kind that proves WW-3.

How it decides:

- The buffer is transcribed at the end of speech, or as soon as it holds
  `max_buffer_seconds` (two seconds) during longer speech. After a capped
  transcription without a match the last `overlap_seconds` are kept, so a
  phrase straddling the cut is whole in the next buffer.
- The transcript and the phrase are normalised: lower-cased, apostrophes
  dropped, anything else that is not a letter or digit turned into a word
  break. The phrase matches when its letters, spaces removed, appear in the
  transcript's letters, spaces removed, starting at the start of a word. So
  "Autobot", "auto bot", "auto-bot" and "autobots" match `autobot`; "auto"
  does not, and neither does a word that merely ends in it.
- The configured phrase is given to whisper as a hotword, worded as a
  glossary entry ("Glossary: autobot."), which tells the decoder how the word
  is spelled. Without it `tiny.en` writes "or to bolt" or "auto butt" for a
  clearly spoken "autobot" about one time in four. The bare word as the
  hotword overdoes it and turns a spoken "auto" into "autobot"; the glossary
  wording did not, across ten synthetic voices.
- That hint has a price: now and then whisper writes "autobot" for a spoken
  "auto". So a match is confirmed by a second transcription without the
  hint, which must either contain the phrase too or at least sound like it:
  the phrase's consonants, in order, must appear among the transcript's
  (`sounds_like`). "or to bolt" and "auto butt" confirm `autobot`; "auto",
  "or two" and "bye" do not. The second pass runs only on a match, so it
  costs nothing while nobody says the phrase.
- The score is the hinted transcript's confidence, exp(mean token
  log-probability), and the detector fires only at or above the phrase's
  `threshold`.
- After firing on a capped buffer it ignores the rest of that stretch of
  speech (at most `refractory_seconds`), so one utterance yields one event.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

from autobot.config import (
    KIND_TEXT_MATCH,
    AudioConfig,
    ConfigError,
    WakePhraseConfig,
)
from autobot.listener.detector import Clock, Detector, Frame, WakeEvent, utc_now
from autobot.listener.models import (
    FASTER_WHISPER_MODEL_DIR,
    enforce_offline,
    require_dir,
)

INT16_FULL_SCALE = 32768.0
WHISPER_SAMPLE_RATE = 16000
WHISPER_FILES = ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt")
# Two threads: the listener shares the NUC, and four buy little on short clips.
WHISPER_CPU_THREADS = 2
# How the phrase is put to whisper as a hotword: see the module docstring.
HOTWORDS_FORMAT = "Glossary: {hint}."

# Letters that carry no consonant sound of their own, and voiced consonants
# folded onto their unvoiced partners, for `consonant_skeleton`.
_NOT_CONSONANTS = re.compile(r"[aeiouyhw ]+")
_UNVOICED = str.maketrans("bdgvzqcj", "ptkfskks")
_REPEATS = re.compile(r"(.)\1+")

_APOSTROPHES = re.compile(r"['’]")
_NOT_WORD = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Transcript:
    text: str
    # Confidence from 0 to 1: exp(mean token log-probability).
    score: float


class Transcriber(Protocol):
    """Local speech-to-text; `WhisperTranscriber` or a test fake."""

    def transcribe(self, samples: npt.NDArray[np.float32], hint: str) -> Transcript:
        """Transcribe mono float32 audio at 16 kHz. `hint` is a phrase to expect."""
        ...


class WhisperTranscriber:
    """faster-whisper `tiny.en` on the CPU, loaded from a local directory only."""

    def __init__(self, model_dir: str | Path | None = None):
        path = require_dir(
            model_dir or FASTER_WHISPER_MODEL_DIR, "faster-whisper", WHISPER_FILES
        )
        enforce_offline()
        from faster_whisper import WhisperModel

        self._model: Any = WhisperModel(
            str(path),
            device="cpu",
            compute_type="int8",
            cpu_threads=WHISPER_CPU_THREADS,
            local_files_only=True,
        )

    def transcribe(self, samples: npt.NDArray[np.float32], hint: str) -> Transcript:
        segments, _info = self._model.transcribe(
            samples,
            language="en",
            beam_size=5,
            # One pass at temperature 0: deterministic, and bounded in time.
            temperature=0.0,
            condition_on_previous_text=False,
            without_timestamps=True,
            vad_filter=False,
            hotwords=HOTWORDS_FORMAT.format(hint=hint) if hint else None,
        )
        texts: list[str] = []
        log_probabilities: list[float] = []
        for segment in segments:
            texts.append(segment.text)
            log_probabilities.append(float(segment.avg_logprob))
        if not texts:
            return Transcript(text="", score=0.0)
        mean = sum(log_probabilities) / len(log_probabilities)
        return Transcript(text=" ".join(texts).strip(), score=min(1.0, math.exp(mean)))


@cache
def shared_transcriber() -> WhisperTranscriber:
    """One whisper model per process, shared by every `text_match` phrase."""
    return WhisperTranscriber()


def normalise(text: str) -> str:
    """Lower-case words separated by single spaces; punctuation removed."""
    lowered = _APOSTROPHES.sub("", text.lower())
    return _NOT_WORD.sub(" ", lowered).strip()


def contains_phrase(transcript: str, phrase: str) -> bool:
    """True when `transcript` contains `phrase`, by the module's matching rule."""
    wanted = normalise(phrase).replace(" ", "")
    if not wanted:
        return False
    words = normalise(transcript).split()
    compact = "".join(words)
    start = 0
    for word in words:
        if compact.startswith(wanted, start):
            return True
        start += len(word)
    return False


def consonant_skeleton(text: str) -> str:
    """The consonant sounds of `text`, roughly: "Auto butt." -> "tpt".

    Vowels, `h`, `w` and `y` are dropped, voiced consonants become their
    unvoiced partners (b -> p, d -> t, g -> k, v -> f, z -> s) and repeats
    collapse. It is spelling, not phonetics, but it is enough to tell
    "or to bolt" from "or two".
    """
    consonants = _NOT_CONSONANTS.sub("", normalise(text)).translate(_UNVOICED)
    return _REPEATS.sub(r"\1", consonants)


def sounds_like(transcript: str, phrase: str) -> bool:
    """True when the phrase's consonants appear, in order, in the transcript's."""
    wanted = consonant_skeleton(phrase)
    if not wanted:
        return False
    remaining = iter(consonant_skeleton(transcript))
    return all(sound in remaining for sound in wanted)


class TextMatchDetector:
    """A `Detector` that transcribes speech and looks for the phrase."""

    def __init__(
        self,
        phrase: str,
        target: str,
        transcriber: Transcriber,
        *,
        threshold: float = 0.5,
        sample_rate: int = WHISPER_SAMPLE_RATE,
        max_buffer_seconds: float = 2.0,
        overlap_seconds: float = 1.0,
        min_seconds: float = 0.3,
        refractory_seconds: float = 2.0,
        clock: Clock = utc_now,
    ):
        if sample_rate != WHISPER_SAMPLE_RATE:
            raise ConfigError(
                f"text_match needs audio at {WHISPER_SAMPLE_RATE} Hz, "
                f"not sample_rate = {sample_rate}"
            )
        self.phrase = phrase
        self.target = target
        self.threshold = threshold
        self._transcriber = transcriber
        self._clock = clock
        self._max_samples = int(max_buffer_seconds * sample_rate)
        self._overlap_samples = int(overlap_seconds * sample_rate)
        self._min_samples = int(min_seconds * sample_rate)
        self._refractory_samples = int(refractory_seconds * sample_rate)
        self._chunks: list[Frame] = []
        self._buffered = 0
        # Samples buffered since the last transcription.
        self._fresh = 0
        self._refractory_left = 0
        # The last transcript, for the log and for tests.
        self.last_transcript: Transcript | None = None

    def feed(self, frame: Frame) -> WakeEvent | None:
        if self._refractory_left > 0:
            self._refractory_left -= len(frame)
            return None
        self._chunks.append(frame)
        self._buffered += len(frame)
        self._fresh += len(frame)
        if self._buffered < self._max_samples:
            return None
        event = self._transcribe()
        if event is not None:
            self._clear()
            self._refractory_left = self._refractory_samples
        else:
            self._keep_overlap()
        return event

    def end_of_speech(self) -> WakeEvent | None:
        event = None
        if self._refractory_left <= 0 and self._fresh >= self._min_samples:
            event = self._transcribe()
        self.reset()
        return event

    def reset(self) -> None:
        self._clear()
        self._refractory_left = 0

    def _clear(self) -> None:
        self._chunks = []
        self._buffered = 0
        self._fresh = 0

    def _keep_overlap(self) -> None:
        samples = np.concatenate(self._chunks)[-self._overlap_samples :]
        self._chunks = [samples]
        self._buffered = len(samples)
        self._fresh = 0

    def _transcribe(self) -> WakeEvent | None:
        samples = np.concatenate(self._chunks).astype(np.float32) / INT16_FULL_SCALE
        self._fresh = 0
        transcript = self._transcriber.transcribe(samples, self.phrase)
        self.last_transcript = transcript
        if transcript.score < self.threshold:
            return None
        if not contains_phrase(transcript.text, self.phrase):
            return None
        # Confirm without the hint: see the module docstring.
        unhinted = self._transcriber.transcribe(samples, "").text
        if not (
            contains_phrase(unhinted, self.phrase) or sounds_like(unhinted, self.phrase)
        ):
            return None
        return WakeEvent(
            phrase=self.phrase,
            target=self.target,
            kind=KIND_TEXT_MATCH,
            score=round(transcript.score, 3),
            at=self._clock(),
        )


def build(phrase: WakePhraseConfig, audio: AudioConfig) -> Detector:
    """The engine's builder: see `autobot.listener.detector.build_detector`."""
    return TextMatchDetector(
        phrase.phrase,
        phrase.target,
        shared_transcriber(),
        threshold=phrase.threshold,
        sample_rate=audio.sample_rate,
    )
