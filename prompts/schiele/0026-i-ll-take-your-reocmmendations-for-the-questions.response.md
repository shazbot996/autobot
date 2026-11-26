---
run: schiele/0026
at: 2026-11-26T00:25:16.411Z
mode: answer
agent: answer
model: claude:claude-sonnet-5-5
target: docs/plans/active/headless-wake-word-prototype-PLAN.md
template: answer
prompt-sha: 79bcc7a7ecd6
template-sha: 90976540f104
commit: 4622fab+
attachments: []
read:
  - AGENTS.md
  - docs/plans/active/headless-wake-word-prototype-PLAN.md
outcome: success
usage:
  input: 23268
  output: 7440
  peak: 23268
  window: 1000000
cost: 0
---

---
type: plan
title: Headless wake-word prototype
authority: prescriptive
status: active
describes:
  - Makefile
  - pyproject.toml
  - config/autobot.toml
  - deploy/autobot-listener.service
  - src/autobot/**
  - tests/**
provenance:
  - run: schiele/0023
    at: 2026-11-25T23:17:47.114Z
    mode: create
  - run: schiele/0024
    at: 2026-11-25T23:50:50.301Z
    mode: revise
---

# Headless Wake-Word Prototype — Implementation Plan

Implements the autobot — Wake-Word Listener spec (`docs/specs/active/wake-word-listener-SPEC.md`). This is the first build: a headless, always-on local process on the NUC that opens the microphone, listens around the clock for any utterance containing the wake phrase `autobot` ("hey autobot", "yo autobot"), and answers aloud that it heard it. Nothing else from the other two specs is built. The plan also lays the two control layers `AGENTS.md` asks for, the `Makefile` and the terminal user interface, because the prototype cannot be tested on the NUC without them.

The wake-word engine is the thing being built, not a thing being chosen off a shelf. The plan gets there in two moves: a bootstrap detector that proves the whole pipeline (microphone in, acknowledgement out) using the already-proposed local speech-to-text, and then the real engine, a small custom wake-word model trained for `autobot` and run by openWakeWord, which is what makes 24x7 listening cheap. Both detectors sit behind one interface so the rest of the application, when it arrives, sees one event stream.

## 0. The delta

| Spec Rule | Current Implementation | Planned Change |
|---|---|---|
| WW-1 (always-on local process, no audio off the machine) | Nothing | A Python listener process reading the NUC microphone, VAD-gated, run as a systemd user service; no network dependency at all |
| WW-2 (wake phrases read from configuration) | Nothing | `config/autobot.toml` with a `[[wake_phrases]]` list; the only entry configured is `autobot` |
| WW-3 (new kinds of wake phrases) | Nothing | A `Detector` protocol with two kinds: `text_match` (transcript contains the phrase) and `openwakeword` (trained model), selected per phrase by a `kind` field |
| WW-4 (capture after wake, hand-off to STT) | Nothing | Only the hand-off seam: the listener publishes a `WakeEvent` as a JSON line. Capturing the speech that follows is out of scope (section 10) |
| Acknowledgement (spec open question) | Nothing | A spoken acknowledgement rendered once by piper-tts and played on every wake |
| `AGENTS.md`: `Makefile`, bare `make` summary, `make up` / `make down`, `make audio-check` | Nothing | The `Makefile` of step 1 and the audio check of step 2 |
| `AGENTS.md`: terminal user interface opened by `make deploy` | Nothing | A curses TUI for audio configuration and testing, wake-phrase settings, and service install (step 7) |

## 1. Project skeleton and the `Makefile`

**Files:** new `pyproject.toml`, `Makefile`, `src/autobot/__init__.py`, `src/autobot/__main__.py`, `tests/test_package.py`, `.gitignore`.

- One Python package `autobot` under `src/`, with `pyproject.toml` as the only project definition, per `CONVENTIONS.md`. Python 3.12. Dev extras: `pytest`, `ruff`, `mypy`.
- The `Makefile` is the runtime layer. A bare `make` prints a one-line summary of every target. Targets in this step: `setup` (creates `.venv` and installs the package with dev extras), `test`, `lint`, `typecheck`, `up`, `down`, `status`, `logs`, `audio-check`, `deploy`, `train-wakeword`. Targets whose step has not landed yet print "not built yet: see docs/plans/active/headless-wake-word-prototype-PLAN.md step N" and exit non-zero, so the summary is complete from day one.
- `up` and `down` are the back-end targets; they are aliased as `backend-up` and `backend-down`. No front-end targets exist because the build is headless; the summary says so.

**Done when:** on a fresh clone, `make` prints the summary, `make setup && make test && make lint && make typecheck` all pass, and `make up` exits with the "not built yet" message pointing at step 3.

## 2. Configuration and the audio hardware check

**Files:** new `config/autobot.toml`, `src/autobot/config.py`, `src/autobot/audio_check.py`, `tests/test_config.py`.

- `config/autobot.toml` holds everything the prototype reads, in four tables: `[host]` (`nuc_hostname`), `[audio]` (`input_device`, `output_device`, `sample_rate = 16000`, `frame_ms = 80`), `[[wake_phrases]]` (`phrase = "autobot"`, `kind`, `model`, `threshold`, `target = "ack"`), and `[ack]` (`text = "Yes?"`, `voice`). `config.py` loads it with the standard library `tomllib` into typed dataclasses and validates it; a missing or malformed file is a clear error, not a traceback.
- `make audio-check` runs `python -m autobot.audio_check`. It first compares the machine's hostname with `host.nuc_hostname`; when they differ, or the field is unset, it stops with "audio-check runs on the NUC only; this host is <name>" and exits non-zero. On the NUC it lists input and output devices through `sounddevice`, records two seconds from the configured input, prints the peak and RMS level, and plays the recording back on the configured output.

**Done when:** `make audio-check` on the WSL dev server fails with the one-line message; on the NUC it prints the device list and levels and the owner hears the two-second recording played back.

## 3. Listener process with the bootstrap `text_match` detector

**Files:** new `src/autobot/listener/audio.py`, `src/autobot/listener/vad.py`, `src/autobot/listener/detector.py`, `src/autobot/listener/events.py`, `src/autobot/listener/engines/__init__.py`, `src/autobot/listener/engines/text_match.py`, `src/autobot/listener/service.py`; `Makefile` (`up`, `down`, `status`, `logs`).

- `audio.py` opens the configured input device as a 16 kHz mono int16 stream and yields 80 ms frames. `vad.py` wraps Silero VAD (ONNX, local) so that frames of silence never reach a detector; this is the first efficiency measure, and it holds for both detector kinds.
- `detector.py` defines the `Detector` protocol: `feed(frame) -> WakeEvent | None`, plus `WakeEvent(phrase, target, kind, score, at)`. `events.py` writes each `WakeEvent` as one JSON line to the log and to a Unix domain socket at `run/listener.sock`, which is the seam the routing layer will read later.
- `engines/text_match.py` is the bootstrap detector. It buffers voiced audio until the VAD reports an end of speech (or a two-second cap), transcribes the buffer with faster-whisper (`tiny.en`, CPU, int8), lower-cases the text, and fires when it contains the configured phrase. "Hey autobot" and "yo autobot" both match because the test is containment, not equality.
- `service.py` is the main loop: load config, build one detector per `[[wake_phrases]]` entry, run the stream, publish events. It handles `SIGTERM` cleanly and never opens a network connection. `make up` starts it in the background with a pid file under `run/`; `make down` stops it; `make status` shows whether it is running and its CPU and memory; `make logs` tails `run/listener.log`.

**Done when:** on the NUC, `make up` then saying "hey autobot" produces a `WakeEvent` JSON line in `make logs` within three seconds, saying unrelated sentences produces none, and `make status` shows the resident process.

## 4. Spoken acknowledgement

**Files:** new `src/autobot/listener/ack.py`; `src/autobot/listener/service.py`.

- At startup `ack.py` renders `[ack].text` with piper-tts in the configured voice to a WAV held in memory, once. On each `WakeEvent` whose `target` is `ack` it plays that WAV on the configured output device and logs `ack spoken`. Rendering once keeps the response immediate and keeps the always-on loop free of synthesis work.
- While the acknowledgement plays, the detectors are muted so the assistant does not wake itself.

**Done when:** on the NUC, saying "yo autobot" is answered aloud with "Yes?" from the speaker, and the log shows the `WakeEvent` followed by `ack spoken`.

## 5. Tests from recorded fixtures

**Files:** new `tests/fixtures/audio/` (short WAV clips), `tests/listener/test_vad.py`, `tests/listener/test_text_match.py`, `tests/listener/test_events.py`, `tests/listener/conftest.py`.

- Fixtures are 16 kHz mono WAVs recorded on the NUC: three positives ("autobot", "hey autobot", "yo autobot") and three negatives (room noise, unrelated speech, a near miss such as "auto"). They are a few seconds each and are committed.
- Tests drive the detectors by feeding fixture frames through the same `feed()` path the service uses; no test opens a microphone, a speaker or the network, per `CONVENTIONS.md`. Model files for faster-whisper and Silero are resolved from a local `models/` directory; the test run fails clearly if they are missing rather than downloading.

**Done when:** `make test` passes on the NUC and on the dev server with networking disabled, and the positive fixtures each yield exactly one `WakeEvent` while the negatives yield none.

## 6. The `openwakeword` engine and the `autobot` model

**Files:** new `src/autobot/listener/engines/openwakeword.py`, `src/autobot/wakeword/__init__.py`, `src/autobot/wakeword/train.py`, `tests/listener/test_openwakeword.py`; `config/autobot.toml`, `Makefile` (`train-wakeword`).

- `engines/openwakeword.py` runs an openWakeWord model (ONNX, CPU) over each voiced 80 ms frame and fires when the score crosses the phrase's `threshold`, with a short refractory period so one utterance yields one event. This is the efficient 24x7 detector: a few percent of one core, no transcription.
- `wakeword/train.py` is where the engine is built. It synthesises positive clips of the phrase with piper-tts voices (the same local engine as the acknowledgement, varied in voice, speed and noise), mixes them with the negative audio openWakeWord's training utilities provide, trains the small classifier head on openWakeWord's shared feature extractor, and writes `models/wakeword/autobot.onnx`. `make train-wakeword` runs it; it can take a long time on the NUC's CPU, so it prints progress and is resumable.
- The `autobot` entry in `config/autobot.toml` switches to `kind = "openwakeword"`, `model = "models/wakeword/autobot.onnx"`; the `text_match` kind stays available as a fallback and as a second kind that proves WW-3.

**Done when:** `make train-wakeword` produces `models/wakeword/autobot.onnx`; with the config switched, `make up` on the NUC answers "hey autobot" and "yo autobot" aloud, ignores an hour of ordinary room conversation, and `make status` shows idle CPU well below the `text_match` figure recorded in step 3. The fixture tests pass for both kinds.

## 7. The terminal user interface

**Files:** new `src/autobot/tui/__init__.py`, `src/autobot/tui/app.py`, `src/autobot/tui/screens/audio.py`, `src/autobot/tui/screens/wake_phrase.py`, `src/autobot/tui/screens/service.py`, `src/autobot/tui/screens/host.py`; `Makefile` (`deploy`).

- `make deploy` opens a 90s-style full-screen curses interface written with the standard library, with a menu of four screens. **Audio**: pick input and output devices from the live list, show a level meter from the input, record and play a test clip, save a clip into `tests/fixtures/audio/`. **Wake phrase**: edit `threshold`, choose `kind`, run `train-wakeword` with its progress shown, and play the acknowledgement. **Service**: install, enable, start, stop and show the status of the systemd unit from step 8. **Host**: set `nuc_hostname` to this machine.
- Every change is written back to `config/autobot.toml`, which is the only place configuration lives; the TUI is a front for that file, not a second store.
- Cloud deployment and identity and access management screens are placeholders that say the Google Cloud project does not exist yet.

**Done when:** from `make deploy` the owner can select the microphone, watch the meter move while speaking, record and play back a clip, change the threshold, quit, and find the new values in `config/autobot.toml`; `make up` then uses them.

## 8. Running 24x7 as a service

**Files:** new `deploy/autobot-listener.service`; `src/autobot/tui/screens/service.py`, `Makefile` (`up`, `down`, `status`).

- A systemd user unit runs `python -m autobot.listener` from the repository's `.venv`, restarts on failure, and starts at login with `loginctl enable-linger` so it survives reboots. The TUI's Service screen installs it to `~/.config/systemd/user/` and enables it.
- Once the unit is installed, `make up`, `make down` and `make status` drive systemd instead of the foreground pid file, so the owner uses the same commands either way.

**Done when:** after the NUC is rebooted with nobody logged in at the console, saying "autobot" is answered aloud, and `make status` shows the unit active with its uptime, CPU and memory.

## 9. Choices the spec leaves open

1. **Engine: openWakeWord with a custom-trained `autobot` model**, after a `text_match` bootstrap, rather than a classifier built from scratch. The owner wants to build the engine, needs a machine-learning model, and wants it cheap to run all day. openWakeWord is local, Python, CPU-only and trains new phrases from synthetic speech, which answers WW-3 and keeps `CN-1`; the `text_match` bootstrap gets a working prototype on the NUC before training has finished.
2. **"Contains autobot"** is the detection rule for both kinds: `text_match` tests containment of the transcript, and the trained model fires on the phrase wherever it falls in the stream. Prefixes such as "hey" or "yo" are therefore free.
3. **A wake phrase "kind" is the detector that implements it**: `openwakeword` (a trained model file) or `text_match` (a text pattern). New kinds are new `Detector` implementations.
4. **The acknowledgement is spoken** ("Yes?" by default) rather than a tone, with piper-tts, rendered once at startup, because the build is headless and there is no screen to show it on, and because it also proves the local TTS path the later specs depend on.
5. **End of speech is detected by the Silero VAD** for the `text_match` buffer only; detecting the end of the query that follows a wake is deferred with WW-4.
6. **Off the NUC the listener does not run.** `make audio-check` refuses; the service is not installed; tests run anywhere from fixtures. This follows `INFRASTRUCTURE.md`, which limits support to the NUC.
7. **Configuration is TOML** in `config/autobot.toml`, read with `tomllib`, because the project already uses TOML for `pyproject.toml` and it needs no dependency.
8. **The hand-off seam is a JSON line per `WakeEvent`** on `run/listener.sock` and in the log, so the routing layer of the next plan can be a separate process, as `ARCH.md` describes.
9. **Training runs on the NUC's CPU**, not on the Windows dev server. The run may take hours; that is accepted, because `INFRASTRUCTURE.md` supports only the NUC and the training is a one-off that can run overnight, which is why `make train-wakeword` is resumable.

## 10. Out of scope

- Capturing the speech after the wake phrase and passing it to speech-to-text (WW-4 beyond the event seam).
- The rules layer, the agent host, Gemini ADK, the Google Cloud project, and any credentials.
- The wall UI and any front-end `make` targets.
- More than one configured wake phrase, or targets other than `ack`; the config list supports them, but nothing else is configured.
- Cloud deployment and identity and access management screens in the TUI beyond placeholders.

## Settled without asking

- **Derived (CN-1).** The listener, both detectors, the VAD and the training script run on the NUC with no network calls; step 5's tests run with networking disabled to prove it.
- **Derived (CN-2).** The acknowledgement is synthesised locally by piper-tts.
- **Derived (WW-2).** The `autobot` phrase lives in `config/autobot.toml`, not in code.
- **Derived (WW-3).** Two detector kinds behind one protocol, selected per phrase by `kind`.
- **Default.** Python 3.12, `pytest`, `ruff`, `mypy`, `src/` layout: ADR 0001 is still `proposed`, but the transcript asks for code now and `CONVENTIONS.md` already names everything but the type checker; `mypy` is picked as the pip-installable mainstream default.
- **Default.** faster-whisper (`tiny.en`) for the bootstrap detector and piper-tts for the acknowledgement and training clips, following ADR 0003's recommendation.
- **Default.** Audio capture through `sounddevice` at 16 kHz mono, 80 ms frames, which is what openWakeWord and Silero expect.
- **Default.** The NUC is recognised by `host.nuc_hostname` in the config, set from the TUI's Host screen, because `INFRASTRUCTURE.md` records no hostname and says the plan supplies it when it runs.
- **Default.** The TUI is written with the standard library `curses`, which gives the 90s look without a dependency.
- **Default.** Model files live under `models/` and are not committed; test fixture WAVs under `tests/fixtures/audio/` are committed.
- **Default.** The 24x7 process is a systemd user unit with linger enabled, the Ubuntu-native way to keep a per-user service running.

## Changes needed elsewhere

- **docs/decisions/0004-\*-ADR.md (new).** Record acceptance of ADR 0001 (Python) and ADR 0003 (faster-whisper, piper-tts), which this plan builds on while they are still `proposed`.
- **docs/decisions/0005-\*-ADR.md (new).** Record the wake-word engine decision: openWakeWord with a custom `autobot` model, with `text_match` as the bootstrap kind.
- **docs/specs/active/wake-word-listener-SPEC.md.** State that `autobot` is the first test wake phrase and that detection is by containment; resolve the open questions on the engine, the meaning of a "kind", behaviour off the NUC, and the audible acknowledgement.
- **docs/context/SCHEMA.md.** Add `kind`, `model` and `threshold` to the wake phrase configuration, name TOML as its format, and add the `WakeEvent` shape.
- **docs/context/ARCH.md.** Add the Make and TUI layers below the application, name openWakeWord as the listener's engine, and describe `run/listener.sock` as the listener-to-routing seam.
- **docs/context/CONVENTIONS.md.** Record the two control layers and `mypy` as the type checker.
- **docs/context/GLOSSARY.md.** Add entries for openWakeWord, Silero VAD and `WakeEvent`.
- **docs/context/INFRASTRUCTURE.md.** Under "What defines it", name `config/autobot.toml` and `deploy/autobot-listener.service`.
- **AGENTS.md.** Replace "Nothing runs yet" with `make test`, `make lint`, `make typecheck`; drop the WSL test machine references that `INFRASTRUCTURE.md` no longer supports.
- **README.md.** Fill in "Running it" with `make setup`, `make up`, `make deploy`, `make audio-check`.
- **CHANGELOG.md.** Add the prototype under Unreleased.

## Open Questions

None.
