# Agents Guide for autobot

This file orients the coding agents that work in autobot: what to read first, what to run before they report, and what they must never touch. autobot is a personal always-on voice assistant: a local wake-word listener and local speech-to-text and text-to-speech, a rules layer that validates queries before any AI call, and persistent Gemini ADK agents whose memory lives on the local machine. `AGENTS.md` is prescriptive: when an agent's behaviour and this file disagree, the agent is wrong. It carries no YAML frontmatter because agent tools read it as plain markdown.

## Context map (start here)

Read the context documents in `docs/context/` before searching the codebase. Each one covers one subject:

1. [BRIEF.md](docs/context/BRIEF.md): what autobot is, who it is for, and what it is not.
2. [GLOSSARY.md](docs/context/GLOSSARY.md): the project's vocabulary: wake phrase, rule, agent, session, memory, and the named tools.
3. [CONVENTIONS.md](docs/context/CONVENTIONS.md): coding, naming, layout, and documentation conventions.
4. [CONSTRAINTS.md](docs/context/CONSTRAINTS.md): the rules that must never break. Speech-to-text, wake-word detection and text-to-speech are processed locally, never by a cloud service. They override any plan or spec that disagrees.
5. [SCHEMA.md](docs/context/SCHEMA.md): the shared shapes: the configuration file, wake phrase configuration, the `WakeEvent` and `ScoreEvent`, the voice dataset's manifest line, the model evaluation file, rules, agent definitions, agent state.
6. [ARCH.md](docs/context/ARCH.md): the parts (listener, wake-word training and the voice dataset, speech, rules, agent host, the terminal user interface that is the application, local store, and the Make runtime control layer) and the seams between them.
7. [INFRASTRUCTURE.md](docs/context/INFRASTRUCTURE.md): the Intel NUC and the Google Cloud project.

`docs/decisions/` holds the decisions made so far. ADR 0001 and ADR 0003 still read `proposed` because a decision is never edited in place; ADR 0004 records their acceptance, ADR 0005 records the wake-word engine, and ADR 0006 records that the full-screen terminal user interface is the application and that the wake-word model is trained on the owner's voice first, superseding parts of ADR 0005. `docs/specs/active/` holds the specs for the wake-word listener, the wake-word training studio, rules-first routing, and agent sessions; the last two are stubs. `docs/plans/active/` holds `headless-wake-word-prototype-PLAN.md`, the plan the listener was built from, and `wake-word-training-studio-PLAN.md`, the plan the studio was built from, which supersedes steps 6 and 7 of the first.

The code is one Python package, `autobot`, under `src/`: the listener in `src/autobot/listener/`, the wake-word training, the voice dataset, the evaluation and the install in `src/autobot/wakeword/`, the terminal user interface, which is the application, in `src/autobot/tui/`, the configuration loader in `src/autobot/config.py`, the model fetcher in `src/autobot/fetch_models.py`. Tests are under `tests/`, configuration is `config/autobot.toml`, and the systemd unit template is in `deploy/`.

## What to run before reporting

Run all three checks, and report only when all three pass:

- `make test` runs the tests with `pytest`.
- `make lint` runs `ruff check` and `ruff format --check`.
- `make typecheck` runs `mypy` in strict mode over `src` and `tests`.

`make setup` creates `.venv` and installs the package with its dev extras; the three checks stop with a one-line message until it has been run.

The tests open no microphone, no speaker and no network connection. They do need the local model files under `models/`, which is not committed. `make fetch-models` downloads the published ones (Silero VAD, faster-whisper `tiny.en`, the acknowledgement's piper voice `en_US-joe-medium`, openWakeWord's feature models, the sherpa-onnx keyword-spotting model of the `sherpa_kws` kind); the `autobot` wake-word model, `models/wakeword/autobot.onnx`, is not published anywhere and comes from `make train-wakeword`, which takes hours and writes a candidate that only the interface installs. When a model file is missing the run fails with a message naming it. That is a missing file to report, not a test to skip and not a reason to download or train anything from inside a test.

An agent also reads its changes back against the context documents above, and in particular checks them against `CONSTRAINTS.md`. A change to the listener's speech path keeps it offline: see "Local-only speech" in `CONVENTIONS.md`.

The first wake-word test is built, and its wake phrase is `autobot`. People rarely say the word, so it is the first test wake phrase. The WAV fixtures it runs from, in `tests/fixtures/audio/`, are placeholders synthesised with piper-tts, not yet recordings of a person on the NUC; `tests/fixtures/audio/README.md` says how to replace them: by exporting one chosen clip at a time from the Dataset screen of the interface, never as a side effect of recording.

The tests never read or write the owner's recordings. They build their own small voice dataset from the committed fixtures, in a temporary directory.

## Runtime control and audio checks

The terminal user interface is the application, and the `Makefile` is the runtime control layer beside it. No web interface, PWA or wall UI is built (ADR 0006). An agent builds runtime, platform and hardware controls as a `make` target or as a screen of the terminal user interface, and nowhere else:

- **Make.** The `Makefile` handles runtime control. A bare `make` prints a summary of the available commands. `make up`, `make down`, `make status` and `make logs` control the listener, through `python -m autobot.listener.control`; `backend-up` and `backend-down` are aliases. The only front-end target is `make deploy`, which opens the application. `make fetch-models` is the only target that downloads anything. `make train-wakeword` trains a wake-word candidate (`models/wakeword/candidate.onnx` and `candidate.json`), runs for hours the first time, and is resumable, so an agent starts it detached and does not wait for it. It installs nothing: it touches neither the installed model nor `config/autobot.toml`.
- **Terminal user interface.** A full-screen `curses` interface in the manner of a mid-90s bulletin board, with three colour themes; `make deploy` opens it. Its first job is the wake-word training studio, and its main screen always shows where the listener stands: *untrained*, *recording*, *candidate ready*, *installed* or *listener down*. The studio screens are Recording session, Train the model, Candidate (compare, install, revert), Live monitor and Dataset; the settings screens are Audio, Wake phrase, Service and Host. It is a front: it holds no configuration of its own, writes every setting to `config/autobot.toml`, reaches the listener only through `python -m autobot.listener.control` as a subprocess and the event stream on `run/listener.sock`, and keeps its logic in modules that do no drawing, which is where its tests run. Later jobs of the application are further screens of this interface. It needs a terminal, so an agent without one uses the `make` targets and `python -m autobot.listener.control` instead, or runs it in a pseudo-terminal.

An agent that tries the interface out points it away from the real directories with three environment variables, read only by `python -m autobot.tui`: `AUTOBOT_VOICE_DIR` replaces `data/voice/`, `AUTOBOT_FIXTURE_DIR` replaces `tests/fixtures/audio/`, and `AUTOBOT_MODELS_ROOT` replaces the repository root as the directory the model files are looked for under. `AUTOBOT_CONFIG` loads another configuration file. The listener is not told about the first three.

Build the checks for the local audio hardware on the NUC as a plain `make` target or inside the terminal user interface, so that an agent can test and control the audio hardware itself. The audio hardware check target is `make audio-check`. It lists the audio devices, records two seconds from the microphone, prints the level, and plays the recording back. It runs on the NUC only: this machine's hostname must equal `host.nuc_hostname` in `config/autobot.toml`. On any other machine it fails with a clear message, because the NUC's local audio hardware is what is being checked. The listener, the installer of its systemd unit and, in the interface, the recording session, the live monitor and the install of a model refuse in the same way. Each new target must appear in the bare `make` summary, which is built from the `## ` comment on the target's rule line.

## What not to touch

- Never read or write secrets: `.env`, `.env.*`, or any credential file. Google Cloud credentials for the Gemini project are secrets.
- Never introduce a cloud service, API or hosted model for speech-to-text, wake-word detection or text-to-speech. `CONSTRAINTS.md` `CN-1` and `CN-2` forbid it. A task that seems to need one stops and says so.
- Never commit, copy off the NUC, upload or send anywhere the owner's recordings in `data/voice/`, or anything computed from them: they are never committed and never leave the NUC (TS-5). An agent that needs a voice dataset works against a scratch directory (`AUTOBOT_VOICE_DIR`, or `--dataset` for the training), not the owner's.
- Never move agent memory or agent state off the local machine. The intake makes local persistence a core purpose of the server; see `docs/decisions/0002-gemini-adk-agents-with-local-state-ADR.md`.
- Never write inside `.git/`, and never commit, push, or rewrite history unless a person asks.
- Never edit CR2's run records under `prompts/<author>/`. They are write-once history.
- Never edit an existing Architectural Decision Record in `docs/decisions/` in place. Record a change of mind as a new, higher-numbered decision.
- Never edit CR2's project configuration under `.cr2/` unless the task is about it.

## Open Questions

- On a fresh checkout `make test` cannot pass until the wake-word model has been trained, which takes hours. Should a trained `autobot.onnx` be kept somewhere a checkout can copy it from?
