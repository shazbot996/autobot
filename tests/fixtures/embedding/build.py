"""Builds the tiny embedding fixture of tests/fixtures/embedding/. Run once, by hand.

A stand-in for all-MiniLM-L6-v2 with the same inputs and output: token ids
in, one vector per token out. Each word of a small vocabulary has its own
dimension, so two texts are similar by the words they share.
"""

import sys
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper
from tokenizers import Tokenizer, models, normalizers, pre_tokenizers

target = Path(sys.argv[1])
target.mkdir(parents=True, exist_ok=True)

WORDS = [
    "[PAD]",
    "[UNK]",
    "weather",
    "forecast",
    "time",
    "camera",
    "cameras",
    "question",
    "echo",
    "introduce",
    "what",
    "is",
    "it",
    "like",
    "outside",
    "whats",
    "the",
    "will",
    "rain",
    "today",
    "tomorrow",
    "clock",
    "show",
    "me",
    "front",
    "door",
    "who",
    "are",
    "you",
]
vocab = {word: number for number, word in enumerate(WORDS)}

tokenizer = Tokenizer(models.WordLevel(vocab, unk_token="[UNK]"))
tokenizer.normalizer = normalizers.Sequence(
    [normalizers.Lowercase(), normalizers.Replace("'", "")]
)
tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
tokenizer.save(str(target / "tokenizer.json"))

# One dimension per word; the padding and unknown tokens are the zero vector.
table = np.eye(len(WORDS), dtype=np.float32)
table[0] = 0.0
table[1] = 0.0

graph = helper.make_graph(
    [
        helper.make_node("Gather", ["table", "input_ids"], ["last_hidden_state"]),
    ],
    "tiny-embedding",
    [
        helper.make_tensor_value_info(
            "input_ids", TensorProto.INT64, ["batch", "tokens"]
        ),
        helper.make_tensor_value_info(
            "attention_mask", TensorProto.INT64, ["batch", "tokens"]
        ),
        helper.make_tensor_value_info(
            "token_type_ids", TensorProto.INT64, ["batch", "tokens"]
        ),
    ],
    [
        helper.make_tensor_value_info(
            "last_hidden_state", TensorProto.FLOAT, ["batch", "tokens", len(WORDS)]
        )
    ],
    [numpy_helper.from_array(table, "table")],
)
model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
model.ir_version = 8
onnx.checker.check_model(model)
onnx.save(model, str(target / "model_int8.onnx"))
print(sorted(p.name + " " + str(p.stat().st_size) for p in target.iterdir()))
