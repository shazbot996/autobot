---
type: readme
title: "autobot"
authority: descriptive
status: active
provenance:
  - run: schiele/0015
    at: 2026-11-25T22:58:12.732Z
    mode: revise
---

# autobot

## What it is, in a paragraph a stranger can follow

autobot is a personal, always-on voice assistant for one house. A local process listens to a microphone around the clock for configurable wake phrases, such as a home-made "hey google". When it hears one, it transcribes what follows with a local speech-to-text model, runs the words through a set of plain rules first, and only then, when a rule says so, opens an interactive session with one of several persistent AI agents built on Gemini ADK and served from Google Cloud in the `us-east5` region. Each agent is reached by its own wake phrase and keeps its memory on the local machine, so that, for example, a list-keeping agent remembers its lists between conversations. The assistant answers out loud with a local text-to-speech voice that the owner can choose, and shows what it is doing on a screen on the wall, operated by voice rather than keyboard and mouse. Speech recognition, wake-word detection and speech synthesis never leave the machine; only the agent reasoning goes to the cloud. It is a platform for voice-first applications, and no application exists yet: the framework comes first. That framework is the local process that listens, responds and catches wake words, and captures commands through a rules-based engine that leads into agent activation, in the manner of Google's Dialogflow. Simple question-and-answer use comes first once the framework works.

## Running it

What runs today is the first build: a headless wake-word listener on the Intel NUC. It listens to the microphone around the clock for any utterance containing the wake phrase `autobot` ("hey autobot", "yo autobot") and answers aloud, "Yes?". Nothing after the wake phrase is captured yet, and the rules layer, the agents and the wall UI are not built. It runs on the NUC only, on Ubuntu with PipeWire, and needs Python 3.12 or later.

From a checkout on the NUC:

1. `make setup` creates `.venv` and installs the package with its dev extras.
2. `make fetch-models` downloads the model files into `models/`, which is not committed: the Silero VAD model, the faster-whisper `tiny.en` model, the piper voice for the acknowledgement, and openWakeWord's feature models. It is the only command that downloads anything; the listener and the tests never do, and name the file they miss.
3. `make deploy` opens the terminal user interface. Its Host screen names this machine as the NUC, its Audio screen picks the microphone and speaker and shows a level meter, its Wake phrase screen sets the threshold and the detector kind and starts training, and its Service screen installs and enables the systemd unit so the listener starts at boot.
4. `make audio-check` lists the audio devices, records two seconds from the microphone, prints the level and plays the recording back. It refuses to run on any machine but the NUC.
5. `make train-wakeword` trains the `autobot` wake-word model into `models/wakeword/autobot.onnx`, which the listener needs before it can start with the configured `openwakeword` kind. It runs on the NUC's CPU for about five hours, prints its progress, and is resumable: run it again and it carries on from the clips, features and checkpoint it has cached under `models/wakeword/work/`. The first run installs the training libraries into `.venv` (the CPU build of `torch`) and fetches the training voices and data sets, about 15 GB; after that it downloads nothing. `SCALE` (how much speech to synthesise), `STEPS`, `THREADS` and `ACAV_GB` (how much of openWakeWord's negative data to use) change the run, for example `make train-wakeword SCALE=2 STEPS=10000` for a quick, weaker model. When it finishes it prints the threshold its evaluation recommends, to set in `config/autobot.toml` or on the Wake phrase screen; the evaluation is kept in `models/wakeword/autobot.json` and the model it replaced as `autobot.prev.onnx`. Until a model exists, set `kind = "text_match"` to run the listener on the transcribing detector instead.
6. `make up` starts the listener, `make down` stops it, `make status` shows whether it runs with its uptime, CPU and memory, and `make logs` prints the end of its log (`make logs FOLLOW=1` keeps following it).

A bare `make` prints every target. `make test`, `make lint` and `make typecheck` run the checks (`pytest`, `ruff`, `mypy`); the tests use recorded audio fixtures and open no microphone, speaker or network connection.

Everything is configured in one file, `config/autobot.toml`; the terminal user interface is a front for it. Each wake the listener hears is written as one JSON line to `run/listener.log` and to the Unix socket `run/listener.sock`, which is where the next part to be built will pick it up.

The main user interface is still to come: a progressive web application served locally and shown full-screen in Chrome on the NUC itself.

## Where things are

- `README.md`, `CHANGELOG.md`, `AGENTS.md`: the Root row. What the project is, what has changed, and how coding agents must work here.
- `docs/context/`: the Core row. `BRIEF.md` (what autobot is and is not), `GLOSSARY.md` (its vocabulary), `CONVENTIONS.md` (house rules for the code), `CONSTRAINTS.md` (the rules that must never break: local-only speech processing), `SCHEMA.md` (shared data shapes), `ARCH.md` (the parts and the seams) and `INFRASTRUCTURE.md` (the NUC and the Google Cloud project).
- `docs/decisions/`: Architectural Decision Records. `0001` proposes Python, `0002` records Gemini ADK with locally kept state, `0003` proposes faster-whisper and piper-tts as the local speech stack, `0004` accepts `0001` and `0003` and names `mypy` as the type checker, and `0005` records openWakeWord with a custom-trained `autobot` model as the wake-word engine.
- `docs/specs/active/`: the behaviour each feature must have. Specs exist for the wake-word listener, and stubs for rules-first routing and persistent agent sessions.
- `docs/plans/active/` and `docs/plans/archive/`: implementation plans. `headless-wake-word-prototype-PLAN.md` is the plan the code so far was built from.
- `src/autobot/`: the Python package. `listener/` is the always-on listener (audio, the speech gate, the detectors under `engines/`, events, the acknowledgement, runtime control); `wakeword/` trains the wake-word model; `tui/` is the terminal user interface; `config.py` loads the configuration; `fetch_models.py` is `make fetch-models`; `audio_check.py` is `make audio-check`.
- `tests/`: the tests, mirroring the package, with the audio fixtures in `tests/fixtures/audio/`.
- `config/autobot.toml`: the one configuration file.
- `deploy/autobot-listener.service`: the template of the systemd user unit.
- `Makefile`, `pyproject.toml`: the runtime control layer and the project definition.
- `models/` and `run/`: model files, with the training's data and caches under `models/wakeword/`, and runtime state (pid file, log, socket). Neither is committed.
- `docs/remediations/`, `docs/publish/`, `docs/attachments/`, `docs/images/`: security remediations, external publications, and attached files. Empty.
- `.cr2/`: CR2's own project configuration.

## Open Questions

- The trained `autobot` wake-word model is not committed and takes hours to train. Should it be kept somewhere a fresh checkout can copy it from?
- Which Google Cloud project id and region will host the Gemini agents? Q4 says a project will be created in the `cambiumplatform.com` organization but gives no id.
  The region is `us-east5`. No project exists yet; an id will be made up and the project created to match it.
