---
type: conventions
title: "autobot — Conventions"
authority: prescriptive
status: active
---

# autobot — Conventions

Conventions are the house rules for how autobot is written: the choices that are reasonable either way but must be made one way, so that the codebase reads as if one person wrote it. The intake asked for a stack recommendation rather than naming one; `docs/decisions/0001-python-for-the-listener-and-agent-host-ADR.md` proposed Python, and `docs/decisions/0004-accept-python-and-the-local-speech-stack-ADR.md` records its acceptance. The code conventions below are the mainstream Python defaults and are in force. A rule that must never be broken belongs in `CONSTRAINTS.md`, not here.

## One section per area, each convention with its reason

### Language and formatting

- Code is Python 3.12 or later. Reason: one language for the listener, the rules layer, the speech bindings and the ADK agent host keeps the always-on process simple to run on the NUC.
- Formatting and linting use `ruff`, with its default formatter settings, applied to the whole tree except `docs/`, `prompts/` and `.cr2/`. `make lint` runs both checks. Reason: one tool for both jobs, no style arguments, and a check an agent can run; CR2's documents and run records are not code and are never reformatted.
- Public functions and module boundaries carry type hints and the tree passes `mypy` in strict mode, over `src` and `tests`. `make typecheck` runs it. Reason: the listener, rules and agent host hand data to each other across process and module boundaries, and types make those hand-offs checkable before the microphone is involved. `mypy` is the mainstream checker that installs with the other dev extras (ADR 0004).
- A failure the owner can cause (a bad configuration, a missing model file, the wrong host, an audio device that will not open) is a named exception whose message is one line, and a command-line entry point prints that line and exits non-zero. Reason: these commands are run from `make` and from a service log, where a traceback hides the one fact that matters.

### Layout

- The project is a single Python package, `autobot`, under `src/`, with `pyproject.toml` at the repository root as the only project definition. Reason: the `src` layout prevents accidental imports from the working directory, and one definition file is the mainstream default.
- Tests live under `tests/` mirroring the package layout. Reason: this is where `pytest` discovers them by default and where readers expect them.
- Configuration lives in one file, `config/autobot.toml`, read with the standard library's `tomllib`. Nothing else stores configuration; a tool that changes a setting writes it back to that file. Reason: the project already uses TOML for `pyproject.toml`, reading it needs no dependency, and a second store would drift from the first.
- Model files live under `models/` and runtime state (the pid file, the log, the socket, a requested snapshot) under `run/`. Neither is committed. Reason: models are large binaries that are fetched or trained, not written, and runtime state belongs to one machine.
- The voice dataset, the owner's recordings and their manifest `manifest.jsonl`, lives under `data/voice/`. The whole of `data/` is git-ignored: the recordings are never committed and never leave the NUC. Reason: they are the owner's voice in his own house, to which the reasoning of `CN-1` applies as it does to the live stream, and they are neither fetched nor trained, so they do not belong under `models/` (TS-4, TS-5).
- A committed audio fixture under `tests/fixtures/audio/` is written only by the deliberate export of one chosen clip from the Dataset screen. Nothing writes a fixture as a side effect of recording. Reason: a fixture is committed and therefore public, so what goes into it is chosen clip by clip (TS-5).

### Naming

- Modules, functions and variables are `snake_case`; classes are `CapWords`; constants are `UPPER_SNAKE_CASE`, as PEP 8 prescribes. Reason: the standard library and every chosen dependency follow PEP 8, so anything else reads as foreign.
- Project terms are spelled as `GLOSSARY.md` spells them: `wake_phrase`, `rule`, `agent`, `session`, `memory`. Reason: the glossary is the single vocabulary and the code should be searchable by it.
- A wake-word engine module is named after its kind: the kind `text_match` is `src/autobot/listener/engines/text_match.py`, and `openwakeword` and `sherpa_kws` are named the same way. Reason: the listener finds an engine by importing the module of that name, so the name is the registration.

### Testing

- The test runner is `pytest`, run by `make test`. Reason: it is the mainstream Python default and what agents run before reporting.
- Tests never require a microphone, a speaker or a network connection; audio and cloud boundaries are exercised through recorded fixtures and fakes. Reason: tests must pass in sandboxes without audio hardware, and a test that reached a cloud speech service would itself violate `CONSTRAINTS.md`.
- Tests of the listener drive the detectors, of every kind, through the same frame path the service uses, from the WAV fixtures in `tests/fixtures/audio/`, which are committed. Reason: a test that takes a shortcut around the speech gate proves nothing about what the microphone will do.
- Tests never download a model and never train one. They look model files up under `models/`, the trained wake-word model among them, and fail with a message naming the missing file. Reason: a silent download is a network call in the speech path, which `CN-1` and `CN-2` forbid, and a skipped test would hide that the listener cannot run.

### Local-only speech

- Any module that imports a speech library (`onnxruntime`, `faster_whisper`, `piper` or `sherpa_onnx`) calls `autobot.listener.models.enforce_offline()` before the import, and the import sits inside the function that needs it. Reason: `onnxruntime` uploads usage telemetry from a native thread a few seconds after it is imported unless `ORT_DISABLE_TELEMETRY=1` is already set, and the Hugging Face code that faster-whisper pulls in looks things up online unless it is told not to. Either would break `CN-1`, and neither can be switched off after the import.
- A model is loaded from a local file path, never by a name that a library could resolve by downloading. Reason: the same constraints; a missing file must be an error, not a fetch.
- One module downloads: `autobot.fetch_models`, run by `make fetch-models`. It fetches public model and data files into `models/` and sends nothing else. The listener, the tests and the training (`autobot.wakeword.train`) never call it and never open a network connection; each stops with one line naming the file it misses. Reason: keeping every download in one command a person runs makes the rest of the code checkable against `CN-1` and `CN-2` by reading it.
- openWakeWord's models are run with `onnxruntime` directly; the `openwakeword` Python package is not a dependency. Reason: the package needs `tflite-runtime`, which has no wheel for Python 3.12, and the streaming feature code it would supply is about forty lines (`src/autobot/listener/engines/openwakeword.py`).
- `sounddevice` is imported inside the functions that touch hardware. Reason: importing a module must never load PortAudio, so that the tests and the terminal user interface can import any module on a machine with no audio.

### Control layers

- The terminal user interface under `src/autobot/tui/` is the application's interface, not a layer below a web application: everything the owner does by hand goes through it (TS-1, ADR 0006). No web interface, PWA or wall UI is built. Reason: the owner decided that interface complexity beyond a full-screen terminal is waste for now, and one interface keeps everything done by hand in one place.
- It is a full-screen interface written with the standard library's `curses` and nothing else, opened by `make launch`, and drawn in the manner of a mid-90s bulletin board: framed panels and menus in line-drawing characters, in colour, on every screen. Its colours come from a theme, which is only a palette; `T` switches to the next one and the choice is written to `[tui] theme` in `config/autobot.toml`. Reason: the owner asked for the look and for changeable themes, `curses` gives both without a dependency, and a setting kept in the one configuration file needs no second store (TS-13).
- The terminal user interface is a front. It holds no configuration of its own, and it reaches the listener in two ways only: by running `python -m autobot.listener.control` as a subprocess, and by reading the event stream on `run/listener.sock`. It never imports the listener. Reason: the interface cannot then come to depend on the listener's internals, and the listener keeps one implementation of each control (TS-10).
- The terminal user interface keeps its logic (editing the configuration file, audio levels, running commands, the menu model, the recording session, the listener's state, the event-stream client, the themes) in modules that do no drawing. The voice dataset, the recording script, the evaluation and its comparison, and the install and revert live in `src/autobot/wakeword/`, which does no drawing either. Reason: the logic can then be tested without a terminal, with the committed fixtures and fakes for the audio backend.
- The `Makefile` is the runtime control layer, beside the application rather than inside it. A bare `make` prints a one-line summary of every target, built from the `## ` comment on each target's rule line, so a new target needs that comment to appear. The listener is started and stopped with `make up` and `make down`; `backend-up` and `backend-down` are aliases of them. The only front-end target is `make launch`, which opens the interface. Reason: one place to look for every command, a summary that cannot fall behind the targets, and commands an agent without a terminal can run.
- Anything that opens the NUC's audio hardware checks first that this machine's hostname equals `host.nuc_hostname`, and stops with a one-line message when it does not. In the interface the recording session, the live monitor and the install and revert of a model refuse in the same words. Reason: support is limited to the NUC (`INFRASTRUCTURE.md`), a clear refusal is better than an audio error from a machine that was never meant to run it, and a clip recorded anywhere else would teach the model the wrong room (TS-11).
- A training run never installs a model and never writes `config/autobot.toml`. It writes a candidate, and the model becomes the installed one only when the owner confirms it in the interface. Reason: a model that will wake the owner must be judged by him, on his own recordings, before it replaces one that may be better (TS-8).

### Documentation

- Every managed document under `docs/` and at the repository root, except `AGENTS.md`, starts with YAML frontmatter declaring its `type`, `title`, `authority` and `status`. Reason: CR2 reads these fields to place the document on the board and to decide which actions it admits.
- Each document keeps the section headings its type declares, in order. Reason: agents and the board find content by those headings; renaming one hides what is under it.
- Specs cite constraints and glossary terms by their numbers and spellings rather than restating them. Reason: a rule described once cannot drift into two versions.

## Open Questions

None at present.
