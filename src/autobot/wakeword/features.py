"""openWakeWord features for whole clips, with `onnxruntime` only.

The training pipeline needs the features of many clips of equal length. This
is what `openwakeword.utils.AudioFeatures.embed_clips` computes; it is
repeated here so that the `openwakeword` package (which cannot be installed
on Python 3.12) is not needed, and was checked to give the same numbers.

A clip of `n` samples gives `ceil(n / 160 - 3)` mel rows and one embedding
for every 8 rows once 76 are available: 16 embeddings for 2 s, 28 for 3 s,
41 for 4 s.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import numpy.typing as npt

from autobot.listener.engines.openwakeword import (
    EMBEDDING_MEL_ROWS,
    EMBEDDING_SIZE,
    EMBEDDING_STEP_ROWS,
    MEL_BINS,
    feature_sessions,
    mel_transform,
)

# Clips embedded per call of the embedding model, as openWakeWord batches them.
CLIP_BATCH = 64


def embedding_count(samples: int) -> int:
    """How many embeddings a clip of `samples` samples gives."""
    mel_rows = -(-samples // 160) - 3
    return max(0, (mel_rows - EMBEDDING_MEL_ROWS) // EMBEDDING_STEP_ROWS + 1)


class ClipFeatures:
    """Embeds batches of equal-length int16 clips. One-thread sessions."""

    def __init__(self, sessions: tuple[Any, Any] | None = None):
        self._melspec, self._embedding = sessions or feature_sessions()
        self._melspec_input: str = self._melspec.get_inputs()[0].name
        self._embedding_input: str = self._embedding.get_inputs()[0].name

    def embed_clips(self, clips: npt.NDArray[np.int16]) -> npt.NDArray[np.float32]:
        """`clips` [n, samples] int16 at 16 kHz -> embeddings [n, frames, 96]."""
        count, samples = clips.shape
        frames = embedding_count(samples)
        if frames < 1:
            raise ValueError(f"a clip of {samples} samples is too short to embed")
        out = np.empty((count, frames, EMBEDDING_SIZE), dtype=np.float32)
        starts = np.arange(frames) * EMBEDDING_STEP_ROWS
        rows = starts[:, np.newaxis] + np.arange(EMBEDDING_MEL_ROWS)[np.newaxis, :]
        for first in range(0, count, CLIP_BATCH):
            batch = clips[first : first + CLIP_BATCH]
            windows = np.empty(
                (len(batch), frames, EMBEDDING_MEL_ROWS, MEL_BINS), dtype=np.float32
            )
            for index, clip in enumerate(batch):
                # One clip per call, as openWakeWord does it.
                spectrogram = self._melspec.run(
                    None, {self._melspec_input: clip.astype(np.float32)[np.newaxis, :]}
                )[0]
                mel = mel_transform(spectrogram.reshape(-1, MEL_BINS))
                windows[index] = mel[rows]
            embedded = self._embedding.run(
                None,
                {
                    self._embedding_input: windows.reshape(
                        -1, EMBEDDING_MEL_ROWS, MEL_BINS, 1
                    )
                },
            )[0]
            out[first : first + len(batch)] = embedded.reshape(
                len(batch), frames, EMBEDDING_SIZE
            )
        return out
