# Embedding fixture

A tiny stand-in for the sentence-embedding model of the routing cascade's
second tier (`autobot.embedding`, IS-17), so that the tests can load a model
and a tokenizer from a local path without the real ones and without the
network.

| File | What it is |
|---|---|
| `model_int8.onnx` | An ONNX model with the inputs of `all-MiniLM-L6-v2` (`input_ids`, `attention_mask`, `token_type_ids`) and its output (`last_hidden_state`). It is one lookup table: each word of a small vocabulary has a dimension of its own. About 4 kB. |
| `tokenizer.json` | A word-level tokenizer for that vocabulary, lower-casing, in the `tokenizers` package's own format. |
| `build.py` | The script that made both. |

Two texts are similar here by the words they share, which is all the tests
need: `tests/test_embedding.py` checks that the files load with every socket
refused, that padding does not change a vector, and that a missing file is
named.

**It is not the real model.** The real one, about 23 MB, is fetched by
`make fetch-models` into `models/embedding/all-MiniLM-L6-v2/` and is never
committed. No test needs it.

To rebuild the fixture, with the `train` extra installed (it brings `onnx`):

    .venv/bin/python tests/fixtures/embedding/build.py tests/fixtures/embedding

The output is the same every time.
