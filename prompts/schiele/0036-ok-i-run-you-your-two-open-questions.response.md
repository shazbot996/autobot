---
run: schiele/0036
at: 2026-11-26T17:02:14.705Z
mode: answer
agent: answer
model: claude:claude-sonnet-5-5
target: docs/plans/active/wake-word-training-studio-PLAN.md
template: answer
prompt-sha: 43bccc44a2e3
template-sha: 90976540f104
commit: e41c938
attachments: []
read:
  - AGENTS.md
  - docs/plans/active/wake-word-training-studio-PLAN.md
outcome: success
usage:
  input: 31285
  output: 14331
  peak: 31285
  window: 1000000
cost: 0
---

---
parent: docs/specs/active/autobot-wake-word-training-studio-SPEC.md
type: plan
title: Wake-word training studio
authority: prescriptive
status: active
describes:
  - config/autobot.toml
  - .gitignore
  - Makefile
  - src/autobot/config.py
  - src/autobot/fetch_models.py
  - src/autobot/wakeword/__init__.py
  - src/autobot/wakeword/dataset.py
  - src/autobot/wakeword/script.py
  - src/autobot/wakeword/evaluate.py
  - src/autobot/wakeword/install.py
  - src/autobot/wakeword/train.py
  - src/autobot/listener/events.py
  - src/autobot/listener/service.py
  - src/autobot/listener/control.py
  - src/autobot/listener/engines/sherpa_kws.py
  - src/autobot/tui/__init__.py
  - src/autobot/tui/app.py
  - src/autobot/tui/audio.py
  - src/autobot/tui/ui.py
  - src/autobot/tui/themes.py
  - src/autobot/tui/session.py
  - src/autobot/tui/monitor.py
  - src/autobot/tui/state.py
  - src/autobot/tui/screens/main.py
  - src/autobot/tui/screens/audio.py
  - src/autobot/tui/screens/wake_phrase.py
  - src/autobot/tui/screens/session.py
  - src/autobot/tui/screens/candidate.py
  - src/autobot/tui/screens/monitor.py
  - src/autobot/tui/screens/dataset.py
  - tests/test_config.py
  - tests/tui/tui_fakes.py
  - tests/tui/test_tui_app.py
  - tests/tui/test_tui_themes.py
  - tests/tui/test_tui_session.py
  - tests/tui/test_tui_monitor.py
  - tests/tui/test_tui_state.py
  - tests/wakeword/test_dataset.py
  - tests/wakeword/test_script.py
  - tests/wakeword/test_evaluate.py
  - tests/wakeword/test_install.py
  - tests/wakeword/test_train.py
  - tests/listener/test_events.py
  - tests/listener/test_service.py
  - tests/listener/test_sherpa_kws.py
  - tests/fixtures/audio/README.md
  - docs/decisions/0006-full-screen-tui-and-training-on-the-owners-voice-ADR.md
  - docs/context/BRIEF.md
  - docs/context/CONVENTIONS.md
  - docs/context/ARCH.md
  - docs/context/SCHEMA.md
  - docs/context/GLOSSARY.md
  - docs/context/INFRASTRUCTURE.md
  - docs/specs/active/wake-word-listener-SPEC.md
  - docs/plans/active/headless-wake-word-prototype-PLAN.md
  - AGENTS.md
  - README.md
  - CHANGELOG.md
provenance:
  - run: schiele/0033
    at: 2026-11-26T16:55:01.200Z
    mode: create
  - run: schiele/0034
    at: 2026-11-26T17:01:14.068Z
    mode: revise
---

# Wake-Word Training Studio — Implementation Plan

Implements the autobot — Wake-Word Training Studio spec (`docs/specs/active/autobot-wake-word-training-studio-SPEC.md`). This is a differential plan over a working but mis-aimed build: the terminal user interface, its four screens, the audio capture, the config editor, the background training job and the synthetic training pipeline all exist and are kept; what changes is where recordings go, what the training trains on, what the evaluation measures, how a model is installed, and what the interface is for. Steps 1 to 12 are the studio; step 13 cleans up the documents the spec supersedes; step 14 is the baseline kind the spec places after the studio. Every step ends with `make test`, `make lint` and `make typecheck` passing.

## 0. The delta

| Spec Rule | Current Implementation | Planned Change |
|---|---|---|
| TS-1 Full-screen TUI is the application | `src/autobot/tui/` exists, opened by `make deploy`; its docstrings, the title bar ("Setup Utility") and the `make` help footer ("No front-end targets exist") call it a control layer | Reframe docstrings, title bar and `Makefile` text; no web target; studio screens become the main menu's first entries (steps 1, 3) |
| TS-2 Untrained to installed; state on main screen | `config/autobot.toml` ships `kind = "openwakeword"`, threshold 0.85; the main menu shows the config summary only; no studio flow | Fresh-install kind `text_match`; a drawing-free `state.py` derives the five states; the main menu shows it and the dataset counts, and leads session, train, evaluate, install (steps 1, 3, 6) |
| TS-3 Scripted recording session | Nothing; `tui/audio.py` has `Capture`, `Clip`, `write_wav` | `wakeword/script.py` (prompts), `tui/session.py` (resumable session model), `screens/session.py` (step 2, 3) |
| TS-4 Voice dataset and manifest | Nothing; `data/` does not exist; `.gitignore` lacks it | `wakeword/dataset.py`: `data/voice/`, `manifest.jsonl`, stable split; `.gitignore` gets `data/` (step 2) |
| TS-5 Recordings never leave the NUC; fixtures exported deliberately | Audio screen writes straight into `tests/fixtures/audio/` (`Services.fixture_dir`) | Audio screen saves into the dataset; a Dataset screen exports a chosen clip under a README fixture name with lead-in and tail (steps 2, 10) |
| TS-6 Training uses real recordings first | `train.py` trains on synthetic shards only; `install()` moves the result into place | A `real` stage: manifest clips featurised, 1-in-5 held out by hash, oversampled beside the synthetic windows; trained and held-out file names written to the evaluation (step 4) |
| TS-7 Bar set on real recordings | Evaluation reports synthetic held-out voices, hard negatives, validation stream | `wakeword/evaluate.py` scores an ONNX on the real held-out set (recall, false-accept rate, ambient FA/h per threshold); `autobot.json` gains a `real` section; threshold recommended on it when any real clip exists (step 5) |
| TS-8 Explicit, compared, reversible install | `--name` writes `candidates/<name>.onnx`; a default run installs over `autobot.onnx` and prints "set threshold ..." | Every run writes `candidate.onnx` + `candidate.json`; `wakeword/install.py` compares, installs (keeps `.prev`), writes kind/model/threshold to the config, reverts; `screens/candidate.py` drives it (step 6) |
| TS-9 Live monitor | Socket broadcasts `wake` events only; no scores, no buffer, no control command | Listener publishes a `score` event per stretch, keeps a 10 s rolling buffer, writes it on `control snapshot`; `tui/monitor.py` reads the socket; `screens/monitor.py` marks right/wrong/missed into the dataset (steps 7, 8) |
| TS-10 Front only, drawing-free logic | Implemented for the existing screens: `ConfigStore`, subprocess `Runner`, no listener import; tests use fakes | Implemented (no change) for what exists; every new module in this plan follows it, and the one new path (socket client) lives in `tui/monitor.py` |
| TS-11 NUC only | `require_nuc` guards the listener, ack and `audio-check`; the TUI's Host screen only reports | `App.require_nuc(what)` with the same one-line message guards the session, monitor and install (step 3) |
| TS-12 Existing screens remain | Audio, Wake phrase, Service, Host and the training pane exist | Kept; the Audio screen's save goes into the dataset (step 2) |
| TS-13 Mid-90s BBS look, three themes, `T` key | One colour palette (`Theme.colour()`) and a mono fallback; double-line frames exist; no themes, no key, no config key | `tui/themes.py` with *light*, *dark*, *sun*; `T` cycles and writes `[tui] theme`; banner and themed panels on every screen (step 11) |
| TS-14 sherpa-onnx KWS baseline kind | Nothing | `engines/sherpa_kws.py` as a third kind, scored on the same real held-out set, after the studio has been used (step 14) |
| TS-15 Joe Medium voice | `[ack] voice = "en_US-lessac-medium"`; `fetch_models` fetches the configured voice | Config and `AckConfig` default become `en_US-joe-medium` (step 1) |

## 1. Configuration shape and fresh-install defaults

**Files:** `config/autobot.toml`, `src/autobot/config.py`, `src/autobot/fetch_models.py`, `Makefile`, `src/autobot/tui/__init__.py`, `src/autobot/tui/app.py`, `tests/test_config.py`, `tests/tui/tui_fakes.py`.

- `config/autobot.toml`: `kind = "text_match"`, `threshold = 0.5`, `model = ""` for `autobot` with a comment that the studio installs the trained model; `[ack] voice = "en_US-joe-medium"`; a new `[tui]` table with `theme = "light"` and a comment naming the three themes.
- `config.py`: `AckConfig.voice` default `en_US-joe-medium`; `TUI_THEMES = ("light", "dark", "sun")`; a `TuiConfig(theme: str = "light")` parsed from the optional `[tui]` table, rejecting an unknown theme name with the usual one-line message; `Config.tui`. `parse_config` accepts the new top-level table.
- `fetch_models.py`: the docstring names the Joe Medium voice; `runtime_assets` is unchanged because it already fetches the configured voice.
- `Makefile`: the `help` footer becomes "`make deploy` opens the application: the full-screen terminal user interface."; the `deploy` comment reads "Open the application: the wake-word training studio and the settings screens"; the `train-wakeword` comment says it writes a candidate for the interface to install.
- `tui/__init__.py` and `app.py`: docstrings say the interface is the application; the title bar reads `autobot <version>`.
- `tui_fakes.py`'s `CONFIG_TEXT` gains the `[tui]` table and the Joe voice so every interface test runs against the shipped shape.

**Done when:** `python -c "from autobot.config import load_config; c = load_config(); print(c.wake_phrases[0].kind, c.ack.voice, c.tui.theme)"` prints `text_match en_US-joe-medium light`; `make` prints the new footer; a config with `theme = "neon"` fails to load with a one-line message naming `[tui] theme`.

## 2. The voice dataset, and the Audio screen saves into it

**Files:** new `src/autobot/wakeword/dataset.py`, `tests/wakeword/test_dataset.py`; modified `.gitignore`, `src/autobot/tui/audio.py`, `src/autobot/tui/app.py`, `src/autobot/tui/screens/audio.py`, `tests/tui/tui_fakes.py`, `tests/tui/test_tui_app.py`.

- `dataset.py` (no drawing, no audio hardware): `VOICE_DIR = Path("data/voice")`, `MANIFEST = VOICE_DIR / "manifest.jsonl"`; a frozen `ClipRecord` with the TS-4 fields (`file`, `phrase`, `prompt`, `prompt_id`, `label`, `source`, `speaker`, `device`, `recorded_at`); `VoiceDataset(root)` with `records()`, `add(clip, record)` (writes the WAV with `write_wav`, appends one JSON line, fsyncs), `remove(file)` (deletes the WAV and rewrites the manifest without the line), `counts()` (clips and minutes per label), `split(file) -> "train" | "heldout"` as `sha1(file) % 5 == 0`, and `for_phrase(phrase)`. File names are `<speaker>-<prompt_id>-<YYYYmmdd-HHMMSS>.wav` through `sanitise_clip_name`. A WAV in the directory with no manifest line is ignored.
- `.gitignore` adds `data/` with the comment "the owner's voice recordings: never committed, never leave the NUC".
- `Services` gains `dataset: VoiceDataset` (default `VoiceDataset(repo / VOICE_DIR)`); `fixture_dir` stays for the export of step 10.
- The Audio screen's "Save the clip..." asks for a label (`pick`: the phrase / other speech / no speech) and the speaker's name (`prompt`, remembering the last answer in the view), then calls `dataset.add` with `prompt = "audio screen"`, `prompt_id = "audio"`, `source = "session"`, the resolved input device's name and the current time. The hint and the message name `data/voice/`.
- Tests: `test_dataset.py` covers round-trip, split stability (the same file name always lands in the same split, about one in five of a hundred names held out), removal, and the ignored stray WAV. `test_record_play_and_save_a_clip` asserts the WAV and the manifest line under `data/voice/` and that nothing appeared in the fixture directory.

**Done when:** recording a clip on the Audio screen and saving it creates `data/voice/<name>.wav` and one line in `data/voice/manifest.jsonl`, `git status` shows nothing new, and `tests/fixtures/audio/` is unchanged.

## 3. The recording script, the session, the NUC guard and the main screen's state

**Files:** new `src/autobot/wakeword/script.py`, `src/autobot/tui/session.py`, `src/autobot/tui/state.py`, `src/autobot/tui/screens/session.py`, `tests/wakeword/test_script.py`, `tests/tui/test_tui_session.py`, `tests/tui/test_tui_state.py`; modified `src/autobot/tui/app.py`, `src/autobot/tui/audio.py`, `src/autobot/tui/screens/main.py`, `tests/tui/test_tui_app.py`.

- `script.py`: `Prompt(id, text, label, instruction, seconds, phrase_last)` and `build_script(phrase, table: PhraseTable) -> list[Prompt]`, deterministic for a phrase. Positives (about 56): the bare phrase ×10, "hey <phrase>" ×8, "yo <phrase>" ×8, the phrase inside a sentence ×8 (prefix + phrase + a `SUFFIXES` entry; `phrase_last = False`), slow ×4, fast ×4, quiet ×4, raised ×4, near the microphone ×3, far from it ×3. Negatives: twenty sound-alikes from `table.hard` (every `train_excluded` text first, then the first distinct trained ones), ten `SENTENCES`. Ambient: five prompts of 60 s each, "say nothing", label `ambient`. The ambient floor and counts are module constants.
- `tui/session.py` (no drawing): `Session(dataset, phrase, speaker, script)` with `next_prompt()` (the first prompt with no manifest clip for this speaker and phrase: resumption falls out of the manifest), `progress()`, `clips_for(prompt)`, `accept(prompt, clip, device)`, `discard(prompt)`; `Capture` gains a per-recording `max_seconds` so an ambient prompt records a minute.
- `tui/state.py` (no drawing): `listener_state(config, repo, candidate_exists, session_open, listener_running) -> State` with the five values, precedence *recording* > *candidate ready* > *listener down* > *installed* > *untrained*; `describe(state)` gives the main-screen line. `listener_running` comes from `control status` run through the `Runner` when the main menu is entered and every ten seconds while it is on top.
- `App.require_nuc(what) -> bool`: compares `services.hostname` with `config.host.nuc_hostname` case-insensitively and, when they differ, says `f"{what} runs on the NUC only; this host is {hostname}"` (the `require_nuc` wording) and returns False. The session screen, and later the monitor and install, call it before doing anything.
- `screens/session.py`: on entry, `require_nuc("the recording session")`, then `prompt` for the speaker's name (default: the last speaker in the manifest). One prompt per screen: instruction, text in large framed type, takes so far, the level meter from the Audio screen's machinery (one input stream, opened as `AudioScreen.start_meter` does). Keys: Enter record/stop, P play, R re-record, D discard, N next, B back a prompt, Esc leave (the session resumes later). The ambient prompt shows a countdown and stops itself.
- Main menu: first entries become "Recording session", "Train the model" (opens the existing `TrainScreen`), "Candidate" (step 6), "Live monitor" (step 8), "Dataset" (step 10), then Audio, Wake phrase, Service, Host, Quit; the cloud placeholders are removed. The panel shows the state line, "Dataset: N positives, N negatives, M min ambient", and "Training is offered once a session is complete" until the counts reach the script's totals; the Train entry is never disabled.
- Tests drive a whole short script (a test-only `build_script` with three prompts) through `make_harness` with `hostname="nuc"`, feeding blocks through `FakeAudio`, and assert the manifest; a run on `testbox` shows the NUC refusal and records nothing; state tests cover each of the five values and the precedence.

**Done when:** on the NUC, "Recording session" asks for a name, shows "autobot" as the first prompt, records on Enter, plays back on P, and after Esc and reopening resumes at the first unanswered prompt; on another machine it shows the one-line refusal; the main menu shows *untrained* on a fresh checkout and *recording* while a session is open.

## 4. Training on the real recordings, writing a candidate

**Files:** modified `src/autobot/wakeword/train.py`, `src/autobot/wakeword/__init__.py`, `tests/wakeword/test_train.py`.

- A `real` stage between `feats` and `train`: `load_real(dataset, phrase)` returns the manifest clips for the phrase in a fixed order with their split; `real_feats(run, clips)` writes `work/real/<manifest-sha1>.npz` holding, for the training split, 3 s windows (one clean, two through `Augmenter`) with the phrase's end placed at 1.3 to 2.9 s like the synthetic ones; for the held-out split, the raw int16 audio is kept for `evaluate.py` (step 5). The phrase end of a positive is the end of its voiced span (energy above the clip's noise floor) when `prompt.phrase_last`; otherwise faster-whisper `tiny.en` word timestamps locate the phrase locally, falling back to the voiced span's end when it is not found. Negatives and ambient audio are cut into 3 s windows with no key.
- `stage_train`: every batch of 256 positives draws half from the real positive inputs and 128 of the 384 negatives from real negative and ambient inputs when any exist, else the batch is the first model's. The checkpoint identity gains `real_manifest` (the manifest hash) so a changed dataset starts a fresh run.
- Output: `Run.output` defaults to `models/wakeword/candidate.onnx` with `candidate.json`; the `install()` function and the "set threshold ... in config/autobot.toml" message go; the final log line says the candidate is ready for the interface. `--name` still writes `candidates/<name>.onnx`. `--output` is kept for tests.
- `autobot.json` gains `"real": {"trained": [files], "heldout": [files], "counts": {...}}` with `"note": "no real recordings: synthetic clips only"` when the manifest is empty for the phrase.
- Tests (no torch): the split lists are disjoint and together equal the manifest; `real_manifest` changes the identity; a run with an empty dataset plans the same shards as before; the default output is `candidate.onnx`.

**Done when:** `make train-wakeword` on a checkout with a recorded session logs `real: N training clips, M held out`, writes `models/wakeword/candidate.onnx` and `candidate.json` whose `real.trained` and `real.heldout` list the file names, and leaves `models/wakeword/autobot.onnx` and `config/autobot.toml` byte-identical.

## 5. The real-set evaluation

**Files:** new `src/autobot/wakeword/evaluate.py`, `tests/wakeword/test_evaluate.py`; modified `src/autobot/wakeword/train.py`.

- `evaluate.py` runs with the runtime dependencies only (`onnxruntime`, `numpy`), so the interface can call it without the `train` extra: `RealScores` from `score_real(model: Path, dataset, phrase, thresholds=THRESHOLDS)`: peak score per held-out positive and negative clip through `ClipFeatures` on 4 s windows, and a streamed score over the concatenated held-out ambient audio; per threshold `recall`, `fa_rate` (held-out real negatives) and `ambient_fa_per_hour` via `count_events`. `recommend_real(rows)` applies the existing `recommend` rule with ambient FA/h in place of the validation stream. `compare(candidate, installed) -> Comparison` says whether the candidate is at least as good (recall no lower, `fa_rate` and `ambient_fa_per_hour` no higher, each at its own recommended threshold) and formats a side-by-side table.
- `stage_eval` scores the exported ONNX with `score_real` and writes `real.sweep`, `real.recommended_threshold`, `real.hours_ambient`; `recommended_threshold` at the top level is the real one when any real clip exists and the synthetic one otherwise, with `"recommended_on": "real" | "synthetic"`.
- Tests use the committed fixtures as a tiny dataset (positives `autobot.wav`, `hey_autobot.wav`; negatives `near_miss_auto.wav`, `unrelated_speech.wav`; ambient `room_noise.wav`) and `models/wakeword/autobot.onnx`, failing with the file's name when a model is missing; a `compare` test with hand-built rows.

**Done when:** `python -m autobot.wakeword.evaluate models/wakeword/candidate.onnx` prints the real sweep and the recommended threshold, and `candidate.json` after a training run has a `real.sweep` and says which set the threshold was recommended on.

## 6. Candidate: compare, install, revert

**Files:** new `src/autobot/wakeword/install.py`, `src/autobot/tui/screens/candidate.py`, `tests/wakeword/test_install.py`; modified `src/autobot/tui/app.py`, `src/autobot/tui/screens/main.py`, `src/autobot/tui/screens/wake_phrase.py`, `tests/tui/test_tui_app.py`.

- `install.py` (no drawing): `paths(repo, phrase_config)` for `candidate`, `installed`, `previous` (`autobot.prev.onnx` and `.json`); `install_candidate(paths, store, index)` copies the installed pair to `.prev` through temporary names and `os.replace`, moves the candidate pair into place, then writes `Change(kind=openwakeword)`, `Change(model=...)`, `Change(threshold=recommended)` through `ConfigStore.update`; `revert(paths, store, index)` swaps installed and `.prev` and writes the `.prev` evaluation's threshold. Both raise `InstallError` with one line on any missing file.
- `screens/candidate.py`: `require_nuc("installing a model")`; shows the candidate's and the installed model's real-set figures re-scored now with `score_real` on the current held-out clips (run behind `wait`), the `compare` verdict, and offers "Install the candidate" only when the verdict is good or nothing is installed; "Revert to the previous model" when `.prev` exists. Both confirm, then offer to restart a running listener through `control down` and `up`.
- The Wake phrase screen's facts gain "Candidate: ready / none"; the main menu's *candidate ready* state links here.
- Tests: install and revert against `tmp_path` with fake ONNX bytes and JSON, asserting the config lines and the comments surviving; the screen refuses the install when the candidate is worse, and writes `kind = "openwakeword"` and the threshold when it is better.

**Done when:** with a candidate trained, the Candidate screen shows two columns of real-set figures, Install asks "Install the candidate?", and afterwards `config/autobot.toml` reads `kind = "openwakeword"`, `model = "models/wakeword/autobot.onnx"` and the recommended threshold, `autobot.prev.onnx` exists, and Revert restores the previous file and threshold.

## 7. The listener publishes scores and keeps a rolling buffer

**Files:** modified `src/autobot/listener/events.py`, `src/autobot/listener/service.py`, `src/autobot/listener/control.py`, `tests/listener/test_events.py`, `tests/listener/test_service.py`.

- `events.py`: a `ScoreEvent(phrase, kind, score, at)` and `to_json_line` for both kinds (`"event": "score"`); `publish` takes either. Score events go to the socket only, not to the log.
- `service.py`: after each `pipeline.process`, when the stretch count rose, one `ScoreEvent` per detector from `last_peak` (openwakeword) or `last_transcript.score` (text_match). A `collections.deque` of the last 125 microphone frames (10 s) is kept in `run`; `SIGUSR1` sets a flag the loop checks and writes the buffer as `run/snapshot.wav` through a temporary name and `os.replace`, then logs "snapshot written".
- `control.py`: `snapshot` finds the listener pid (service or foreground), records the time, sends `SIGUSR1`, waits up to five seconds for `run/snapshot.wav` newer than the request, and prints the path; it fails with one line when the listener is not running. The module docstring and `build_parser` list it.
- Tests: `to_json_line` for a score event; a `Pipeline` run over the fixtures through a fake publisher sees one score event per stretch; the snapshot writer, called directly with a filled deque, writes a 16 kHz mono WAV of the expected length.

**Done when:** with the listener up, `nc -U run/listener.sock` shows a `score` line after each spoken stretch, and `python -m autobot.listener.control snapshot` prints `run/snapshot.wav`, a 10 s WAV.

## 8. The live monitor screen

**Files:** new `src/autobot/tui/monitor.py`, `src/autobot/tui/screens/monitor.py`, `tests/tui/test_tui_monitor.py`; modified `src/autobot/tui/app.py`, `src/autobot/tui/runner.py`, `tests/tui/tui_fakes.py`.

- `tui/monitor.py` (no drawing, no curses): `EventStream(path)` with a non-blocking Unix socket client, `poll() -> list[dict]` parsing complete JSON lines, reconnecting every two seconds when the socket is absent or dropped, and `connected`. `Services` gains `events: EventStream` (tests inject a fake fed from a list).
- `runner.py`: `listener_control_argv("snapshot")` is already general; add `SNAPSHOT_FILE = Path("run/snapshot.wav")`.
- `screens/monitor.py`: `require_nuc("the live monitor")`; a scrolling log of events (time, kind, score, "WAKE" in the error colour) with the connection state in the facts. Keys R (right), W (wrong), M (missed) run `control snapshot` behind `wait`, read the WAV, and `dataset.add` it with `source = "monitor"`, label `positive`/`negative`/`positive`, `prompt = "monitor: right detection" | "monitor: false accept" | "monitor: missed wake"`, `prompt_id = "monitor-right" | "monitor-wrong" | "monitor-missed"`, the speaker asked once on entry (default: the last in the manifest). The hint says a mark saves the last ten seconds into the dataset.
- Tests: a fake stream delivers a `score` and a `wake` line, the screen shows them, pressing W runs the snapshot command and adds a `negative` clip with `source = "monitor"`.

**Done when:** on the NUC with the listener up, the Live monitor shows scores as you speak, and pressing M after a missed "autobot" adds a line with `"source": "monitor", "label": "positive"` to `data/voice/manifest.jsonl` and a 10 s WAV beside it.

## 9. Training pane and job hand-off kept, pointed at the candidate

**Files:** modified `src/autobot/tui/screens/wake_phrase.py`, `src/autobot/tui/screens/main.py`.

- `TrainScreen` is unchanged in mechanism; its hint says the run writes a candidate, and when the job ends with status 0 it offers to open the Candidate screen. The main menu's "Train the model" entry opens it directly.

**Done when:** starting the training from the main menu shows the log as before, and when it finishes a "Candidate ready: compare and install?" dialog opens the Candidate screen.

## 10. The Dataset screen and the fixture export

**Files:** new `src/autobot/tui/screens/dataset.py`; modified `src/autobot/wakeword/dataset.py`, `src/autobot/tui/screens/main.py`, `tests/wakeword/test_dataset.py`, `tests/tui/test_tui_app.py`, `tests/fixtures/audio/README.md`.

- `dataset.py` gains `export_fixture(record, source, destination, lead_in=0.5, tail=1.0)`: pads with silence so the voiced span has at least the lead-in before and tail after, writes 16 kHz mono WAV. `FIXTURE_NAMES` is read from the README's table at test time to keep them in step.
- `screens/dataset.py`: a scrollable list of manifest clips (speaker, label, prompt, seconds, split), P play, D delete (the confirmation says that deleting a clip changes the held-out set and makes earlier evaluations incomparable), E export: `pick` one of the README fixture names, confirm overwriting, write into `services.fixture_dir`. The facts show the counts and minutes.
- README: fixtures are exported from the Dataset screen, clip by clip; the "saved from the Audio screen" paragraph goes.

**Done when:** choosing a clip and E, then `autobot.wav`, writes `tests/fixtures/audio/autobot.wav` with at least 0.5 s of quiet before and 1 s after the speech, and `make test` still passes on the new fixture.

## 11. Themes and the bulletin-board look

**Files:** new `src/autobot/tui/themes.py`, `tests/tui/test_tui_themes.py`; modified `src/autobot/tui/ui.py`, `src/autobot/tui/app.py`, `src/autobot/tui/screens/main.py`, `tests/tui/tui_fakes.py`, `tests/tui/test_tui_app.py`.

- `themes.py`: `Palette` (a colour per `Theme` role, as curses colour constants plus bold flags) and `PALETTES = {"light": ..., "dark": ..., "sun": ...}` in `TUI_THEMES` order; `Theme.from_palette(palette)` initialises the colour pairs; `next_theme(name)` cycles and wraps. `Theme.mono()` stays for `NO_COLOR` and terminals without colour, where `T` says "this terminal has no colours".
- `App`: `cycle_theme()` swaps `canvas.theme` and saves `Change("tui", "theme", name)`; `dispatch` handles `T`/`t` before the view when the top view has no focused text field. The dialogs' own key loops already consume letters, so the speaker-name prompt types a `t`. The key bar shows `T Theme`.
- Look: the main menu gets a framed banner (`autobot` in block glyphs, falling back to plain text in ASCII mode) and the state line in a coloured badge; every `MenuView` draws its facts in a framed panel and its menu with the pointer glyph; the title bar and key bar take their colours from the theme. No new dependency.
- Tests: pressing `T` three times cycles light, dark, sun, light in the config file; `T` inside the speaker-name prompt appears in the typed name; every screen draws in each theme on the fake screen.

**Done when:** pressing `T` on any screen changes the colours at once and `config/autobot.toml` reads `theme = "dark"`, then `"sun"`, then `"light"`; reopening with `make deploy` starts in the saved theme.

## 12. Studio wiring check

**Files:** `tests/tui/test_tui_app.py`.

- One end-to-end interface test on the fake screen with `hostname="nuc"`: fresh config (*untrained*) → three-prompt session → a fake `candidate.onnx` and `candidate.json` placed by the test (*candidate ready*) → install (config reads `openwakeword`, *installed*) → revert (threshold restored). It is the regression that the road of TS-2 stays connected.

**Done when:** that test passes and `make test`, `make lint`, `make typecheck` are green.

## 13. Clean up the documents the spec supersedes

**Files:** new `docs/decisions/0006-full-screen-tui-and-training-on-the-owners-voice-ADR.md`; modified `docs/context/BRIEF.md`, `docs/context/CONVENTIONS.md`, `docs/context/ARCH.md`, `docs/context/SCHEMA.md`, `docs/context/GLOSSARY.md`, `docs/context/INFRASTRUCTURE.md`, `docs/specs/active/wake-word-listener-SPEC.md`, `docs/plans/active/headless-wake-word-prototype-PLAN.md`, `AGENTS.md`, `README.md`, `CHANGELOG.md`.

- Each change is the one the spec's "Changes needed elsewhere" names for that file, made after steps 1 to 12 have worked on the NUC: ADR 0006 records the two decisions and names the parts of ADR 0005 it supersedes (ADR 0005 is not edited); CONVENTIONS "Control layers" and "Layout" (`data/voice/`); ARCH, SCHEMA (manifest line, `score` event, `real` fields, `[tui] theme`), GLOSSARY, INFRASTRUCTURE, BRIEF; the listener spec gains the buffer, the score event, the `text_match` fresh-install kind and the Joe voice; the prototype plan marks steps 6 and 7 superseded by this plan; AGENTS.md drops the web-interface sentence and names the Joe voice in the fetched models; README "Running it" opens with `make deploy`; CHANGELOG records the studio under Unreleased.

**Done when:** every file in the spec's "Changes needed elsewhere" list has its change, `grep -rn "web interface\|wall UI" AGENTS.md docs/context/CONVENTIONS.md` finds only wording that says they are not built, and ADR 0005 is byte-identical to before.

## 14. The sherpa-onnx KWS baseline kind

**Files:** new `src/autobot/listener/engines/sherpa_kws.py`, `tests/listener/test_sherpa_kws.py`; modified `src/autobot/config.py`, `src/autobot/fetch_models.py`, `src/autobot/wakeword/evaluate.py`, `pyproject.toml`, `tests/fixtures/audio/README.md`.

- Runs only after the studio has installed at least one model. `sherpa_kws` joins `WAKE_PHRASE_KINDS`; the engine module's `build` loads a local KWS model under `models/sherpa-kws/` (fetched by `make fetch-models`; `ModelMissingError` when absent) with the phrase as its keyword, and follows the `Detector` protocol; `sherpa-onnx` becomes a runtime dependency, imported inside `build` after `enforce_offline()`. `evaluate.py` gains `score_kind(kind, config, dataset)` that runs any detector over the held-out real clips through `Pipeline`, so the Candidate screen can show the baseline's recall and false accepts in a third column.

**Done when:** `kind = "sherpa_kws"` starts the listener on the NUC without training, and the Candidate screen shows its real-set figures beside the trained model's.

## 15. Choices the spec leaves open

1. **Where the phrase ends in a real positive.** Prompts whose text ends with the phrase use the voiced span's end; sentence prompts use faster-whisper `tiny.en` word timestamps, run locally, to locate it, falling back to the span's end. Alignment is not labelling: the label still comes from the prompt.
2. **How few real clips are mixed with how many synthetic windows.** Half of every positive batch and a third of every negative batch are real when any exist, so a session of fifty clips is heard as often as the synthetic set.
3. **One candidate at a time.** The default output is `models/wakeword/candidate.onnx`; a new run replaces it. `--name` keeps writing named candidates for experiments; the interface installs only `candidate.onnx`.
4. **The control-interface hand-off for the buffer** is a `snapshot` subcommand that signals the listener with `SIGUSR1` and waits for `run/snapshot.wav`; the socket stays one-way.
5. **Score events** go to the socket only, not the log, which keeps the log as it is today.
6. **The theme setting** lives in a `[tui]` table as `theme`.
7. **Label of an Audio-screen clip.** The save asks which of the three labels applies and records `prompt = "audio screen"`, `source = "session"`.
8. **After install or revert** the interface offers to restart a running listener with `control down` and `up`, since the listener loads its model at start.
9. **The real-set recommendation** reuses `recommend` with ambient false accepts per hour in the validation stream's place and the same one-per-hour target.
10. **Hard-negative count per session.** The script asks for twenty sound-alikes, `train_excluded` first, although the table has over a hundred, because a session should fit in half an hour and the monitor's marks add the rest from real use.
11. **Deleting clips from the Dataset screen.** Deletion is allowed, with a confirmation that says it changes the held-out set and makes earlier evaluations incomparable, because a bad take (a cough, a door) would otherwise stay in the data for good.

## 16. Out of scope

- A speaker-verification stage (TS-7 leaves it to a later spec).
- A per-speaker held-out split (TS-6 defers it until there are two speakers).
- A kiosk or login start of the interface (TS-1 leaves it to the Service screen later).
- Any web interface, PWA or wall UI, and any front-end `make` target.
- Changes to WW-1 to WW-9 beyond the additions the spec names.

## Settled without asking

- **Derived (TS-10).** The real-set scorer uses `onnxruntime` only, so the interface compares models without the `train` extra and without importing the listener.
- **Derived (TS-11).** The interface's NUC check reuses the one-line wording of `require_nuc` so the three refusals read the same.
- **Derived (TS-13).** `T` is handled in `App.dispatch` after the dialogs' own loops, which is what makes it type a letter in a text field.
- **Default.** The `[tui]` table name, the `prompt_id` field beside `prompt`, the `snapshot` subcommand, and the half-real batch mix, as listed in section 15.
- **Default.** The cloud placeholders leave the main menu: nothing behind them exists and the studio entries need the room; the Service screen keeps its place.

## Open Questions

None at present.
