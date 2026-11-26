---
run: schiele/0032
at: 2026-11-26T16:52:49.698Z
mode: answer
agent: answer
model: claude:claude-sonnet-5-5
target: docs/specs/active/autobot-wake-word-training-studio-SPEC.md
template: answer
prompt-sha: ba9aea2d85d6
template-sha: 90976540f104
commit: 8265bea
attachments: []
read:
  - AGENTS.md
  - docs/specs/active/autobot-wake-word-training-studio-SPEC.md
outcome: success
usage:
  input: 22231
  output: 7020
  peak: 22231
  window: 1000000
cost: 0
---

---
type: spec
title: autobot — Wake-Word Training Studio
authority: prescriptive
status: active
describes:
  - src/autobot/tui/**
  - src/autobot/wakeword/**
  - tests/tui/**
  - tests/wakeword/**
provenance:
  - run: schiele/0027
    at: 2026-11-26T16:41:23.305Z
    mode: create
  - run: schiele/0028
    at: 2026-11-26T16:45:47.064Z
    mode: revise
  - run: schiele/0029
    at: 2026-11-26T16:51:43.683Z
    mode: revise
---

# autobot — Wake-Word Training Studio

The terminal user interface is now the application, not a control panel beside it: a full-screen `curses` interface on the NUC, and its first job is to make the wake-word listener work in this house by collecting the owner's voice through the AIRHUG, in this room, and training the `autobot` model from it. A fresh install is expected not to hear well; the interface walks the owner from that state to a trained, installed model and keeps improving it from real use. Three head-only retrains on cached features (runs A, B and B2 against run 3) showed that every number the project has is for synthetic voices, that openWakeWord's validation stream is too short to rank models at one false accept an hour, and that the misses sit where synthetic speech is weakest: accents, the bare word, slow renderings. Rules TS-1 to TS-15 below govern the studio. This is a superseding spec. It overrides: the `headless-wake-word-prototype-PLAN.md` step 6 behaviour by which `make train-wakeword` installs its result directly and treats synthetic clips as the training set; step 7's definition of the interface as a control panel of four screens with the Audio screen saving clips straight into `tests/fixtures/audio/`; the plan's "Choices the spec leaves open" item 1 insofar as it names synthetic speech as the model's training source; ADR 0005's statement that `openwakeword` is the configured kind on a fresh checkout; the "Control layers" convention in `CONVENTIONS.md` that places the terminal user interface below an application with a web interface; and the `tests/fixtures/audio/README.md` instruction to record fixtures from the Audio screen. No rule of `wake-word-listener-SPEC.md` is withdrawn: WW-1 to WW-9 stand, and this spec builds on WW-7 (kinds) and WW-9 (NUC only). Those documents are not edited here; the plan written from this spec cleans them up once it has worked.

## Numbered rules, each with its reason

1. **TS-1. The application is one full-screen terminal user interface.** autobot's user-facing application is the terminal user interface under `src/autobot/tui/`, run full screen in a terminal on the NUC and opened by `make deploy`. It fills whatever terminal it is given. The owner opens it by hand with `make deploy`; it does not start itself at login on the NUC's X display, and a kiosk start is a platform step the Service screen can add once the studio has been used. Its first job is the one this spec describes: training the wake-word listener on the owner's voice. Later jobs of the application appear as further screens of the same interface; no web interface, PWA or wall UI is built, and no front-end `make` target other than the one that opens this interface exists. Reason: the owner decided that interface complexity beyond a full-screen terminal is waste for now, and one interface focuses the architecture: everything the owner does by hand goes through one place, drawn with the standard library and nothing else.

2. **TS-2. The studio leads from untrained to installed.** On a fresh install the configured kind of the `autobot` phrase is `text_match` and the interface reports that no trained model is installed. From there it leads the owner, in order, through a recording session (TS-3), a training run (TS-6), an evaluation (TS-7) and an explicit install (TS-8). The interface offers the first training run once one complete session has been recorded, about fifty positives and the five-minute ambient floor; it always shows how many clips and how many minutes the dataset holds, but it never refuses to train on less. The listener's state is shown at all times on the interface's main screen, as one of: *untrained* (`text_match`, no model file), *recording* (a session is open), *candidate ready* (a trained model awaits the owner's decision), *installed* (an `openwakeword` model is configured and its file exists), or *listener down*. Reason: the first model trained on synthetic speech alone is not good enough to leave on in a house (ADR 0005, Consequences), so a fresh install must start on the kind that needs no model, and the owner must always be able to see where in the road to a trained model the system stands. Nothing yet says how few clips are too few, and the real-set figures of TS-7 will, so the interface informs rather than refuses.

3. **TS-3. A scripted recording session.** The interface runs a scripted session that shows the owner one prompt at a time and records each response on the NUC through the configured input device (`[audio] input_device`, resolved as the Audio screen resolves it today). A session begins by asking for the speaker's name, and every clip of the session carries it; another household voice is added by running the same session under that person's own name. The script covers at least: the bare phrase; the phrase with the prefixes "hey" and "yo"; the phrase inside a sentence; slow, fast, quiet and raised delivery; near the microphone and far from it. It also collects negatives: the sound-alikes of `PhraseTable.hard` for the phrase in `src/autobot/wakeword/data.py`, ordinary sentences, and at least five minutes of hands-off room audio during which the owner says nothing. Every clip is stored with the prompt that asked for it, so each is labelled by construction and no clip is labelled by hand afterwards. A session can be stopped at any prompt and resumed later from the first prompt not yet answered; any clip can be played back, re-recorded or discarded while its prompt is shown. Reason: the misses of the synthetic models are concentrated in exactly the deliveries this script asks for, and a prompt-driven session is the only way to get labelled real audio without a transcription step that could mislabel it. The five-minute ambient floor and the prompt list beyond Fable's minimum are this spec's choices.

4. **TS-4. The voice dataset lives in one directory with one manifest.** Recordings are 16 kHz mono 16-bit WAV files under `data/voice/` in the checkout, and `data/voice/manifest.jsonl` is the only record of what each clip is: one JSON object per line with the clip's file name, the `phrase` it was recorded for, the prompt text, the label (`positive`, `negative` or `ambient`), the `source` (`session` or `monitor`, per TS-9), the speaker, the input device name, and the date and time of recording. A clip not in the manifest does not exist to the training. `data/voice/` is never committed. Reason: one directory and one manifest keep the dataset inspectable and the split reproducible (TS-6); recording the device and date lets a later evaluation separate microphones and sessions. `data/voice/` rather than `models/wakeword/` is this spec's choice: `CONVENTIONS.md` reserves `models/` for files that are fetched or trained, and the owner's recordings are neither.

5. **TS-5. The recordings never leave the NUC.** No clip in `data/voice/`, and no feature computed from one, is sent anywhere, uploaded or copied off the machine by any part of autobot. The committed fixtures under `tests/fixtures/audio/` are a separate, small, hand-picked export: the owner chooses a clip in the interface and exports it under one of the fixture file names the fixtures README lists, with the quiet lead-in and tail it asks for. Reason: the clips are the owner's voice in his own house, and the reasoning of `CN-1` (sound from the house stays on the premises) applies to them as it does to the live stream; the fixtures are committed and therefore public, so what goes into them must be a deliberate choice, clip by clip.

6. **TS-6. Training uses the real recordings first.** `make train-wakeword` reads the voice dataset: the real positives and negatives are training data, and the synthetic clips of the existing pipeline (`src/autobot/wakeword/train.py` stages synth and feats) are augmentation beside them, not the primary source. A held-out split of the real recordings, one clip in five chosen by a stable hash of the clip's file name, is reserved for evaluation and is never trained on; a clip's split never changes between runs. The split is made per clip whoever the speaker is; a per-speaker split can be added once there are two speakers. Which real clips were trained on and which were held out is written into the model's evaluation file. A run with no real recordings still trains, on synthetic clips alone, and its evaluation says so. Reason: the real recordings are what the listener will actually hear and are few, so each must be used fully but never measured on what it trained on; a stable split keeps two runs comparable; and keeping the synthetic pipeline means a phrase with no recordings yet can still be trained. The manifest already records the speaker, so holding out per speaker later needs no new data. The one-in-five split is this spec's choice.

7. **TS-7. The bar is set on the real recordings.** A model's evaluation (`autobot.json` beside the model, as today) reports, separately from every synthetic figure: recall on the held-out real positives, the false-accept rate on the held-out real negatives, and false accepts per hour on the ambient room audio, each at the sweep of thresholds `train.py` already reports. The recommended threshold is chosen on the real figures when any real recordings exist. No speaker-verification stage (openWakeWord's custom verifier model trained on the owner's clips) belongs to this spec; it is left to a later spec. Reason: the synthetic held-out voices and openWakeWord's validation stream are what the three retrains showed cannot rank models for this room; the real set can, and it must be reported on its own so that it is never averaged away. A verifier presupposes a model that already detects the phrase, and these real-set figures are what will show whether one is needed.

8. **TS-8. Install is explicit, compared, and reversible.** A training run writes a candidate and leaves the installed model and `config/autobot.toml` untouched. The interface shows the candidate's real-set figures (TS-7) beside the installed model's, re-scored on the same current held-out clips, and offers to install it only when the candidate is at least as good on both real-set measures: recall no lower and false accepts no higher at each model's recommended threshold. When no model is installed there is nothing to beat and the offer is made as soon as the candidate exists. Installing happens only when the owner confirms from the interface; it moves the candidate into place, keeps the previous model as `autobot.prev.onnx` with its evaluation, and writes `kind = "openwakeword"`, the `model` path and the recommended `threshold` to `config/autobot.toml`. The interface can revert to the previous model, restoring its file, its evaluation and its threshold, with the same confirmation. Reason: a model installed by a script at four in the morning cannot be judged by the person it will wake, a model that is worse on the owner's own voice must not replace one that is better, and a revert that needs no retraining is what makes trying a candidate cheap.

9. **TS-9. The live monitor turns use into training data.** A screen of the interface connects to the running listener's event stream (`run/listener.sock`) and shows its detections, and the detector scores of each stretch of speech, as they happen. From it the owner can mark a detection right or wrong, and can mark that a wake was missed. For this, the listener keeps a rolling buffer of the last ten seconds of microphone audio in memory and, when the interface asks through the listener's control interface, writes that buffer to a file on the NUC. Each mark saves that audio into the voice dataset as a clip with `source = "monitor"` and the label the mark implies: a right detection is `positive`, a wrong one `negative`, a miss `positive`. These clips join the next training run under TS-6. Reason: after the first session, the misses and false accepts of real use are the most valuable recordings there are, and the only way to catch them is to keep the audio for a moment and let the owner say what it was while it is fresh. Ten seconds and the file hand-off are this spec's choices; the buffer is not written anywhere unless a mark asks for it, so the listener still records nothing on its own.

10. **TS-10. The interface stays a front, and its logic stays drawable-free.** The interface holds no configuration of its own: every setting it changes is written to `config/autobot.toml`, and it reaches the listener only through `python -m autobot.listener.control` as a subprocess and through the event stream on `run/listener.sock`, never by importing it. The recording session, the dataset and its manifest, the evaluation comparison and the install and revert steps live in modules that do no drawing, so they are tested without a terminal; the tests use the committed WAV fixtures and fakes for the audio backend, and open no microphone, speaker or network connection. Reason: `CONVENTIONS.md` "Control layers" and "Testing" already require both, and the studio adds the one new path (the event stream) that the convention did not name.

11. **TS-11. It runs on the NUC only.** The recording session, the live monitor and the install step refuse to run on a machine whose hostname differs from `host.nuc_hostname`, with the one-line message the listener and `make audio-check` already give (WW-9). Reason: the point of the studio is to train on the hardware and in the acoustics the listener will run in; a clip recorded anywhere else would teach the model the wrong room.

12. **TS-12. The existing screens remain.** The Audio, Wake phrase, Service and Host screens keep what they do today (devices, PipeWire defaults, the level meter, threshold, kind, model path, the systemd unit, naming the NUC), and the training pane keeps showing `make train-wakeword`'s log as it runs. The Audio screen's "save the clip" writes into the voice dataset, not into `tests/fixtures/audio/`; exporting a fixture is the separate, deliberate action of TS-5. Reason: the recording machinery is already built there, and the spec builds on it rather than replacing it; the one change is where a saved clip goes, because a committed fixture must not be a side effect of testing the microphone.

13. **TS-13. It looks like a mid-90s bulletin board, in colour, with themes.** The interface is drawn in the manner of a mid-90s bulletin board system: colourful, fun, with framed panels and menus drawn in `curses` line-drawing characters, on every screen, the existing screens of TS-12 included. Its colours come from a theme, and it ships with three, *light*, *dark* and *sun*. Pressing `T` on any screen switches at once to the next theme, cycling in that order and wrapping from *sun* back to *light*, except while a text field (such as the speaker's name of TS-3) has focus, where the key types a letter. Further themes are added later as small, separate additions, since a theme is only a palette. The chosen theme is written to `config/autobot.toml`, so the interface opens in it next time. Reason: the owner will sit with the studio through long recording sessions and wants it to be fun to look at, and people love being able to change colour themes; `curses` keeps TS-1's standard library only, and keeping the theme in the configuration file keeps TS-10.

14. **TS-14. A no-training baseline kind follows the studio.** Once the studio works, an open-vocabulary keyword spotter, sherpa-onnx's KWS, which needs no training, is added as a third kind beside `text_match` and `openwakeword`, as a separate small step after the studio. It runs locally on the NUC like every other detector, and the evaluation of TS-7 scores it on the same held-out real clips as the trained model. Reason: WW-7 makes a new kind one module, and a baseline on the same microphone and the same real set is what tells whether training is paying off.

15. **TS-15. autobot speaks in piper's Joe Medium voice.** The text-to-speech voice, starting with the listener's acknowledgement, is the piper voice `en_US-joe-medium` ("Joe Medium"), run locally like every piper voice, and `make fetch-models` fetches it with the other published models. Reason: of the piper voices the owner has tried, Joe Medium worked best.

## Settled without asking

- **Resolved.** The theme key `T` would collide with typing the speaker's name that TS-3 now asks for, so TS-13 makes it type a letter while a text field has focus and switch themes everywhere else.
- **Derived (TS-10).** The chosen theme persists in `config/autobot.toml`, because the interface holds no configuration of its own.
- **Resolved.** The opening paragraph's rule range now reads TS-1 to TS-15, and TS-2's fresh-install kind stays `text_match`: the baseline kind of TS-14 is added after the studio and does not change where a fresh install starts.

## Changes needed elsewhere

- **docs/context/BRIEF.md.** The interface is a full-screen terminal user interface on the NUC, not a wall UI or PWA; "What it is not" drops the keyboard-and-mouse exclusion or restates it for the TUI.
- **docs/context/CONVENTIONS.md.** "Control layers": the terminal user interface is the application's interface, not a layer below a web application; it may read `run/listener.sock` as well as run control commands. "Layout": `data/voice/` holds the voice dataset and is not committed.
- **docs/context/ARCH.md.** The terminal user interface becomes the application's interface with the studio screens; the wall UI part and seam are withdrawn or deferred; the listener gains the rolling buffer and the score event; the training reads the voice dataset.
- **docs/context/SCHEMA.md.** Add the manifest line of `data/voice/manifest.jsonl`, the score event on the socket, and the real-set fields of `autobot.json`. Add the theme setting of TS-13 to the configuration file's shape.
- **docs/context/GLOSSARY.md.** Add "voice dataset", "recording session", "candidate" and "live monitor"; the "wall UI" and "PWA" entries say they are no longer planned.
- **docs/context/INFRASTRUCTURE.md.** The NUC holds `data/voice/`, which is not committed and never leaves the machine.
- **docs/decisions/0006-\*-ADR.md (new).** Record the decision that the application is a full-screen terminal user interface and that the wake-word model is trained on the owner's recordings with synthetic speech as augmentation, superseding the parts of ADR 0005 named above.
- **docs/specs/active/wake-word-listener-SPEC.md.** Add the rolling buffer and the score event as listener behaviour, and note that the fresh-install kind is `text_match`. The acknowledgement is spoken in piper's `en_US-joe-medium` voice (TS-15).
- **docs/plans/active/headless-wake-word-prototype-PLAN.md.** Mark steps 6 and 7 as superseded by the plan written from this spec.
- **tests/fixtures/audio/README.md.** Fixtures are exported from the voice dataset in the interface, not saved from the Audio screen.
- **AGENTS.md.** The terminal user interface is the application, with the studio screens; the sentence that the application runs with a web interface goes. The list of models `make fetch-models` downloads names the `en_US-joe-medium` piper voice.
- **README.md.** "Running it" opens with `make deploy` and the road from untrained to installed.
- **CHANGELOG.md.** Record the studio under Unreleased.
- **config/autobot.toml.** The text-to-speech voice is `en_US-joe-medium`, and a theme setting holds the interface's current theme.
- **src/autobot/fetch_models.py.** Fetch the piper voice `en_US-joe-medium`.

## Open Questions

None.
