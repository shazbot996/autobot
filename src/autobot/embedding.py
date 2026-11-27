"""Sentence embeddings on the NUC's CPU, for tier 2 of the routing cascade (IS-17).

Tier 2 asks whether what was said means what a routing rule's command or
one of its example phrases means. Both are turned into vectors by a small
sentence-embedding model and compared by cosine similarity. The model stays
on the NUC, so this tier works while the inference server is away (IS-4).

`LocalEmbedder` runs `all-MiniLM-L6-v2` as an int8 ONNX file under
`onnxruntime`, with the `tokenizers` package for its `tokenizer.json`, which
sits beside the model file (`[server] embedding_model`). Both are loaded
from that local path and nowhere else: the offline environment is set
before either library is imported, nothing is looked up by name, and a
missing file is an `EmbeddingError` that names it and the `make
fetch-models` that fetches it. `make fetch-models` is the only thing that
downloads (`CONVENTIONS.md` "Local-only speech").

The text embedded is a transcript the NUC already has. Nothing leaves the
machine here.

An embedding is the mean of the model's token vectors, weighted by the
attention mask, scaled to length 1, so the dot product of two is their
cosine similarity. The vectors of the texts last asked for are kept, since
the rules' commands and examples are embedded again for every transcript.

`Embedder` is the seam; the tests give a fake that loads no model.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

from autobot.listener.models import enforce_offline

TOKENIZER_FILE = "tokenizer.json"
# The most tokens of a text the model is given: a command is a few words.
MAX_TOKENS = 64
# How many texts' vectors are kept.
CACHE_TEXTS = 512

type Vectors = npt.NDArray[np.float32]


class EmbeddingError(Exception):
    """The embedding model cannot be used. The message is one line."""


class Embedder(Protocol):
    """Turns texts into unit vectors, one row each."""

    def load(self) -> None:
        """Load the model now, so the first `embed` does not wait for it."""
        ...

    def embed(self, texts: Sequence[str]) -> Vectors:
        """The vectors of `texts`, each of length 1, as rows. It blocks."""
        ...


def similarity(query: Vectors, phrases: Vectors) -> Vectors:
    """The cosine similarity of the one row `query` to each row of `phrases`."""
    scores: Vectors = (phrases @ query.reshape(-1)).astype(np.float32)
    return scores


def unit(vectors: npt.NDArray[Any]) -> Vectors:
    """`vectors` with every row scaled to length 1; a zero row stays zero."""
    lengths = np.linalg.norm(vectors, axis=1, keepdims=True)
    scaled: Vectors = (vectors / np.maximum(lengths, 1e-12)).astype(np.float32)
    return scaled


class LocalEmbedder:
    """`all-MiniLM-L6-v2` from a local ONNX file, with its tokenizer beside it."""

    def __init__(self, model_path: Path) -> None:
        self._model_path = model_path
        self._tokenizer_path = model_path.parent / TOKENIZER_FILE
        self._lock = threading.Lock()
        self._session: Any = None
        self._tokenizer: Any = None
        self._inputs: tuple[str, ...] = ()
        self._cache: OrderedDict[str, Vectors] = OrderedDict()

    def load(self) -> None:
        with self._lock:
            self._loaded()

    def _loaded(self) -> None:
        if self._session is not None:
            return
        for path in (self._model_path, self._tokenizer_path):
            if not path.is_file():
                raise EmbeddingError(
                    f"embedding model file is missing: {path} "
                    "(models are never downloaded at run time; "
                    "'make fetch-models' fetches them)"
                )
        enforce_offline()
        import onnxruntime
        from tokenizers import Tokenizer

        try:
            tokenizer = Tokenizer.from_file(str(self._tokenizer_path))
            tokenizer.enable_truncation(max_length=MAX_TOKENS)
            tokenizer.enable_padding()
            options = onnxruntime.SessionOptions()
            # A command is short: one thread is enough and leaves the
            # listener its cores.
            options.intra_op_num_threads = 1
            options.inter_op_num_threads = 1
            session = onnxruntime.InferenceSession(
                str(self._model_path),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
        except Exception as error:  # noqa: BLE001
            line = " ".join(str(error).split()) or type(error).__name__
            raise EmbeddingError(
                f"cannot load the embedding model {self._model_path}: {line}"
            ) from None
        self._tokenizer = tokenizer
        self._inputs = tuple(entry.name for entry in session.get_inputs())
        self._session = session

    def embed(self, texts: Sequence[str]) -> Vectors:
        with self._lock:
            self._loaded()
            wanted = [" ".join(text.split()) for text in texts]
            fresh = [text for text in dict.fromkeys(wanted) if text not in self._cache]
            if fresh:
                for text, vector in zip(fresh, self._run(fresh), strict=True):
                    self._cache[text] = vector
                while len(self._cache) > max(CACHE_TEXTS, len(fresh)):
                    self._cache.popitem(last=False)
            for text in wanted:
                self._cache.move_to_end(text)
            stacked: Vectors = np.stack([self._cache[text] for text in wanted])
            return stacked

    def _run(self, texts: Sequence[str]) -> Vectors:
        encoded = self._tokenizer.encode_batch(list(texts))
        ids = np.array([entry.ids for entry in encoded], dtype=np.int64)
        mask = np.array([entry.attention_mask for entry in encoded], dtype=np.int64)
        feeds: dict[str, npt.NDArray[np.int64]] = {}
        for name in self._inputs:
            if name == "input_ids":
                feeds[name] = ids
            elif name == "attention_mask":
                feeds[name] = mask
            elif name == "token_type_ids":
                feeds[name] = np.zeros_like(ids)
        try:
            tokens = self._session.run(None, feeds)[0]
        except Exception as error:  # noqa: BLE001
            line = " ".join(str(error).split()) or type(error).__name__
            raise EmbeddingError(f"the embedding model failed: {line}") from None
        # The mean of the token vectors that are not padding.
        weights = mask[:, :, None].astype(np.float32)
        summed = (np.asarray(tokens, dtype=np.float32) * weights).sum(axis=1)
        return unit(summed / np.maximum(weights.sum(axis=1), 1.0))
