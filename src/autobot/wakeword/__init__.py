"""Building wake-word models: the training side of the `openwakeword` kind.

`python -m autobot.wakeword.train` (`make train-wakeword`) trains the small
model the `openwakeword` engine runs for one wake phrase, on the owner's
recordings in the voice dataset with synthetic speech beside them, and writes
it as a candidate. The interface compares the candidate with the installed
model on the held-out recordings and installs it when the owner confirms.
Everything is local (CONSTRAINTS.md CN-1): the recordings never leave the
machine, the synthetic clips are made with piper-tts, the features come from
openWakeWord's two ONNX feature models, and the classifier is trained on the
CPU. The data sets and voices it needs are fetched beforehand by `make
fetch-models TRAIN=1`; the training itself never opens a network connection.

- `data`: the voices, carrier phrases and hard negatives the clips are made from.
- `dataset`: the voice dataset (`data/voice/`), its manifest and its split.
- `script`: the prompts of a recording session.
- `features`: openWakeWord features for whole clips, with `onnxruntime` only.
- `train`: the pipeline and its command line; it writes a candidate.
- `evaluate`: a model's figures on the held-out real recordings, and the
  comparison of a candidate with the installed model. Runtime libraries only.
- `install`: install a candidate, keep the previous model, revert.
"""
