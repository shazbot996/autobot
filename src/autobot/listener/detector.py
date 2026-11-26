"""The `Detector` protocol, `WakeEvent`, and the kind -> engine factory.

This module is the seam between the listener and its wake-word engines
(spec rule WW-3). The contract, which every engine relies on:

**Frames.** A `Frame` is a one-dimensional `numpy.int16` array of exactly
`AudioConfig.frame_samples` samples (1280 at 16 kHz and 80 ms), mono.

**What a detector is fed.** The service runs every microphone frame through
the speech gate (`vad.SpeechGate`). Silence never reaches a detector. For each
stretch of speech a detector sees, in order and without gaps:

1. `feed()` for up to `SpeechGate.pre_roll_frames` frames from just before the
   speech began (so the first syllable is not clipped),
2. `feed()` for every frame while the speech lasts, including the short pauses
   inside it and the `SpeechGate.hangover_frames` quiet frames that end it,
3. one `end_of_speech()` call.

Frames of one stretch are contiguous in time; consecutive stretches are not.

**Margins.** A detector that needs more audio around the speech than the
gate's defaults give it sets an attribute `gate_margins = (pre_roll_frames,
hangover_frames)`. The service then feeds it through a gate of its own with
those margins, driven by the same VAD score, and the other detectors are fed
as before. The `openwakeword` engine does this: its model wants about a
second of what came before the phrase and scores highest just after it ends.

**Events.** `feed()` and `end_of_speech()` each return a `WakeEvent` when the
phrase was heard, else `None`. One utterance of the phrase must yield one
event: an engine that scores frame by frame needs a refractory period.

**Muting.** `reset()` is called when audio was dropped (after the
acknowledgement plays, so the listener does not wake itself). The detector
must forget everything it has buffered.

**Blocking.** Calls run on the service's main loop. Microphone frames queue up
while a call runs, so a call may take a few hundred milliseconds
occasionally, but not on every frame.

**A new kind is one new module.** `build_detector` imports
`autobot.listener.engines.<kind>` and calls its
`build(phrase: WakePhraseConfig, audio: AudioConfig) -> Detector`. The kind
must also be listed in `autobot.config.WAKE_PHRASE_KINDS`. `build` raises
`ModelMissingError` when a model file is absent.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import numpy as np
import numpy.typing as npt

from autobot.config import AudioConfig, WakePhraseConfig

type Frame = npt.NDArray[np.int16]

ENGINES_PACKAGE = "autobot.listener.engines"


@dataclass(frozen=True)
class WakeEvent:
    """A wake phrase was heard."""

    # The configured phrase, as written in the configuration.
    phrase: str
    # What the wake is for; `ack` is the only target built so far.
    target: str
    # The detector kind that heard it.
    kind: str
    # The detector's confidence, from 0 to 1.
    score: float
    # When it was heard: timezone-aware, UTC.
    at: datetime


class Detector(Protocol):
    """One wake phrase, heard by one engine. See the module docstring."""

    def feed(self, frame: Frame) -> WakeEvent | None:
        """Take the next frame of a stretch of speech."""
        ...

    def end_of_speech(self) -> WakeEvent | None:
        """The stretch of speech fed so far has ended."""
        ...

    def reset(self) -> None:
        """Forget all buffered audio and state: audio was dropped."""
        ...


type DetectorBuilder = Callable[[WakePhraseConfig, AudioConfig], Detector]
type Clock = Callable[[], datetime]

# Builders registered at run time; they win over the engine modules.
_BUILDERS: dict[str, DetectorBuilder] = {}


class UnknownKindError(Exception):
    """No engine implements a wake phrase kind. The message is one line."""


def utc_now() -> datetime:
    """The default `Clock`."""
    return datetime.now(UTC)


def register(kind: str, builder: DetectorBuilder) -> None:
    """Register `builder` for `kind`, replacing the engine module of that name.

    Engines do not need this: a module `engines/<kind>.py` with a `build`
    function is found by name. It exists for fakes in tests and for kinds
    built outside the `engines` package.
    """
    _BUILDERS[kind] = builder


def unregister(kind: str) -> None:
    """Remove a builder added with `register`."""
    _BUILDERS.pop(kind, None)


def builder_for(kind: str) -> DetectorBuilder:
    """The builder for `kind`: a registered one, else `engines/<kind>.py`'s `build`."""
    if kind in _BUILDERS:
        return _BUILDERS[kind]
    if not kind.isidentifier():
        raise UnknownKindError(f"wake phrase kind {kind!r} is not a valid kind name")
    name = f"{ENGINES_PACKAGE}.{kind}"
    try:
        module = importlib.import_module(name)
    except ModuleNotFoundError as error:
        if error.name == name:
            raise UnknownKindError(
                f"wake phrase kind {kind!r} has no engine: {name} does not exist"
            ) from None
        raise UnknownKindError(
            f"wake phrase kind {kind!r} cannot load: missing Python module "
            f"{error.name!r} (run 'make setup')"
        ) from None
    builder: DetectorBuilder | None = getattr(module, "build", None)
    if builder is None:
        raise UnknownKindError(f"engine module {name} has no build() function")
    return builder


def build_detector(phrase: WakePhraseConfig, audio: AudioConfig) -> Detector:
    """Build the detector for one `[[wake_phrases]]` entry."""
    return builder_for(phrase.kind)(phrase, audio)
