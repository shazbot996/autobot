# Agents Guide for autobot

This file orients the coding agents that work in autobot: what to read first, what to run before they report, and what they must never touch. autobot is a personal always-on voice assistant: a local wake-word listener and local speech-to-text and text-to-speech, a rules layer that validates queries before any AI call, and persistent Gemini ADK agents whose memory lives on the local machine. `AGENTS.md` is prescriptive: when an agent's behaviour and this file disagree, the agent is wrong. It carries no YAML frontmatter because agent tools read it as plain markdown.

## Context map (start here)

Read the context documents in `docs/context/` before searching the codebase. Each one covers one subject:

1. [BRIEF.md](docs/context/BRIEF.md): what autobot is, who it is for, and what it is not.
2. [GLOSSARY.md](docs/context/GLOSSARY.md): the project's vocabulary: wake phrase, rule, agent, session, memory, and the named tools.
3. [CONVENTIONS.md](docs/context/CONVENTIONS.md): coding, naming, layout, and documentation conventions.
4. [CONSTRAINTS.md](docs/context/CONSTRAINTS.md): the rules that must never break. Speech-to-text, wake-word detection and text-to-speech are processed locally, never by a cloud service. They override any plan or spec that disagrees.
5. [SCHEMA.md](docs/context/SCHEMA.md): the shared shapes: the configuration file, wake phrase configuration, the `WakeEvent`, rules, agent definitions, agent state.
6. [ARCH.md](docs/context/ARCH.md): the parts (listener, speech, rules, agent host, wall UI, local store, and the Make and terminal user interface control layers) and the seams between them.
7. [INFRASTRUCTURE.md](docs/context/INFRASTRUCTURE.md): the Intel NUC and the Google Cloud project.

`docs/decisions/` holds the decisions made so far. ADR 0001 and ADR 0003 still read `proposed` because a decision is never edited in place; ADR 0004 records their acceptance, and ADR 0005 records the wake-word engine. `docs/specs/active/` holds the specs for the wake-word listener, rules-first routing, and agent sessions; the last two are stubs. `docs/plans/active/` holds `headless-wake-word-prototype-PLAN.md`, the plan the code so far was built from.

The code is one Python package, `autobot`, under `src/`: the listener in `src/autobot/listener/`, the wake-word training in `src/autobot/wakeword/`, the terminal user interface in `src/autobot/tui/`, the configuration loader in `src/autobot/config.py`, the model fetcher in `src/autobot/fetch_models.py`. Tests are under `tests/`, configuration is `config/autobot.toml`, and the systemd unit template is in `deploy/`.

## What to run before reporting

Run all three checks, and report only when all three pass:

- `make test` runs the tests with `pytest`.
- `make lint` runs `ruff check` and `ruff format --check`.
- `make typecheck` runs `mypy` in strict mode over `src` and `tests`.

`make setup` creates `.venv` and installs the package with its dev extras; the three checks stop with a one-line message until it has been run.

The tests open no microphone, no speaker and no network connection. They do need the local model files under `models/`, which is not committed. `make fetch-models` downloads the published ones (Silero VAD, faster-whisper `tiny.en`, the acknowledgement's piper voice, openWakeWord's feature models); the `autobot` wake-word model, `models/wakeword/autobot.onnx`, is not published anywhere and is trained by `make train-wakeword`, which takes hours. When a model file is missing the run fails with a message naming it. That is a missing file to report, not a test to skip and not a reason to download or train anything from inside a test.

An agent also reads its changes back against the context documents above, and in particular checks them against `CONSTRAINTS.md`. A change to the listener's speech path keeps it offline: see "Local-only speech" in `CONVENTIONS.md`.

The first wake-word test is built, and its wake phrase is `autobot`. People rarely say the word, so it is the first test wake phrase. The WAV fixtures it runs from, in `tests/fixtures/audio/`, are placeholders synthesised with piper-tts, not yet recordings of a person on the NUC; `tests/fixtures/audio/README.md` says how to replace them.

## Runtime control and audio checks

autobot has two control layers below the application. An agent builds runtime, platform and hardware controls into these layers, and never into the main application:

- **Make.** The `Makefile` handles runtime control. A bare `make` prints a summary of the available commands. Servers are started and stopped with targets such as `make up` and `make down`, with separate targets for front-end and back-end controls. `make up`, `make down`, `make status` and `make logs` control the listener, through `python -m autobot.listener.control`; `backend-up` and `backend-down` are aliases, and no front-end targets exist while the build is headless. `make fetch-models` is the only target that downloads anything; `make train-wakeword` trains the wake-word model, runs for hours, and is resumable, so an agent starts it detached and does not wait for it.
- **Terminal user interface.** A 90s-style terminal user interface manages runtime, configuration and deployment. `make deploy` opens it. It handles the cloud deployments, the identity and access management checks, and the other platform work that the application depends on but does not handle itself. Some runtime configuration is also done from this interface. Today it has screens for audio, the wake phrase, the listener's systemd unit and the host; its cloud deployment and identity and access management entries are placeholders until the Google Cloud project exists. It needs a terminal, so an agent without one uses the `make` targets and `python -m autobot.listener.control` instead.

The application itself runs with a web interface that covers everything else. That interface is not built yet.

Build the checks for the local audio hardware on the NUC in one of these layers, as a plain `make` target or inside the terminal user interface, so that an agent can test and control the audio hardware itself. The audio hardware check target is `make audio-check`. It lists the audio devices, records two seconds from the microphone, prints the level, and plays the recording back. It runs on the NUC only: this machine's hostname must equal `host.nuc_hostname` in `config/autobot.toml`. On any other machine it fails with a clear message, because the NUC's local audio hardware is what is being checked. The listener and the installer of its systemd unit refuse in the same way. Each new target must appear in the bare `make` summary, which is built from the `## ` comment on the target's rule line.

## What not to touch

- Never read or write secrets: `.env`, `.env.*`, or any credential file. Google Cloud credentials for the Gemini project are secrets.
- Never introduce a cloud service, API or hosted model for speech-to-text, wake-word detection or text-to-speech. `CONSTRAINTS.md` `CN-1` and `CN-2` forbid it. A task that seems to need one stops and says so.
- Never move agent memory or agent state off the local machine. The intake makes local persistence a core purpose of the server; see `docs/decisions/0002-gemini-adk-agents-with-local-state-ADR.md`.
- Never write inside `.git/`, and never commit, push, or rewrite history unless a person asks.
- Never edit CR2's run records under `prompts/<author>/`. They are write-once history.
- Never edit an existing Architectural Decision Record in `docs/decisions/` in place. Record a change of mind as a new, higher-numbered decision.
- Never edit CR2's project configuration under `.cr2/` unless the task is about it.

## Open Questions

- On a fresh checkout `make test` cannot pass until the wake-word model has been trained, which takes hours. Should a trained `autobot.onnx` be kept somewhere a checkout can copy it from?
