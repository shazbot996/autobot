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
- Model files live under `models/` and runtime state (the pid file, the log, the socket) under `run/`. Neither is committed. Reason: models are large binaries that are fetched or trained, not written, and runtime state belongs to one machine.

### Naming

- Modules, functions and variables are `snake_case`; classes are `CapWords`; constants are `UPPER_SNAKE_CASE`, as PEP 8 prescribes. Reason: the standard library and every chosen dependency follow PEP 8, so anything else reads as foreign.
- Project terms are spelled as `GLOSSARY.md` spells them: `wake_phrase`, `rule`, `agent`, `session`, `memory`. Reason: the glossary is the single vocabulary and the code should be searchable by it.
- A wake-word engine module is named after its kind: the kind `text_match` is `src/autobot/listener/engines/text_match.py`. Reason: the listener finds an engine by importing the module of that name, so the name is the registration.

### Testing

- The test runner is `pytest`, run by `make test`. Reason: it is the mainstream Python default and what agents run before reporting.
- Tests never require a microphone, a speaker or a network connection; audio and cloud boundaries are exercised through recorded fixtures and fakes. Reason: tests must pass in sandboxes without audio hardware, and a test that reached a cloud speech service would itself violate `CONSTRAINTS.md`.
- Tests of the listener drive the detectors, of every kind, through the same frame path the service uses, from the WAV fixtures in `tests/fixtures/audio/`, which are committed. Reason: a test that takes a shortcut around the speech gate proves nothing about what the microphone will do.
- Tests never download a model and never train one. They look model files up under `models/`, the trained wake-word model among them, and fail with a message naming the missing file. Reason: a silent download is a network call in the speech path, which `CN-1` and `CN-2` forbid, and a skipped test would hide that the listener cannot run.

### Local-only speech

- Any module that imports a speech library (`onnxruntime`, `faster_whisper` or `piper`) calls `autobot.listener.models.enforce_offline()` before the import, and the import sits inside the function that needs it. Reason: `onnxruntime` uploads usage telemetry from a native thread a few seconds after it is imported unless `ORT_DISABLE_TELEMETRY=1` is already set, and the Hugging Face code that faster-whisper pulls in looks things up online unless it is told not to. Either would break `CN-1`, and neither can be switched off after the import.
- A model is loaded from a local file path, never by a name that a library could resolve by downloading. Reason: the same constraints; a missing file must be an error, not a fetch.
- One module downloads: `autobot.fetch_models`, run by `make fetch-models`. It fetches public model and data files into `models/` and sends nothing else. The listener, the tests and the training (`autobot.wakeword.train`) never call it and never open a network connection; each stops with one line naming the file it misses. Reason: keeping every download in one command a person runs makes the rest of the code checkable against `CN-1` and `CN-2` by reading it.
- openWakeWord's models are run with `onnxruntime` directly; the `openwakeword` Python package is not a dependency. Reason: the package needs `tflite-runtime`, which has no wheel for Python 3.12, and the streaming feature code it would supply is about forty lines (`src/autobot/listener/engines/openwakeword.py`).
- `sounddevice` is imported inside the functions that touch hardware. Reason: importing a module must never load PortAudio, so that the tests and the terminal user interface can import any module on a machine with no audio.

### Control layers

- Runtime, platform and hardware controls are built into two layers below the application, never into the application itself: the `Makefile` and the terminal user interface. Reason: the owner wants starting, stopping, configuring and deploying kept apart from the application, which runs with a web interface that covers everything else.
- The `Makefile` handles runtime control. A bare `make` prints a one-line summary of every target, built from the `## ` comment on each target's rule line, so a new target needs that comment to appear. Servers are started and stopped with `make up` and `make down`, with separate targets for front-end and back-end controls; `backend-up` and `backend-down` are the back-end ones, and no front-end targets exist while the build is headless. Reason: one place to look for every command, and a summary that cannot fall behind the targets.
- The terminal user interface is a 90s-style full-screen interface written with the standard library's `curses`, opened by `make deploy`. It manages configuration, the service and, later, cloud deployment, the identity and access management checks and the other platform work the application depends on but does not handle itself. Reason: the owner asked for it, and `curses` gives the look without a dependency.
- The terminal user interface keeps its logic (editing the configuration file, audio levels, running commands, the menu model) in modules that do no drawing, and reaches the listener only by running its control commands as subprocesses. Reason: the logic can then be tested without a terminal, and the interface cannot come to depend on the listener's internals.
- Anything that opens the NUC's audio hardware checks first that this machine's hostname equals `host.nuc_hostname`, and stops with a one-line message when it does not. Reason: support is limited to the NUC (`INFRASTRUCTURE.md`), and a clear refusal is better than an audio error from a machine that was never meant to run it.

### Documentation

- Every managed document under `docs/` and at the repository root, except `AGENTS.md`, starts with YAML frontmatter declaring its `type`, `title`, `authority` and `status`. Reason: CR2 reads these fields to place the document on the board and to decide which actions it admits.
- Each document keeps the section headings its type declares, in order. Reason: agents and the board find content by those headings; renaming one hides what is under it.
- Specs cite constraints and glossary terms by their numbers and spellings rather than restating them. Reason: a rule described once cannot drift into two versions.

## Open Questions

- How is the wall UI written? Q3 leaves the interface undecided, so no front-end conventions exist yet.
