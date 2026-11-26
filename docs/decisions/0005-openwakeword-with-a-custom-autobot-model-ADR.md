---
type: decision
title: "autobot — ADR 0005: openWakeWord with a custom-trained autobot model"
authority: historical
status: active
---

# autobot — ADR 0005: openWakeWord with a custom-trained autobot model

This records the wake-word engine decision that ADR 0003 left for a later ADR. It was made in the headless wake-word prototype plan (`docs/plans/active/headless-wake-word-prototype-PLAN.md`, section 9), which the owner directed to be executed in full.

## Context

The intake rules out cloud services for wake-word detection (`CN-1`) but names no engine. The owner did not know which to use and asked for help choosing. Three things were known: the owner wants to build the engine here rather than take a finished one off a shelf; it needs a machine-learning model, as the one the owner built before for a Coral Dev Board did; and it must be cheap enough to run all day on the NUC, which has a 12-thread Intel CPU and no GPU. The spec adds that new kinds of wake phrases must be configurable, not only new phrases of one kind (WW-3), and the first test wake phrase is `autobot`, which no ready-made model covers.

## Decision

The wake-word engine is openWakeWord, running a small model trained here for the phrase `autobot`.

- **openWakeWord** supplies the shared feature extractor (a mel-spectrogram model and a speech-embedding model, both ONNX, both local files) and the method: a small classifier trained on top of those embeddings, from synthetic speech.
- **The models are run with `onnxruntime` directly.** The `openwakeword` Python package is not a dependency: it requires `tflite-runtime`, which has no wheel for Python 3.12. The listener repeats the package's streaming feature code, about forty lines, and the training repeats its clip features; both were checked against the package and give the same numbers.
- **The `autobot` model is trained on the NUC** by `make train-wakeword`. Clips of the phrase, of words that sound like it and of ordinary speech are synthesised locally with piper-tts in varied voices, given reverb and noise, and turned into openWakeWord features; the classifier is trained against them and openWakeWord's published general negative features, and evaluated on voices kept out of training. The result is written to `models/wakeword/autobot.onnx`, with its evaluation in `autobot.json`.
- **The detector is fed speech, not silence, with wider margins than `text_match`.** The model scores highest in the half second after the phrase ends and wants to have heard what came before it. Fed only the default 0.24 s before and 0.48 s after each stretch of speech, it detected 82.9 % of 140 held-out utterances at threshold 0.9, against 90.7 % when it scored every frame. With 0.96 s before and 0.8 s after it detects the same 90.7 % and is still idle in silence, so that is what it asks the speech gate for.
- **`text_match` is the bootstrap and fallback kind.** It transcribes each stretch of speech with faster-whisper and fires when the transcript contains the phrase. It proved the whole pipeline, microphone in and acknowledgement out, before any model was trained, and it stays available.
- **A wake phrase "kind" is the detector that implements it.** Both kinds sit behind one `Detector` protocol and are selected per phrase by the `kind` field of its `[[wake_phrases]]` entry in `config/autobot.toml`. A new kind is a new module under `src/autobot/listener/engines/`.
- **Detection is by containment for both kinds.** The phrase is recognised wherever it falls in an utterance, so "hey autobot" and "yo autobot" need no configuration of their own.

## Alternatives

- **A classifier built from scratch**, with its own features and training pipeline. Rejected: the owner wants to build the engine, but training a model for a new phrase on openWakeWord's feature extractor is that work without the part that is already solved.
- **A ready-made keyword engine with fixed or vendor-trained phrases** (Porcupine and the like). Rejected: custom phrases need the vendor's service or licence, which conflicts with `CN-1` and with the owner keeping control.
- **`text_match` alone**, transcribing everything the room says. Rejected as the 24x7 engine: every stretch of speech costs a transcription. It is kept as the fallback.
- **A model for an accelerator** such as the Coral Dev Board's tensor processing unit. Not chosen: the NUC has no such hardware, and a model this small does not need it.

## Consequences

- Wake-word detection, its training and its feature models all run on the NUC, with no network calls (`CN-1`). The listener and the training set `ORT_DISABLE_TELEMETRY=1` before `onnxruntime` is imported, because the runtime otherwise uploads usage telemetry. The published files they need (the feature models, the piper voices, openWakeWord's negative and validation features, impulse responses and noise clips) are downloaded once by a separate command, `make fetch-models`, which sends no audio and nothing derived from audio.
- Two detector kinds exist behind one protocol, which satisfies WW-3 and gives the listener a second engine to fall back on.
- Each new wake phrase of the `openwakeword` kind needs its own trained model file, and should be given its own list of words that sound like it (`src/autobot/wakeword/data.py`). Training runs on the NUC's CPU and takes about five hours at the default size; it is resumable.
- The detector is cheap: about 1.5 ms of CPU per 80 ms frame it is fed, and nothing in silence. In the same room over the same minutes the listener used 8.8 % of one core with `openwakeword` and 120.8 % with `text_match`.
- The first model, from a short run (23,000 synthesised clips, 10,000 steps), is not good enough to leave on in a house: on held-out voices it hears 74 % of `autobot` clips at threshold 0.9 with 3.9 false accepts per hour on openWakeWord's validation audio, and 69 % with 2.2 per hour at 0.95. It fires most on "a lot of bots", "water bottle", "odd about", "auto box", "auto bar" and "autobus". A run with ten times the clips, those phrases added to its hard negatives and four times the general negatives was started on 2026-11-25 to replace it; `models/wakeword/autobot.json` holds the evaluation of whichever model is installed.
- `SCHEMA.md` gains the `kind`, `model` and `threshold` fields of a wake phrase and the `WakeEvent` shape; `GLOSSARY.md` gains entries for openWakeWord, Silero VAD and `WakeEvent`.
- The trained model is not committed: `models/` is ignored by git, so a fresh checkout has to train or be given the model before the `openwakeword` kind can run.

## Open Questions

- How well does a model trained only on synthetic speech hear the owner's own voice in the room, and what `threshold` balances missed wakes against false ones? The evaluation so far is on synthetic voices and openWakeWord's validation audio; only use on the NUC answers this. The configured threshold is 0.9.
- Does the larger training run bring false accepts under the target of one every two hours at a usable recall? If not, the next steps are real recordings of the owner saying the phrase, and speech and music as background in training.
- How are further wake phrases trained: one model per phrase, as now, or one model for several?
- Should the trained model, or the recipe and seed that reproduce it, be kept somewhere other than the git-ignored `models/` directory?
