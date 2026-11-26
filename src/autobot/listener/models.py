"""Local model files and the no-network guard.

Every model the listener uses is a file under `models/` (git-ignored). Nothing
here, and nothing that calls it, downloads anything: a missing file is a
one-line `ModelMissingError`, never a fetch (CONSTRAINTS.md CN-1 and CN-2).
`make fetch-models` (`autobot.fetch_models`) is the separate command that puts
the files there.
"""

from __future__ import annotations

import os
from pathlib import Path

from autobot.config import resolve_repo_path

MODELS_DIR = Path("models")
SILERO_VAD_MODEL = MODELS_DIR / "silero" / "silero_vad.onnx"
FASTER_WHISPER_MODEL_DIR = MODELS_DIR / "faster-whisper" / "tiny.en"
PIPER_VOICES_DIR = MODELS_DIR / "piper"
# openWakeWord's shared feature extractor: audio -> mel, then mel -> embedding.
OPENWAKEWORD_MELSPEC_MODEL = MODELS_DIR / "openwakeword" / "melspectrogram.onnx"
OPENWAKEWORD_EMBEDDING_MODEL = MODELS_DIR / "openwakeword" / "embedding_model.onnx"
# Where `make train-wakeword` writes the wake-word models it trains.
WAKEWORD_MODELS_DIR = MODELS_DIR / "wakeword"

# Environment that keeps the speech libraries off the network. It must be set
# before the library is imported: call `enforce_offline()` first.
#
# - `ORT_DISABLE_TELEMETRY`: the onnxruntime wheel (seen in 1.32.0) uploads
#   usage telemetry to mobile.events.data.microsoft.com from a native thread a
#   few seconds after it is imported. This switches it off; calling
#   `onnxruntime.disable_telemetry_events()` after the import does not.
#   Verified with `strace -f -e trace=connect`.
# - The `HF_*` variables keep the Hugging Face hub code that faster-whisper
#   pulls in from looking anything up.
_OFFLINE_ENV = {
    "ORT_DISABLE_TELEMETRY": "1",
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
    "DO_NOT_TRACK": "1",
}


class ModelMissingError(Exception):
    """A local model file is missing. The message is one line."""


def enforce_offline() -> None:
    """Set the offline environment. Call before importing a speech library.

    That means before the first import, anywhere in the process, of
    `onnxruntime`, `faster_whisper`, `piper` or `torch`.
    """
    os.environ.update(_OFFLINE_ENV)


def require_file(path: str | Path, what: str) -> Path:
    """The absolute path of a model file under the repository, or `ModelMissingError`."""
    resolved = resolve_repo_path(path)
    if not resolved.is_file():
        raise ModelMissingError(
            f"{what} model file is missing: {resolved} (models are local files "
            "under models/ and are never downloaded at run time; "
            "'make fetch-models' fetches them)"
        )
    return resolved


def require_dir(path: str | Path, what: str, files: tuple[str, ...]) -> Path:
    """The absolute path of a model directory holding `files`, or `ModelMissingError`."""
    resolved = resolve_repo_path(path)
    for name in files:
        require_file(resolved / name, what)
    return resolved


def piper_voice_path(voice: str) -> Path:
    """The `.onnx` file of a piper voice; its `.onnx.json` must sit beside it."""
    model = require_file(PIPER_VOICES_DIR / f"{voice}.onnx", f"piper voice {voice!r}")
    require_file(PIPER_VOICES_DIR / f"{voice}.onnx.json", f"piper voice {voice!r}")
    return model
