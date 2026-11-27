---
type: decision
title: "autobot — ADR 0006: The full-screen terminal user interface is the application, and the wake-word model is trained on the owner's voice first"
authority: historical
status: active
---

# autobot — ADR 0006: The full-screen terminal user interface is the application, and the wake-word model is trained on the owner's voice first

This records two decisions the owner made on 2026-11-26, set down as rules in the wake-word training studio spec (`docs/specs/active/autobot-wake-word-training-studio-SPEC.md`, TS-1 to TS-15) and built from its plan (`docs/plans/active/wake-word-training-studio-PLAN.md`). The first is about the interface: autobot's application is one full-screen terminal user interface, and no web interface is built. The second is about the wake-word model: it is trained on recordings of the owner's own voice first, with synthetic speech as augmentation. It supersedes parts of ADR 0005, named under Decision. ADR 0005 itself is not edited.

## Context

ADR 0005 chose openWakeWord with a model trained here for `autobot`, from synthetic speech, and left two questions open: how well a model trained only on synthetic speech hears the owner in the room, and whether a larger run would bring false accepts down at a usable recall.

The larger run and three retrains of the classifier on its cached features answered the second question, on 2026-11-26:

- Ten times the synthetic speech barely moved the curve of recall against false accepts. On text-to-speech voices kept out of training, the best of the models still missed about a third of `autobot` utterances (about 33 % false rejects) at one false accept an hour on openWakeWord's validation audio.
- Leaving the hard negatives that sound the same as the phrase out of the training helped only at low false-accept rates.
- The misses were concentrated where synthetic speech is weakest: accented voices, the bare word with nothing before it, and slow renderings.
- Every one of those numbers is for synthetic voices. openWakeWord's validation audio is too short to rank models at one false accept an hour, and none of it is this room, this microphone or this voice.

So the first question could not be answered by more synthesis, and the model on the NUC was, as ADR 0005 already said of the first one, not good enough to leave on in a house.

On the interface, the intake described a screen on the wall: a web interface, probably a progressive web application shown full-screen in Chrome, driven by voice. `CONVENTIONS.md` therefore placed the terminal user interface in a control layer below an application with a web interface. No part of that web interface was ever built. What was built, and what the owner sat in front of, was the terminal user interface. Collecting recordings needs an interface the owner operates by hand, on the NUC, for long sessions.

## Decision

**1. The application is one full-screen terminal user interface.**

- autobot's user-facing application is the terminal user interface under `src/autobot/tui/`, drawn with the standard library's `curses`, run full screen in a terminal on the NUC and opened by `make deploy` (TS-1). It is not a control layer below some other application.
- No web interface, progressive web application or wall UI is built, and no front-end `make` target exists other than the one that opens this interface. Later jobs of the application appear as further screens of the same interface.
- Its first job is the wake-word training studio: a scripted recording session, training, an evaluation on real recordings, an explicit install, and a live monitor that turns use into training data.
- It stays a front (TS-10): it holds no configuration of its own, reaches the listener only through `python -m autobot.listener.control` and the event stream on `run/listener.sock`, and keeps its logic in modules that do no drawing.
- It is drawn in the manner of a mid-90s bulletin board, with three colour themes, *light*, *dark* and *sun*, switched with `T` and kept in `[tui] theme` (TS-13).

**2. The wake-word model is trained on the owner's recordings first.**

- The owner's voice is recorded on the NUC, through its microphone, in the room the listener runs in, into the voice dataset `data/voice/` with one manifest, `manifest.jsonl` (TS-3, TS-4). The recordings are never committed and never leave the NUC (TS-5).
- `make train-wakeword` trains on those recordings, and the synthetic speech of ADR 0005 is augmentation beside them (TS-6). With no recordings it still trains, on synthetic speech alone, and its evaluation says so.
- A stable share of the recordings is held out and never trained on: one script prompt in five, one unprompted clip in five, and the last fifth of every ambient recording. A model is judged on that held-out real set, reported apart from every synthetic figure (TS-7).
- A training run writes a candidate and installs nothing. Installing is the owner's decision in the interface, after a comparison with the installed model on the same held-out recordings, and it can be reverted (TS-8).
- A fresh install starts on the `text_match` kind, and the interface reports the listener as *untrained* until a candidate has been installed (TS-2).
- The acknowledgement is spoken in piper's Joe Medium voice, `en_US-joe-medium` (TS-15).

**What this supersedes in ADR 0005.**

- *Decision, "openWakeWord":* the classifier is no longer trained "from synthetic speech" alone. The engine, the feature extractor and the method stand.
- *Decision, "The `autobot` model is trained on the NUC":* the training set is the owner's recordings with synthetic clips beside them; the evaluation that decides is the one on held-out real recordings, not the one on voices kept out of the synthesis; and the result is written to `models/wakeword/candidate.onnx` with `candidate.json`, not to `models/wakeword/autobot.onnx`. The model reaches that path only through an install the owner confirms.
- *The configured kind and threshold:* ADR 0005 and the prototype plan made `openwakeword`, at threshold 0.9, the configured kind once the model existed. The kind on a fresh install is `text_match`, at 0.5, and `openwakeword` and its threshold are written to `config/autobot.toml` by the install.
- *Consequences, "`models/wakeword/autobot.json` holds the evaluation of whichever model is installed":* a model file at that path counts as installed only when the configuration names `kind = "openwakeword"`. The synthetic-only model the earlier runs left there is a leftover: kept, not counted as installed, and not compared.
- *Open Questions, the first two:* the larger run did not bring the model to a usable recall, and the next step ADR 0005 named, real recordings of the owner, is this decision. How well the model hears the owner is now measured by the held-out real set rather than left to use alone.

Everything else in ADR 0005 stands: openWakeWord's feature models run with `onnxruntime` directly, the wider speech-gate margins, `text_match` as the bootstrap and fallback kind, a kind as the detector that implements a phrase, and detection by containment.

It also settles a question ADR 0001 and ADR 0004 left open, in what language the wall UI is written: none, because no wall UI is built.

## Alternatives

- **More synthetic speech, or a larger network.** Rejected: ten times the speech did not move the curve, and the misses are in deliveries a text-to-speech voice does not produce.
- **Keep the synthetic-only model on and tune its threshold by ear.** Rejected: no threshold gave both a usable recall and one false accept an hour, and there was no measurement in this room to tune against.
- **Label real audio by transcribing it.** Rejected: a transcription step can mislabel exactly the utterances that matter. A scripted session labels each clip by the prompt that asked for it.
- **Let the training install its result, as before.** Rejected: a model installed by a script at four in the morning cannot be judged by the person it will wake, and a model that is worse on the owner's voice must not replace a better one.
- **Build the web interface now, and put the recording studio in it.** Rejected: the owner decided that interface complexity beyond a full-screen terminal is waste for now. The recording machinery already existed in the terminal user interface's Audio screen.
- **A speaker-verification stage** (openWakeWord's custom verifier trained on the owner's clips). Not chosen here: it presupposes a model that already detects the phrase. TS-7 leaves it to a later spec.

## Consequences

- `CONVENTIONS.md` "Control layers" changes: the terminal user interface is the application's interface, and it may read `run/listener.sock` as well as run control commands. The `Makefile` remains the runtime control layer.
- The NUC holds `data/voice/`, which is git-ignored with the rest of `data/`. A fresh checkout has no recordings and no trained model, and starts *untrained*.
- The listener gains what the live monitor needs (TS-9): a `score` event on the socket for every stretch of speech, the audio of the last five stretches and of the last ten seconds held in memory, and a `snapshot` control command that writes one of them to a file when a mark asks. It still records nothing on its own.
- A training run no longer changes what the listener runs. `config/autobot.toml` is written by the interface alone.
- The training leaves the near-homophone hard negatives out by default, the setting the retrains favoured at low false-accept rates; `--no-exclude-near-homophones` trains on them again.
- A verdict between two models needs a held-out set of at least twenty positives, twenty negatives and twelve minutes of ambient audio. One complete session holds out about a dozen positives, so the first installs rest on the owner's own judgment of the figures.
- A third kind, `sherpa_kws`, an open-vocabulary keyword spotter that needs no training, follows the studio as a baseline to measure the trained model against (TS-14). Its engine exists, and the Candidate screen shows its figures on the held-out real set beside the trained model's, at its default threshold.
- The brief's wall-mounted, voice-driven display is withdrawn for now. The assistant is still voice-first: what the owner says to it and hears back is unchanged.
- At the time of writing the studio has been checked on the NUC with scratch data and a virtual audio source, not yet with the owner's voice: no session has been recorded, `data/voice/` does not exist, and no candidate has been trained on real recordings.

## Open Questions

- How well does a model trained on one session of the owner's voice hear him, and how many sessions, speakers and monitor clips does it take? Only recording and the held-out figures answer this.
- Is a speaker-verification stage needed once the trained model detects the phrase? TS-7 leaves it to a later spec.
- Should the split hold out whole speakers once a second person has recorded a session? TS-6 defers it until there are two speakers.
- Does the interface start by itself on the NUC's display at login? TS-1 leaves a kiosk start to the Service screen, later.
- The recordings never leave the NUC, so nothing backs them up. Is a second copy on the machine enough?
