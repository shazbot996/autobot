---
type: readme
title: "autobot"
authority: descriptive
status: active
provenance:
  - run: schiele/0015
    at: 2026-11-25T22:58:12.732Z
    mode: revise
  - written: schiele/finish-the-agents-and-the-agent-tree-plan
    at: 2026-11-27T19:13:08Z
    mode: revise
---

# autobot

## What it is, in a paragraph a stranger can follow

autobot is a personal, always-on voice assistant for one house. A local process listens to a microphone around the clock for configurable wake phrases, such as a home-made "hey google". When it hears one, it transcribes what follows with a local speech-to-text model, runs the words through a set of plain rules first, and only then, when a rule says so, opens an interactive session with one of several persistent AI agents built on Gemini ADK and served from Google Cloud in the `us-east5` region. Each agent is reached by its own wake phrase and keeps its memory on the local machine, so that, for example, a list-keeping agent remembers its lists between conversations. The assistant answers out loud with a local text-to-speech voice that the owner can choose. It is set up, and taught the owner's voice, from a full-screen terminal interface on the same machine. Speech recognition, wake-word detection and speech synthesis never leave the machine; only the agent reasoning goes to the cloud. It is a platform for voice-first applications, and no application exists yet: the framework comes first. That framework is the local process that listens, responds and catches wake words, and captures commands through a rules-based engine that leads into agent activation, in the manner of Google's Dialogflow. Simple question-and-answer use comes first once the framework works.

## Running it

`make launch` opens autobot: a full-screen terminal user interface on the Intel NUC. It is the application, and its first job is a training studio that takes the wake-word listener from *untrained* to *installed* on the owner's own voice. Its main screen always says where the listener stands: *untrained*, *recording*, *candidate ready*, *installed* or *listener down*. The road, in the order of its menu:

1. **Recording session.** Give your name, then say what each prompt shows, one at a time: the wake phrase `autobot` bare, with "hey" and "yo", inside a sentence, slow, fast, quiet, raised, near the microphone and far from it; then words that sound like it, ordinary sentences, and five minutes of the room with nobody speaking. It is meant to fit in about half an hour, can be left at any prompt and resumes where it stopped. An optional sixty-minute room recording follows. If the microphone delivers only silence (its own mute button, say), the screen shows NO SIGNAL and asks before it records. The clips go into `data/voice/`, which is never committed and never leaves the NUC.
2. **Train the model.** Runs `make train-wakeword` and shows its log. It trains on the recordings, with synthetic speech beside them, and writes a candidate. It installs nothing.
3. **Candidate.** Shows how the candidate does on recordings held out of the training, beside the installed model and beside the untrained `sherpa_kws` baseline, and offers to install it. Installing writes the model and its threshold to `config/autobot.toml`, keeps the model it replaces, and can be reverted.
4. **Live monitor.** Shows the listener's scores as you speak. Mark a detection right or wrong, or mark a missed wake, and that utterance joins the dataset for the next training run.

The studio is built and tested, and has been run on the NUC with scratch data and a virtual audio source. It has not yet been used with a real voice: no session has been recorded and no model has been trained on real recordings. Until one is installed the listener runs on the transcribing `text_match` detector.

Press Tab on the main menu to open autobot's face, and Tab or Esc to come back. On the face, say the wake phrase and then a command, and the command goes to an agent. The first commands to try are "time" and "introduce", which need nothing but this machine. "weather" and "forecast" work once the weather agent has a zip code (set it in the **Agent editor**) and its API key is in Secret Manager (the **Google Cloud** screen). "I have a question, ..." sends what follows to the Gemini model chosen on the Google Cloud screen and reads the answer aloud. "camera" opens the outside cameras in Firefox, and "echo" records your voice and plays it back. Say "cancel" at any time to go back a step. A command is heard only through a `text_match` wake phrase, because no other kind transcribes speech.

Behind the interface is the always-on listener. It listens to the microphone around the clock for any utterance containing the wake phrase ("hey autobot", "yo autobot") and answers aloud, "Yes?", in piper's Joe Medium voice. The face routes what is said after the wake phrase to an agent; the rules layer that validates a query first is not built. Everything runs on the NUC only, on Ubuntu with PipeWire, and needs Python 3.12 or later.

From a fresh checkout on the NUC:

1. `make setup` creates `.venv` and installs the package with its dev extras.
2. `make fetch-models` downloads the model files into `models/`, which is not committed: the Silero VAD model, the faster-whisper `tiny.en` model, the piper voice for the acknowledgement (`en_US-joe-medium`), openWakeWord's feature models and the sherpa-onnx keyword-spotting model. It is the only command that downloads anything; the listener and the tests never do, and name the file they miss.
3. `make launch` opens the interface. Besides the studio, its Host screen names this machine as the NUC, its Audio screen picks the microphone and speaker and shows a level meter, its Wake phrase screen sets the threshold and the detector kind, its Service screen installs and enables the systemd unit so the listener starts at boot, and its Dataset screen lists every recorded clip. `T` switches between the three colour themes, *light*, *dark* and *sun*.
4. `make audio-check` lists the audio devices, records two seconds from the microphone, prints the level and plays the recording back. It refuses to run on any machine but the NUC.
5. `make up` starts the listener, `make down` stops it, `make status` shows whether it runs with its uptime, CPU and memory, and `make logs` prints the end of its log (`make logs FOLLOW=1` keeps following it).
6. `make train-wakeword` is what the studio's training runs, and can be run from a shell. It writes `models/wakeword/candidate.onnx` with its evaluation in `candidate.json`, and leaves the installed model and `config/autobot.toml` alone. The first run takes hours on the NUC's CPU, installs the training libraries into `.venv` (the CPU build of `torch`) and fetches the training voices and data sets; after that it downloads nothing, and a run takes about half an hour once the synthetic speech is cached. It is resumable: run it again and it carries on from what it has cached under `models/wakeword/work/`. `SCALE` (how much speech to synthesise), `STEPS`, `THREADS` and `ACAV_GB` (how much of openWakeWord's negative data to use) change the run. With no recordings it trains on synthetic speech alone and says so.

A wake phrase has one of three detector kinds. `text_match` transcribes speech and looks for the phrase; it needs no model and is what a fresh install runs. `openwakeword` is the trained model the studio installs, cheap enough to leave on all day. `sherpa_kws` is a keyword spotter that needs no training, kept as a baseline to measure the trained model against.

A bare `make` prints every target. `make test`, `make lint` and `make typecheck` run the checks (`pytest`, `ruff`, `mypy`); the tests use recorded audio fixtures and open no microphone, speaker or network connection.

Everything is configured in one file, `config/autobot.toml`; the interface is a front for it. Each wake the listener hears is written as one JSON line to `run/listener.log` and to the Unix socket `run/listener.sock`, which is where the next part to be built will pick it up, and where the live monitor reads the listener's scores.

There is no web interface. Later parts of autobot appear as further screens of the same terminal interface.

## Where things are

- `README.md`, `CHANGELOG.md`, `AGENTS.md`: the Root row. What the project is, what has changed, and how coding agents must work here.
- `docs/context/`: the Core row. `BRIEF.md` (what autobot is and is not), `GLOSSARY.md` (its vocabulary), `CONVENTIONS.md` (house rules for the code), `CONSTRAINTS.md` (the rules that must never break: local-only speech processing), `SCHEMA.md` (shared data shapes), `ARCH.md` (the parts and the seams) and `INFRASTRUCTURE.md` (the NUC and the Google Cloud project).
- `docs/decisions/`: Architectural Decision Records. `0001` proposes Python, `0002` records Gemini ADK with locally kept state, `0003` proposes faster-whisper and piper-tts as the local speech stack, `0004` accepts `0001` and `0003` and names `mypy` as the type checker, `0005` records openWakeWord with a custom-trained `autobot` model as the wake-word engine, and `0006` records that the full-screen terminal user interface is the application and that the model is trained on the owner's voice first.
- `docs/specs/active/`: the behaviour each feature must have. Specs exist for the wake-word listener and the wake-word training studio, and stubs for rules-first routing and persistent agent sessions.
- `docs/plans/active/` and `docs/plans/archive/`: implementation plans. `headless-wake-word-prototype-PLAN.md` is the plan the listener was built from, and `wake-word-training-studio-PLAN.md` the plan the studio was built from; it supersedes steps 6 and 7 of the first.
- `src/autobot/`: the Python package. `listener/` is the always-on listener (audio, the speech gate, the detectors under `engines/`, events, the acknowledgement, runtime control); `wakeword/` holds the voice dataset, the recording script, the training, the evaluation on real recordings and the install; `tui/` is the terminal user interface, which is the application; `config.py` loads the configuration; `fetch_models.py` is `make fetch-models`; `audio_check.py` is `make audio-check`.
- `tests/`: the tests, mirroring the package, with the audio fixtures in `tests/fixtures/audio/`.
- `config/autobot.toml`: the one configuration file.
- `deploy/autobot-listener.service`: the template of the systemd user unit.
- `Makefile`, `pyproject.toml`: the runtime control layer and the project definition.
- `models/` and `run/`: model files, with the training's data and caches under `models/wakeword/`, and runtime state (pid file, log, socket). Neither is committed.
- `data/voice/`: the voice dataset, the owner's recordings with their manifest `manifest.jsonl`. It is created by the first recording, is never committed, and never leaves the NUC.
- `docs/remediations/`, `docs/publish/`, `docs/attachments/`, `docs/images/`: security remediations, external publications, and attached files. Empty.
- `.cr2/`: CR2's own project configuration.

## Open Questions

- The trained `autobot` wake-word model is not committed and takes hours to train. Should it be kept somewhere a fresh checkout can copy it from?
- Which Google Cloud project id and region will host the Gemini agents? Q4 says a project will be created in the `cambiumplatform.com` organization but gives no id.
  The region is `us-east5`. No project exists yet; an id will be made up and the project created to match it.
