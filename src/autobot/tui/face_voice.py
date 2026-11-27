"""autobot's voice for the face: text rendered with local piper-tts. No drawing.

`synthesise` renders a line in the configured voice, `[ack].voice`, from the
voice file `models/piper/<voice>.onnx` and its `.onnx.json` under the models
root, into memory. Synthesis is local (CONSTRAINTS.md CN-2): the offline
environment is set before `piper` is imported, and `piper` is imported inside
the function, so importing this module loads no speech library. A missing
voice file is a one-line error, never a download.

Only the offline guard is taken from `autobot.listener.models`: it is the one
list of offline variables, not the listener process, which the interface
never imports.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from autobot.listener.models import enforce_offline
from autobot.tui.audio import Clip

# Where the piper voices are, under the models root.
PIPER_VOICES_DIR = Path("models/piper")
# Silence after the speech, so the output device does not cut the last sound.
TAIL_SECONDS = 0.15


class VoiceError(Exception):
    """autobot's voice cannot be rendered. The message is one line."""


def voice_files(voice: str, models_root: Path) -> tuple[Path, Path]:
    """The `.onnx` file of a piper voice and its `.onnx.json`, or `VoiceError`."""
    model = models_root / PIPER_VOICES_DIR / f"{voice}.onnx"
    settings = models_root / PIPER_VOICES_DIR / f"{voice}.onnx.json"
    for path in (model, settings):
        if not path.is_file():
            raise VoiceError(
                f"piper voice file is missing: {path} ('make fetch-models' fetches it)"
            )
    return model, settings


def synthesise(text: str, voice: str, models_root: Path) -> Clip:
    """`text` spoken in `voice`, as mono int16 at the voice's own rate."""
    model, settings = voice_files(voice, models_root)
    enforce_offline()
    from piper import PiperVoice

    loaded = PiperVoice.load(model, config_path=settings)
    chunks = [chunk.audio_int16_array for chunk in loaded.synthesize(text)]
    rate = int(loaded.config.sample_rate)
    tail = np.zeros(int(TAIL_SECONDS * rate), dtype=np.int16)
    return Clip(np.concatenate([*chunks, tail]).astype(np.int16), rate)
