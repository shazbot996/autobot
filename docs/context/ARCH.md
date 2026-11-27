---
type: arch
title: "autobot — Architecture"
authority: descriptive
status: active
provenance:
  - run: schiele/0019
    at: 2026-11-25T23:11:43.275Z
    mode: revise
  - run: schiele/0103
    at: 2026-11-27T17:25:51.252Z
    mode: revise
---

# autobot — Architecture

The architecture document is the map of autobot: the parts, how speech and data move between them, and the seams along which its specs divide. These parts are built and are described here from the code:

- the listener;
- the wake-word training with its voice dataset;
- the first form of the rules layer, the routing of a command after the wake;
- autobot's side of Google Cloud, which so far configures and checks the project and nothing more;
- the application's interface, a full-screen terminal user interface whose first job is the wake-word training studio, and which also holds the face.

The `Makefile` beside them is the runtime control layer. The other parts are still the design the Smart Start intake and the agent specs describe, and each says so.

autobot is not one large process. It has two layers: a data plane that handles live audio, and a control plane above it that manages the rules. The owner reaches both by hand through one interface, the terminal user interface, and starts and stops the listener through the `Makefile` or that interface. The system supports the Ubuntu NUC alone.

The studio is built and its tests pass, and it has been run on the NUC with scratch data and a virtual audio source. It has not yet been used with the owner's voice: no session has been recorded, `data/voice/` does not exist yet, and no model has been trained on real recordings.

## What the parts are

- **Listener (data plane). Built.** The always-on local process that reads the microphone on the NUC and detects configurable wake phrases. It is the Python package `src/autobot/listener/`, run as `python -m autobot.listener`. It does the following:
  - reads 16 kHz mono audio in 80 ms frames (`audio.py`);
  - passes the frames through a speech gate built on Silero VAD, so that silence never reaches a detector (`vad.py`);
  - feeds the voiced frames to one detector per configured wake phrase (`detector.py`, `engines/`);
  - publishes each wake as a `WakeEvent`, each stretch's detector scores as `ScoreEvent`s, and each transcription a detector makes as a `TranscriptEvent` (`events.py`);
  - speaks an acknowledgement (`ack.py`);
  - on request, stops listening for a stated time (a hold), as it does while its acknowledgement plays.

  For the live monitor it also holds, in memory only, the audio of the last five stretches of speech, each with its pre-roll, and the last ten seconds of microphone audio (`RecentAudio` in `service.py`). It writes one of them to a file only when a control command asks. It is local for cost: always-on listening must cost nothing per second (`CN-1`).
- **Wake-word engines. Built.** A detector implements one wake phrase behind the `Detector` protocol: `feed` takes the next frame of a stretch of speech, `end_of_speech` says the stretch has ended, and `reset` makes the detector forget what it has buffered. Three kinds exist, one module each under `src/autobot/listener/engines/`.
  - **`text_match`** is the kind a fresh install starts on, and the fallback. It transcribes each stretch of speech with faster-whisper (`tiny.en`) and fires when the transcript contains the phrase. It needs no trained model, and it is the only kind whose transcripts are published.
  - **`openwakeword`** is the engine for 24x7 use. A small model trained here for the phrase `autobot` scores every frame it is fed, on openWakeWord's shared feature extractor. The detector fires when the score reaches the phrase's `threshold`, then ignores the next two seconds so that one utterance is one wake (ADR 0005). Its three ONNX models are run with `onnxruntime` on the CPU, one thread each; the `openwakeword` Python package is not a dependency. It becomes the configured kind when the owner installs a trained model from the interface.
  - **`sherpa_kws`** is the no-training baseline (TS-14): sherpa-onnx's open-vocabulary keyword spotter, a small streaming recogniser fetched into `models/sherpa-kws/`, with the configured phrase spelled in the model's own word pieces. It reports that it heard the phrase, not how sure it was. It runs in the listener as an engine. `evaluate.score_kind` runs it, through the listener's own frame path, over the same held-out real recordings as the trained model. The Candidate screen shows its figures in a last column, at its default threshold only, since it gives no score to sweep.
- **Wake-word training and the voice dataset. Built.** `src/autobot/wakeword/`.
  - **The voice dataset** (`dataset.py`) is the owner's recordings under `data/voice/` with one manifest, `manifest.jsonl`. It decides, and nothing else does, which clips are trained on and which are held out.
  - **The recording script** (`script.py`) is the fixed list of prompts a session asks for.
  - **The training** (`train.py`, run by `make train-wakeword`) works in three steps. It synthesises the phrase, words that sound like it and ordinary speech with piper-tts in 20 voices, adds reverb and noise, and computes openWakeWord features. It cuts the training side of the voice dataset into the same windows. It then trains the classifier on both and on openWakeWord's general negative features, with half of every batch of positives and a third of every batch of negatives real when recordings exist. With no recordings it trains on synthetic speech alone, and its evaluation says so.
  - **The evaluation** (`evaluate.py`) scores the classifier on 7 synthetic voices it never trained on and, separately, on the held-out real recordings. The run then writes a candidate, `models/wakeword/candidate.onnx` with `candidate.json`. It installs nothing.
  - **The install** (`install.py`) moves a candidate into place, keeps the model it replaces as `<name>.prev.onnx`, writes `kind`, `model` and `threshold` to the configuration file, and can revert.
- **Speech-to-text (STT).** Local transcription with faster-whisper (ADR 0003, accepted by ADR 0004). It is not built as a part of its own. faster-whisper runs today inside the `text_match` detector, whose transcripts the listener publishes on its socket. The face reads its commands from those transcripts. The training also uses faster-whisper, to find where the phrase ends in a recorded sentence. Never a cloud service (`CN-1`).
- **Rules layer (control plane). First form built.** Classic rules that validate a transcript, decide which kind of AI call it feeds, route it to an agent, or answer locally with low latency. This is the control plane above the listener, comparable to Dialogflow in Google Cloud: it is where the rules are managed. Rules accumulate over time into conversational pathways toward the planned agent tools. This is the "rules-first" part of rules-first AI agents.
  - **What is built** is routing. A rule is a `[[routes]]` entry of `config/autobot.toml`, with a `command` and the `agent` it routes to. `src/autobot/routing.py` matches a transcript against the rules in file order, by the listener's containment rule, and the first match wins. It does no drawing and calls nothing outside the machine.
  - **Where it runs.** The rules run inside the terminal user interface's process: the face's router (`src/autobot/tui/router.py`) applies them to the transcripts it reads from the listener's socket, and the Agent routing screen lists, edits and tries them.
  - **What is not built.** Validation, local answers and the choice of AI call.
- **Agent host. Not built.** The server-side part that defines persistent agents with Gemini ADK, opens interactive sessions with them against Gemini models in the owner's Google Cloud project, and persists each agent's state and memory locally. Creating new agents that serve from configured wake phrases and saving their state locally is a core purpose of this part (ADR 0002).
  - **The one agent built** is the echo, and it is not hosted here. It is an experience of the face (`src/autobot/tui/echo.py`), local, with no model behind it.
  - **The router's table of agents** (`AGENTS` in `router.py`) maps the agent name `echo` to that experience.
  - **What is specified but not built.** The agents spec defines `question`, `weather`, `time`, `camera` and `introduce`, the agent tree, the Agent editor, and the run and configuration modes switched by Tab. No `src/autobot/agents/` exists.
- **Text-to-speech (TTS).** Local synthesis in a configurable voice, with piper-tts (ADR 0003, accepted by ADR 0004). It has two uses at run time, both in the voice `[ack].voice` names, piper's Joe Medium (`en_US-joe-medium`):
  - **The listener's acknowledgement.** The text in `[ack].text` is rendered once, when the listener starts, and that clip is played on every wake.
  - **The face's speech.** The echo's spoken line is rendered by the interface, in the background, once per visit to the face. It is played while the listener holds its ears.

  Speaking an agent's response is not built. Never a cloud service (`CN-2`).
- **Terminal user interface (the application). Built.** A full-screen `curses` interface, `src/autobot/tui/`, opened by `make launch`. It is autobot's application, not a control panel beside one (TS-1, ADR 0006).
  - **A front.** It holds no configuration of its own, writes every setting to `config/autobot.toml`, and reaches the listener only through its control commands and its event stream.
  - **The main screen** always shows where the listener stands, as one of *untrained*, *recording*, *candidate ready*, *installed* or *listener down* (`state.py`), with what the voice dataset holds.
  - **The studio screens** come first in its menu:
    - **Recording session**: one prompt at a time, resumable.
    - **Train the model**: the log of `make train-wakeword` as it runs.
    - **Candidate**: the candidate's figures on the held-out recordings beside the installed model's, with install and revert.
    - **Live monitor**: the listener's scores and wakes as they happen, and marks that save an utterance into the dataset.
    - **Dataset**: every clip, to play, delete, or export one as a test fixture.
  - **The settings screens** follow: Audio (devices, PipeWire's default microphone and speaker, a level meter, recording a test clip into the dataset), Wake phrase (threshold, kind, model path, playing the acknowledgement), Service (the systemd unit) and Host (naming this machine as the NUC).
  - **The last three entries** are reached from the same menu:
    - **Face** is autobot in use: the ticker of the listener's transcripts, the eyes and their reactions, a microphone meter, the mouth meter, a summary panel and a detail panel. It runs one session at a time, from a wake, through the router, to an agent's experience. Its logic is in `face.py`, `face_ticker.py`, `face_reactions.py`, `face_meters.py`, `face_voice.py`, `router.py` and `echo.py`, and the drawing is in `screens/face.py`.
    - **Agent routing** lists, edits and tries the routing rules.
    - **Google Cloud** walks the owner through connecting autobot to the Google Cloud project as its own service account, in eight steps (Project, APIs, Service account, Key, Secret Manager, Gemini, Rate limits, Cloud Storage). It shows the `gcloud` commands that fix what is missing and writes them as scripts under `run/gcp/`, which only the owner runs.
  - **Drawing.** It is drawn like a mid-90s bulletin board in one of three themes, *light*, *dark* and *sun*; `T` switches.
  - **NUC only.** The recording session, the live monitor, the install, and the face's microphone meter, speech and echo refuse to run on any machine but the NUC.
- **Google Cloud connection. Built for configuration only.** `src/autobot/gcp/` is autobot's side of Google Cloud. It does no drawing, and the Google Cloud screen draws it. It has six modules:
  - **`needs`** is the APIs, roles, permissions and expected secrets, as data.
  - **`commands`** is the `gcloud` commands, as text that nothing in autobot runs.
  - **`keyfile`** checks that the key file is safe: git-ignored, `0600`, and owned by the user.
  - **`client`** is the connection as the service account.
  - **`checks`** is the eight steps and the state of each.
  - **`secrets`** stores a pasted secret in Secret Manager.

  `client.connect` points Application Default Credentials at the service account's key and refuses any identity but `[gcp] service_account`. `RestGoogleCloud` calls the REST endpoints of the needed APIs directly and refuses any other host. No agent uses this connection yet.
- **Local store.** Where wake phrase configuration, routing rules, agent definitions and agent memory live on the local machine. The built parts are:
  - `config/autobot.toml`, the one configuration file, which also holds the `[[routes]]` rules and the `[gcp]` table;
  - `models/`, the model files;
  - `data/voice/`, the voice dataset.

  Neither `models/` nor `data/` is committed, and the recordings never leave the NUC. The service account's key sits under the git-ignored `secrets/`. The form of the rest is not yet decided; see `SCHEMA.md`.
- **Gemini agents (external). Not built.** The Gemini models in the owner's Google Cloud project that the ADK calls for agent reasoning: Vertex AI in `[gcp] region`, model `[gcp] gemini_model`, called as the service account. The Google Cloud project is the only part that is not local. Today it is reached only by the Google Cloud screen's checks and by the store of a pasted secret.
- **Make (runtime control layer). Built.** A bare `make` prints a summary of every target. `make launch` is the only front-end target. The targets are:
  - `make up`, `make down`, `make status` and `make logs`, which run `python -m autobot.listener.control`;
  - `make launch`, which opens the application;
  - `make audio-check`, which checks the NUC's audio hardware;
  - `make test`, `make lint` and `make typecheck`, the checks;
  - `make setup`, which creates the environment;
  - `make fetch-models`, which downloads the published model files into `models/` and is the only target that downloads;
  - `make train-wakeword`, which trains a wake-word candidate.

No wall UI, web interface or PWA is a part. The intake described a voice-driven screen on the wall; ADR 0006 withdrew it, and what the application shows, it shows in the terminal user interface.

## How they fit

What the listener does, on the NUC:

1. The listener runs continuously, as the systemd user unit `autobot-listener.service`. It reads the microphone through PipeWire and holds every model it needs as a local file.
2. Each 80 ms frame goes through the speech gate. Silence is dropped. A stretch of speech is released to the detectors with a few frames of lead-in so that the first syllable is not clipped. One VAD scores each frame once; a detector that needs wider margins is fed through a gate of its own, driven by the same score. The `openwakeword` detector asks for 0.96 s before the speech and 0.8 s after it, because its model wants to have heard what came before the phrase and scores highest just after the phrase ends; `text_match` and `sherpa_kws` take the default 0.24 s and 0.48 s.
3. The detector for each configured wake phrase is fed the stretch. When one hears its phrase, anywhere in the utterance, it returns a `WakeEvent`.
4. Each stretch gets an identifier, a counter that starts at 1 with each run of the listener. A `text_match` detector's transcriptions are published to the readers of `run/listener.sock` as `transcript` events as they are made. When the stretch ends, or just before the first wake inside it, the listener publishes one `ScoreEvent` per configured phrase: what that detector made of the stretch.
5. The listener writes each wake as one JSON line to its log and to every reader connected to `run/listener.sock`, after the score events of its stretch.
6. When the event's target is `ack`, the listener plays the acknowledgement ("Yes?") on the speaker. While it plays, and for a moment after, the microphone's audio is discarded and the detectors are reset, so the listener does not wake itself. A hold asked for with `control hold` does the same for the time it names.
7. Meanwhile the listener keeps the last five scored stretches, each with its pre-roll and at most its last 30 seconds, and the last ten seconds of microphone frames, in memory. It writes neither anywhere on its own.

Nothing in those steps leaves the machine. The listener opens no network connection:

- every model is a local file under `models/`;
- a missing model is an error rather than a download (`make fetch-models` is the separate command that downloads them, and the listener never runs it);
- the process sets its offline environment (`autobot.listener.models.enforce_offline`) before any speech library is imported.

That last step matters for `CN-1`: `onnxruntime`, which runs the VAD, the wake-word model and piper, otherwise uploads usage telemetry a few seconds after it is imported, and `ORT_DISABLE_TELEMETRY=1` is what switches that off.

The studio is the loop that makes the listener hear the owner. It is driven from the terminal user interface:

1. On a fresh install the phrase's kind is `text_match` and the main screen reads *untrained*. A model file at the phrase's model path that the configuration does not name as `openwakeword` is a leftover: kept, not counted as installed, and not compared.
2. A recording session asks for the speaker's name and shows the script one prompt at a time:
   - the phrase bare, with "hey" and "yo", inside a sentence, slow, fast, quiet, raised, near and far;
   - words that sound like it, and ordinary sentences;
   - five one-minute prompts of the room with nobody speaking.

   An optional sixty-minute room recording follows. Each take is written to `data/voice/` with a manifest line the moment it is accepted, so a session resumes at the first prompt not yet answered.
3. `make train-wakeword`, started from the interface or from a shell, trains on the dataset and the synthetic speech and writes a candidate. It touches neither the installed model nor `config/autobot.toml`.
4. The Candidate screen scores the candidate and the installed model on the current held-out recordings and compares them.
   - **A verdict.** With a held-out set of at least twenty positives, twenty negatives and twelve minutes of ambient audio, it gives a verdict, and offers the install only when the candidate is at least as good.
   - **The owner's judgment.** Below that size it shows the figures and offers an install on the owner's own judgment. With nothing installed there is nothing to beat.
   - **The threshold.** The threshold an install writes is the one recommended in that scoring, on the held-out set as it is now. The evaluation file's is used only when nothing is held out.
   - **The baseline.** A last column shows the `sherpa_kws` baseline on the same recordings. It is there to be read and takes no part in the verdict.
5. An install the owner confirms moves the candidate to the phrase's model path, keeps what was there as `<name>.prev.onnx`, and writes `kind = "openwakeword"`, the `model` path and the recommended `threshold` to the configuration file. A revert swaps the two back. The interface then offers to restart a running listener, which loads its model when it starts.
6. The Live monitor reads `run/listener.sock`. The owner marks a detection right or wrong, or marks that a wake was missed. The interface then runs `control snapshot` for that stretch, or for the last ten seconds, and adds the audio to the dataset with `source = "monitor"`. Those clips join the next training run.

The recordings never leave the NUC in any of those steps (TS-5). The one copy that leaves `data/voice/` is a fixture the owner exports, clip by clip, into `tests/fixtures/audio/`.

The face is the first part of the full exchange, and runs inside the interface:

1. The face reads `run/listener.sock`. Each transcript scrolls along the ticker and each wake pops in colour and plays the eyes' *wake* reaction. A wake while nothing runs begins a session; a wake during one reaches only the ticker and the eyes.
2. The router listens for up to ten seconds for a command. It reads the words after the wake phrase in the waking transcript, then each later transcript, and tries them against the `[[routes]]` rules. A transcript no rule matches is one line in the detail panel. With no `text_match` wake phrase there are no words, and the session ends at once with its reason.
3. The first match names its agent, and the router starts that agent's experience from its table and passes it every frame until it ends.
4. The echo, the one agent, speaks its line and records five seconds through the interface's own capture of `[audio] input_device`. It then plays the recording back on `[audio] output_device`. Around each sound it runs `control hold` so that the listener does not wake on it. The recording is held in memory only and dropped when the echo ends.

The rest of the exchange the intake describes is not built:

1. STT transcribes the captured query locally.
2. The rules layer validates the transcript. If a rule can answer locally, it does so at once, with low latency. Otherwise a rule decides which kind of AI call the query feeds and routes it to an agent.
3. The agent host opens or continues a session with that agent through Gemini ADK. The reasoning runs against Gemini in Google Cloud; the agent reads and writes its memory in the local store.
4. The response comes back as text. TTS speaks it in the configured voice.

Only the agent reasoning leaves the machine, and only as text. Audio never does. Today nothing leaves it but the Google Cloud screen's calls. That screen connects, in configuration only, as the service account, to the REST endpoints of the APIs it needs and to Google's sign-in host. It sends nothing but its checks and a secret the owner pastes in. It never runs a command that changes the project: the owner runs those, in his own shell, by hand or from the scripts under `run/gcp/`.

Runtime control stands beside this flow, not in it. `python -m autobot.listener.control` has two modes behind one set of commands:

- **Without the systemd unit**, `make up` starts the listener in the background with its pid in `run/listener.pid`.
- **With the unit installed**, which it is on the NUC, the same commands drive `systemctl --user`.

Either way the listener's output goes to `run/listener.log`. The unit is rendered from the template `deploy/autobot-listener.service` into `~/.config/systemd/user/`. It restarts the listener after a fault that may pass (exit status 1, such as the audio device not being ready), and leaves it down after one that will not (exit status 2, such as a bad configuration or a missing model). With the unit enabled and lingering on for the user, the listener starts at boot with nobody logged in. The terminal user interface drives the listener only by running those commands as subprocesses and by reading its socket; it does not import the listener.

## The seams

1. **Wake-word listener**: the microphone stream, wake phrase detection, and the hand-off to what comes next. This is the seam between the data plane and the control plane. Governed by [wake-word-listener-SPEC.md](../specs/active/wake-word-listener-SPEC.md).
   - **The socket.** Its built form is `run/listener.sock`: a Unix domain stream socket, mode 0600, that the listener binds while it runs. Any number of readers may connect. Each event is broadcast as one JSON line to the readers connected at that moment, with no backlog and no replay. A reader that falls behind is dropped rather than allowed to block the audio loop.
   - **The events.** Three travel on it. `wake` also goes to the log. `score` and `transcript` go to the socket only, so the log holds what it always held, the history of the wakes. For each stretch, transcripts come first, then scores, then wakes.
   - **Not built.** The capture of the speech after the wake, and its hand-off to STT.
2. **Detector kinds**: inside the listener, the `Detector` protocol in `src/autobot/listener/detector.py`. A new kind of wake phrase is one new module under `engines/`, found by its name, plus its name in the list of valid kinds in `src/autobot/config.py`.
3. **Rules-first routing**: the transcript in, and either a local response or a validated, routed query out. Its first form is `autobot.routing.match_route`, which takes a transcript and the `[[routes]]` rules and returns the first rule that matches, or none. The face's router and the Agent routing screen both call it, so a sentence tried on the screen goes where the face would send it. A new agent is a name in `ROUTE_AGENTS` in `src/autobot/config.py` and an entry in the router's `AGENTS` table. Governed by [rules-first-routing-SPEC.md](../specs/active/rules-first-routing-SPEC.md) and, for the face's routing, by [autobot-the-face-SPEC.md](../specs/active/autobot-the-face-SPEC.md) (FS-15).
4. **Agent experiences**: inside the interface, the `Experience` an agent runs on the face, driven once a frame through `FaceContext` in `src/autobot/tui/face.py`. The context holds the panels, the reactions, autobot's voice, the microphone's capture, the listener's hold, playback and the clock. An experience reaches nothing else. The echo is the first. Governed by [autobot-the-face-SPEC.md](../specs/active/autobot-the-face-SPEC.md), and the agents to come by [autobot-agents-and-the-agent-tree-SPEC.md](../specs/active/autobot-agents-and-the-agent-tree-SPEC.md).
5. **Agent sessions**: agent definitions, sessions through Gemini ADK, and locally persisted memory. Governed by [agent-sessions-SPEC.md](../specs/active/agent-sessions-SPEC.md).
6. **Spoken response**: the text response in, spoken audio in a configurable voice out. The face is its first form: a summary panel whose lines are spoken, and a detail panel the agent owns. The echo is its first speaker. Governed by [autobot-the-face-SPEC.md](../specs/active/autobot-the-face-SPEC.md) (FS-8, FS-9, FS-13).
7. **Voice dataset**: `data/voice/manifest.jsonl` and the WAV files beside it. The interface writes it (the recording session, the live monitor's marks, the Audio screen), and the training and the evaluation read it. The split between trained and held-out audio is computed from the manifest line alone, in `src/autobot/wakeword/dataset.py`, so every reader agrees on it. Governed by [autobot-wake-word-training-studio-SPEC.md](../specs/active/autobot-wake-word-training-studio-SPEC.md).
8. **Candidate and install**: a training run's output, `models/wakeword/candidate.onnx` with `candidate.json`, in; the installed model at the phrase's model path and three lines of `config/autobot.toml` out, through `src/autobot/wakeword/install.py` and only on the owner's confirmation. Governed by the same spec (TS-8).
9. **Runtime control**: the subcommands of `python -m autobot.listener.control` (`up`, `down`, `status`, `logs`, `snapshot`, `hold`, `install`, `uninstall`, `enable`, `disable`). The `Makefile` and the terminal user interface both go through them, so there is one implementation of starting and stopping the listener. The socket stays one-way, and two commands are how the interface asks the running listener for something:
   - **`snapshot`.** `snapshot --stretch ID` and `snapshot --buffer` are how the live monitor gets audio out of the listener. The command writes `run/snapshot.request`, signals the listener with `SIGUSR1`, and waits up to five seconds for `run/snapshot.wav`, or for the listener's one-line reason in `run/snapshot.error`.
   - **`hold`.** `hold --seconds S` is how the face keeps the listener from hearing what it plays. The command writes `run/hold.request`, signals the listener with `SIGUSR2`, and waits up to five seconds for the listener to take it.
10. **Google Cloud**: autobot and Google Cloud meet only through the `GoogleCloud` protocol in `src/autobot/gcp/client.py`. `connect` is the only way to get one, and it gives one only as the service account `[gcp] service_account`, through its key at `[gcp] credentials_file`. Every check, the model list and the store of a secret go through it, which is why they are tested against the fake in `tests/gcp/gcp_fakes.py`. What changes the project crosses this seam only as text: the `gcloud` commands of `src/autobot/gcp/commands.py`, which the owner runs. Governed by [autobot-google-cloud-configuration-screen-SPEC.md](../specs/active/autobot-google-cloud-configuration-screen-SPEC.md).

The wall UI seam of earlier versions of this document is withdrawn with the part (ADR 0006).

## Settled without asking

- **Resolved.** The question of whether the rules layer reads `run/listener.sock` as a separate process or is loaded into the listener is gone, because the code answers it. The rules run inside the interface's process, and the face's router reads the socket. The rules-layer part and seam 3 now say so.
- **Resolved.** The question of where the rules layer and the agents appear in the interface is gone, because the code answers it. They appear in the Face and Agent routing entries of the main menu. The agents spec's Agent editor, agent tree and Tab modes are recorded as specified and not built, because the menu still has Face as an item and no `src/autobot/agents/` exists.
- **Resolved.** The Google Cloud connection is recorded as a part of its own and as seam 10, as the Google Cloud screen spec asks of this document. Gemini is still recorded as not built, because no agent calls it yet.

## Changes needed elsewhere

- **AGENTS.md.** Its list of the interface's screens names the studio and settings screens and Google Cloud, but not Face or Agent routing, which the main menu has. It also omits the `hold` control command.
- **docs/specs/active/autobot-agents-and-the-agent-tree-SPEC.md.** Its `describes` names `src/autobot/agents/**`, which does not exist yet. The Face is still a menu item reached without Tab, and the echo is the only agent in the router's table.

## Open Questions

- Do the agent host and the local store sit in the control plane with the rules layer, or in a third process of their own?
  The listener is a separate data-plane process from the rules layer, but the split between the rules layer and the agent host is not stated. Today the rules and the one agent both run inside the terminal user interface's process.
- How is the control plane's rule definition turned into what the listener runs, and is that a compile step?
  The listener is "probably compiled somehow" from the control plane. Today the listener reads `config/autobot.toml` when it starts and never reads the `[[routes]]` rules. The face reads the rules, and nothing is compiled.
- Where does the capture of the speech after a wake happen: in the listener, which already holds the microphone, or in whatever reads the `WakeEvent`?
  Today the face hears commands only through the listener's `text_match` transcripts, and the echo records through the interface's own capture of the microphone. Once a trained `openwakeword` model is installed, the listener publishes no words, so no command can be routed. Recommended: have the listener transcribe the speech after a wake whatever the phrase's kind, as the capture WW-4 already asks for, because routing would otherwise stop working on the day the trained model is installed.
