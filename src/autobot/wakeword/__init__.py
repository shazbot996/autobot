"""Building wake-word models: the training side of the `openwakeword` kind.

`python -m autobot.wakeword.train` (`make train-wakeword`) trains the small
model the `openwakeword` engine runs for one wake phrase. Everything it does
is local (CONSTRAINTS.md CN-1): the clips are synthesised with piper-tts, the
features come from openWakeWord's two ONNX feature models, and the classifier
is trained on the CPU. The data sets and voices it needs are fetched
beforehand by `make fetch-models TRAIN=1`; the training itself never opens a
network connection.

- `data`: the voices, carrier phrases and hard negatives the clips are made from.
- `features`: openWakeWord features for whole clips, with `onnxruntime` only.
- `train`: the pipeline and its command line.
"""
