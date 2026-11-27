"""The `sherpa_kws` engine: open-vocabulary keyword spotting, no training.

This is the baseline the trained `openwakeword` model is measured against
(spec rule TS-14). sherpa-onnx's keyword spotter runs a very small streaming
speech recogniser (a 3.3 M parameter zipformer transducer trained on
GigaSpeech, English) whose beam search is restricted to the configured
phrase. Nothing is trained for the phrase: it is spelled in the model's own
word pieces when the detector is built. The model is a set of local files
under `models/sherpa-kws/` (`make fetch-models` puts them there) and runs on
the CPU on one thread (CONSTRAINTS.md CN-1).

How the phrase becomes a keyword:

- The phrase is upper-cased, since the model's vocabulary is upper-case, and
  everything but letters and apostrophes becomes a word break.
- The model's own sentencepiece model (`bpe.model`) splits it into word
  pieces. A word the model never saw is still spelled, in smaller pieces:
  "autobot" becomes `▁A U T O B O T`, "hey computer" `▁HE Y ▁COMP U TER`.
- A phrase with no letters, or with a digit or a letter outside A to Z in it
  (write "r two d two"), is a `ConfigError`: the model could not spell it.

How it decides:

- sherpa-onnx fires when its beam search has matched every piece of the
  phrase and the mean probability the model gave those pieces is at least
  its `keywords_threshold`. That probability is what `threshold` sets, on a
  halved scale: `keywords_threshold = threshold * THRESHOLD_SCALE` (0.5). So
  autobot's default threshold of 0.5 is sherpa-onnx's own default of 0.25,
  and the whole of 0 to 1 is usable. Measured, the threshold does little
  until it is high: of 140 "autobot" utterances in held-out synthetic voices
  it heard 88 at 0.2, 87 at 0.5, 84 at 0.7, 81 at 0.8, 72 at 0.9 and 54 at
  1.0, with the same 2 false accepts on 140 other sentences throughout.
- `KEYWORDS_BOOST` is the bonus each matched piece gets in the beam search
  (sherpa-onnx's `keywords_score`). It is fixed: see the constant.
- sherpa-onnx reports that the phrase was heard, not how sure it was. So the
  `score` of a `WakeEvent` is the configured `threshold` (the probability
  was at least that), and the detector has no `last_peak`: the listener's
  score for a stretch of speech is that threshold when it fired and nothing
  when it did not.
- The service gates frames through the VAD, so the engine sees stretches of
  speech only and is idle in silence. Each stretch is decoded as its own
  stream, from a fresh start: nothing is carried from one stretch into the
  next. The model decodes in chunks of about two thirds of a second and wants
  to see a little past the phrase, so at the end of a stretch the engine
  adds `TAIL_SECONDS` of silence and decodes what is left; an event can
  therefore come from `end_of_speech()`. The speech gate's default margins
  are enough (`GATE_MARGINS`): on the held-out voices the wider margins the
  `openwakeword` engine asks for heard no more (82 of 140 against 87) for
  nearly half as much audio again, and one unbroken stream with no gate at
  all heard fewer (79).
- After it fires it starts the stream afresh, as sherpa-onnx requires, so
  what is left of the utterance cannot match again, and it ignores further
  detections for the rest of that stretch of speech (at most
  `refractory_seconds` of it), so one utterance yields one event. The
  refractory period ends with the stretch. It is counted in fed audio, and
  silence is not fed: carried into the next stretch it swallowed a phrase
  said six seconds after the last one (11 of 140 on the held-out voices).
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from autobot.config import (
    KIND_SHERPA_KWS,
    AudioConfig,
    ConfigError,
    WakePhraseConfig,
)
from autobot.listener.detector import Clock, Detector, Frame, WakeEvent, utc_now
from autobot.listener.models import MODELS_DIR, enforce_offline, require_dir

# The model: https://github.com/k2-fsa/sherpa-onnx/releases/tag/kws-models
SHERPA_KWS_MODEL = "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"
SHERPA_KWS_DIR = MODELS_DIR / "sherpa-kws"
TOKENS_FILE = "tokens.txt"
BPE_MODEL_FILE = "bpe.model"
# The float models, not the int8 ones the archive also holds: on the NUC they
# are faster (2.2 ms against 3.2 ms a frame) and no worse on the fixtures.
ENCODER_FILE = "encoder-epoch-12-avg-2-chunk-16-left-64.onnx"
DECODER_FILE = "decoder-epoch-12-avg-2-chunk-16-left-64.onnx"
JOINER_FILE = "joiner-epoch-12-avg-2-chunk-16-left-64.onnx"
SHERPA_KWS_FILES = (
    TOKENS_FILE,
    BPE_MODEL_FILE,
    ENCODER_FILE,
    DECODER_FILE,
    JOINER_FILE,
)

SAMPLE_RATE = 16000
INT16_FULL_SCALE = 32768.0

# `threshold` times this is sherpa-onnx's `keywords_threshold`: see the
# module docstring.
THRESHOLD_SCALE = 0.5
# sherpa-onnx's `keywords_score`, its default. A larger bonus keeps weaker
# matches alive in the beam search. On 140 "autobot" utterances and 140 other
# sentences in held-out voices, at sherpa-onnx's default threshold: 0.5 heard
# 70 with 2 false accepts, 1.0 heard 87 with 2, 2.0 heard 92 with 6.
KEYWORDS_BOOST = 1.0
# Silence added at the end of a stretch so the decoder reaches its last frames.
TAIL_SECONDS = 0.66
# The label sherpa-onnx reports for the keyword; the phrase itself may hold spaces.
KEYWORD_LABEL = "WAKE"

# (pre-roll, hangover) frames this engine asks the speech gate for: the
# gate's own defaults. See the module docstring and `autobot.listener.detector`.
GATE_MARGINS = (3, 6)

_NOT_SPELLABLE = re.compile(r"[^A-Z']+")
# A digit or a letter outside A to Z: dropping it would change the phrase.
_FOREIGN = re.compile(r"[^\W_A-Z]")


class Spotter(Protocol):
    """Listens to a stream of frames for one phrase; `SherpaSpotter` or a test fake."""

    def accept(self, frame: Frame) -> bool:
        """Take the next frame; True when the phrase has just been heard."""
        ...

    def flush(self) -> bool:
        """The stream has ended: decode what is left of it, then start afresh.

        True when the phrase was heard in what was left.
        """
        ...

    def reset(self) -> None:
        """Forget the stream: the next frame does not follow the last one."""
        ...


def spelled(phrase: str) -> str:
    """The phrase as the model's vocabulary writes it: "Hey, autobot!" -> "HEY AUTOBOT"."""
    return _NOT_SPELLABLE.sub(" ", phrase.upper()).strip()


def known_tokens(tokens_file: Path) -> frozenset[str]:
    """The pieces in a `tokens.txt`: one piece and its number per line."""
    pieces: set[str] = set()
    for line in tokens_file.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) == 2:
            pieces.add(fields[0])
    return frozenset(pieces)


def keyword_pieces(phrase: str, model_dir: Path) -> tuple[str, ...]:
    """The phrase in the model's word pieces: "autobot" -> ("▁A", "U", "T", ...)."""
    text = spelled(phrase)
    foreign = sorted({char for char in phrase.upper() if _FOREIGN.fullmatch(char)})
    if foreign or not text:
        raise ConfigError(
            f"wake phrase {phrase!r}: the sherpa_kws model cannot spell "
            f"{foreign[0] if foreign else phrase!r} (it knows the letters A to Z "
            "only: write numbers as words)"
        )
    import sentencepiece

    processor = sentencepiece.SentencePieceProcessor()
    processor.Load(str(model_dir / BPE_MODEL_FILE))
    pieces: list[str] = processor.EncodeAsPieces(text)
    known = known_tokens(model_dir / TOKENS_FILE)
    unknown = [piece for piece in pieces if piece not in known]
    if unknown or not pieces:
        raise ConfigError(
            f"wake phrase {phrase!r}: the sherpa_kws model has no token for "
            f"{unknown[0] if unknown else text!r}"
        )
    return tuple(pieces)


def keywords_line(pieces: tuple[str, ...], threshold: float) -> str:
    """One line of a sherpa-onnx keywords file: pieces, boost, threshold, label."""
    return (
        f"{' '.join(pieces)} :{KEYWORDS_BOOST} "
        f"#{threshold * THRESHOLD_SCALE:.4f} @{KEYWORD_LABEL}"
    )


class SherpaSpotter:
    """sherpa-onnx's keyword spotter with one keyword, on the CPU, one thread."""

    def __init__(self, model_dir: Path, pieces: tuple[str, ...], threshold: float):
        enforce_offline()
        import sherpa_onnx

        # sherpa-onnx reads its keywords from a file, once, when it is built.
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", prefix="autobot-keywords-", suffix=".txt"
        ) as keywords:
            keywords.write(keywords_line(pieces, threshold) + "\n")
            keywords.flush()
            self._spotter: Any = sherpa_onnx.KeywordSpotter(
                tokens=str(model_dir / TOKENS_FILE),
                encoder=str(model_dir / ENCODER_FILE),
                decoder=str(model_dir / DECODER_FILE),
                joiner=str(model_dir / JOINER_FILE),
                keywords_file=keywords.name,
                # One thread: the model is small, and idle thread pools cost
                # CPU all day.
                num_threads=1,
                sample_rate=SAMPLE_RATE,
                provider="cpu",
            )
        self._tail = np.zeros(int(TAIL_SECONDS * SAMPLE_RATE), dtype=np.float32)
        self._stream: Any = self._spotter.create_stream()

    def _decode(self) -> bool:
        heard = False
        while self._spotter.is_ready(self._stream):
            self._spotter.decode_stream(self._stream)
            if self._spotter.get_result(self._stream):
                heard = True
                # sherpa-onnx requires this right after a detection.
                self._spotter.reset_stream(self._stream)
        return heard

    def accept(self, frame: Frame) -> bool:
        self._stream.accept_waveform(
            SAMPLE_RATE, frame.astype(np.float32) / INT16_FULL_SCALE
        )
        return self._decode()

    def flush(self) -> bool:
        self._stream.accept_waveform(SAMPLE_RATE, self._tail)
        self._stream.input_finished()
        heard = self._decode()
        self.reset()
        return heard

    def reset(self) -> None:
        self._stream = self._spotter.create_stream()


class SherpaKwsDetector:
    """A `Detector` that fires when the spotter hears the phrase."""

    gate_margins = GATE_MARGINS

    def __init__(
        self,
        phrase: str,
        target: str,
        spotter: Spotter,
        *,
        threshold: float = 0.5,
        frame_seconds: float = 0.08,
        refractory_seconds: float = 1.0,
        clock: Clock = utc_now,
    ):
        self.phrase = phrase
        self.target = target
        self.threshold = threshold
        self._spotter = spotter
        self._clock = clock
        self._refractory_frames = round(refractory_seconds / frame_seconds)
        self._refractory_left = 0

    def _event(self, heard: bool) -> WakeEvent | None:
        if not heard or self._refractory_left > 0:
            return None
        self._refractory_left = self._refractory_frames
        return WakeEvent(
            phrase=self.phrase,
            target=self.target,
            kind=KIND_SHERPA_KWS,
            # sherpa-onnx gives no score: the probability was at least this.
            score=round(self.threshold, 3),
            at=self._clock(),
        )

    def feed(self, frame: Frame) -> WakeEvent | None:
        # The spotter hears every frame, whether or not it may fire.
        heard = self._spotter.accept(frame)
        if self._refractory_left > 0:
            self._refractory_left -= 1
            return None
        return self._event(heard)

    def end_of_speech(self) -> WakeEvent | None:
        event = self._event(self._spotter.flush())
        # The refractory period ends with the stretch: the next stretch is a
        # fresh stream, and it is another utterance.
        self._refractory_left = 0
        return event

    def reset(self) -> None:
        self._spotter.reset()
        self._refractory_left = 0


def build(phrase: WakePhraseConfig, audio: AudioConfig) -> Detector:
    """The engine's builder: see `autobot.listener.detector.build_detector`."""
    if audio.sample_rate != SAMPLE_RATE:
        raise ConfigError(
            f"sherpa_kws needs audio at {SAMPLE_RATE} Hz, "
            f"not sample_rate = {audio.sample_rate}"
        )
    model_dir = require_dir(
        SHERPA_KWS_DIR, "sherpa-onnx keyword spotter", SHERPA_KWS_FILES
    )
    pieces = keyword_pieces(phrase.phrase, model_dir)
    return SherpaKwsDetector(
        phrase.phrase,
        phrase.target,
        SherpaSpotter(model_dir, pieces, phrase.threshold),
        threshold=phrase.threshold,
        frame_seconds=audio.frame_ms / 1000,
    )
