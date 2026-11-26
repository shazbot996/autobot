---
type: changelog
title: "Changelog — autobot"
authority: historical
status: active
---

# Changelog — autobot

This file records what changed in autobot between releases, for the people who use it. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

`CHANGELOG.md` is a **historical** Root document. It records what happened, and a released entry is never rewritten afterwards. For that reason CR2 does not offer `Revise` or `Answer questions` on this square. The log grows instead. New work is added under **Unreleased** as it lands. When you cut a release, those entries move under a new version heading, with the newest version first, grouped as Added, Changed, Deprecated, Removed, Fixed, and Security. A coding agent can add these entries during a build run, for example through `Direct`. You can also add them by hand.

Unlike the other starter documents, the entry below is not tutorial text to sweep away. It is the true first record of this project: the moment it was seeded.

## Unreleased

### Added

- Smart Start intake (2026-11-25) replaced the `cr2-empty` tutorial with autobot's first real context. Rung 0 rewrote `README.md`, `AGENTS.md`, and the Core documents `BRIEF.md`, `GLOSSARY.md`, `CONVENTIONS.md`, `CONSTRAINTS.md` (`CN-1`, `CN-2`: local-only speech-to-text, wake-word modeling and text-to-speech), `SCHEMA.md` and `ARCH.md`. Rung 1 added `docs/context/INFRASTRUCTURE.md` (Intel NUC, WSL test machine, `cambiumplatform.com` Google Cloud organization). Rung 2 added ADR 0001 (Python, proposed), ADR 0002 (Gemini ADK with locally persisted state, active) and ADR 0003 (faster-whisper and piper-tts, proposed). Rung 3 added stub specs `wake-word-listener-SPEC.md`, `rules-first-routing-SPEC.md` and `agent-sessions-SPEC.md`.
- Headless wake-word prototype (2026-11-25), built from `docs/plans/active/headless-wake-word-prototype-PLAN.md`. An always-on listener on the Intel NUC hears any utterance containing the wake phrase `autobot` and answers aloud, "Yes?". It reads the microphone at 16 kHz through PipeWire, drops silence with Silero VAD, and runs one detector per configured wake phrase behind a `Detector` protocol with two kinds: `openwakeword`, a model trained here for `autobot` by `make train-wakeword`, which is the configured kind, and `text_match`, which transcribes speech with faster-whisper and looks for the phrase. Each wake is published as a `WakeEvent` JSON line to `run/listener.log` and to the Unix socket `run/listener.sock`. The acknowledgement is synthesised locally with piper-tts. No audio leaves the machine and the listener makes no network calls.
- `Makefile` runtime control layer: a bare `make` prints every target; `setup`, `test`, `lint`, `typecheck`, `up`, `down`, `status`, `logs`, `audio-check`, `deploy`, `fetch-models` and `train-wakeword`, with `backend-up` and `backend-down` as aliases.
- The `openwakeword` engine (`src/autobot/listener/engines/openwakeword.py`). It runs openWakeWord's two feature models and the trained `autobot` model with `onnxruntime` alone, one thread each, about 1.5 ms per 80 ms frame; the `openwakeword` Python package is not a dependency, because it needs `tflite-runtime`, which has no wheel for Python 3.12. It fires when the score reaches the phrase's `threshold` and then ignores two seconds of audio. It is fed stretches of speech only, through a speech gate with wider margins than `text_match` uses (0.96 s before the speech, 0.8 s after), which a detector now asks for with a `gate_margins` attribute. In the same room over the same minutes the listener used 8.8 % of one core and 176 MiB with `openwakeword`, against 120.8 % and 341 MiB with `text_match`; in silence both use 4.7 %.
- `make train-wakeword` (`src/autobot/wakeword/`) trains the wake-word model on the NUC's CPU: speech synthesised with piper-tts in 20 voices (the phrase in carrier sentences, words that sound like it, ordinary speech), reverb and noise, openWakeWord features and its general negative features, a small classifier, and an evaluation on 7 voices kept out of training. It is resumable at every stage, and writes `models/wakeword/autobot.onnx` and `autobot.json` atomically, keeping the model it replaces. The first model, from a short run, hears 74 % of held-out `autobot` clips at threshold 0.9 and makes 3.9 false accepts per hour on openWakeWord's validation audio, which is not yet good enough for a house; fed through the listener's own frame path it detected 127 of 140 held-out utterances (90.7 %) and fired on 3 of 140 other sentences. A run with ten times the synthesised speech was started on 2026-11-25 to replace it.
- `make fetch-models` (`src/autobot/fetch_models.py`) downloads the published model files into `models/`, and with `TRAIN=1` the voices and data sets the training needs. It is the only code that downloads; the listener, the tests and the training do not.
- Terminal user interface, opened by `make deploy`: screens for audio devices and levels, the wake phrase, the listener's systemd unit and the host, with placeholders for cloud deployment and identity and access management.
- `config/autobot.toml`, the one configuration file, and `deploy/autobot-listener.service`, the systemd user unit that runs the listener from boot.
- `make audio-check`, which checks the NUC's microphone and speaker and refuses to run on any other machine.
- Tests (`pytest`), linting (`ruff`) and strict type checking (`mypy`), run from recorded audio fixtures with no microphone, speaker or network. The fixture tests pass for both detector kinds. The fixtures are placeholders synthesised with piper-tts until recordings are made on the NUC.
- ADR 0004, accepting Python (ADR 0001) and faster-whisper and piper-tts (ADR 0003) and naming `mypy` as the type checker, and ADR 0005, recording openWakeWord with a custom-trained `autobot` model as the wake-word engine, with `text_match` as the fallback kind.

### Changed

- `wake-word-listener-SPEC.md` gained rules WW-5 to WW-9: `autobot` as the first test wake phrase, detection by containment, a wake phrase's kind as the detector that implements it, the spoken acknowledgement, and running on the NUC only.
- `SCHEMA.md`, `ARCH.md`, `CONVENTIONS.md`, `GLOSSARY.md`, `INFRASTRUCTURE.md`, `AGENTS.md` and `README.md` now describe the built listener and control layers: the configuration file and `WakeEvent` shapes, the `run/listener.sock` seam, the Python conventions in force, the NUC's hostname and audio hardware, and the checks agents run (`make test`, `make lint`, `make typecheck`).
- The context documents, `README.md` and `AGENTS.md` no longer describe the WSL layer on the Windows dev server as a test machine; support is limited to the NUC.

### Security

- The listener disables `onnxruntime`'s usage telemetry (`ORT_DISABLE_TELEMETRY=1`, set before the library is imported, and in the systemd unit), which would otherwise make network calls from the speech path.

## 0.1.0 - Seeded Workspace

### Added

- Project workspace initialized from the CR2 `cr2-empty` template.
- The canonical CR2 folder hierarchy: `docs/context/`, `docs/decisions/`, `docs/specs/`, `docs/plans/`, `docs/remediations/`, `docs/publish/`, `docs/attachments/`, `docs/images/`, and `.cr2/`.
- Starter tutorial Root documents (`README.md`, `CHANGELOG.md`, `AGENTS.md`) and Core context documents (`BRIEF.md`, `GLOSSARY.md`, `CONVENTIONS.md`, `CONSTRAINTS.md`, `SCHEMA.md`, `ARCH.md`) under `docs/context/`.
