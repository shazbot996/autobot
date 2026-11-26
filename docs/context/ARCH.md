---
type: arch
title: "autobot — Architecture"
authority: descriptive
status: active
provenance:
  - run: schiele/0019
    at: 2026-11-25T23:11:43.275Z
    mode: revise
---

# autobot — Architecture

The architecture document is the map of autobot: the parts, how speech and data move between them, and the seams along which its specs divide. One part is built, the listener, together with the two control layers that sit below the application: the `Makefile` and the terminal user interface. They are described here from the code. The other parts are still the design the Smart Start intake describes, and each says so.

autobot is not one large process. It has two layers: a data plane that handles live audio, and a control plane above it that manages the rules. Below both sit the control layers that start, stop, configure and deploy them. The system supports the Ubuntu NUC alone.

## What the parts are

- **Listener (data plane). Built.** The always-on local process that reads the microphone on the NUC and detects configurable wake phrases. It is the Python package `src/autobot/listener/`, run as `python -m autobot.listener`. It reads 16 kHz mono audio in 80 ms frames (`audio.py`), passes them through a speech gate built on Silero VAD so that silence never reaches a detector (`vad.py`), feeds the voiced frames to one detector per configured wake phrase (`detector.py`, `engines/`), publishes each wake as a `WakeEvent` (`events.py`), and speaks an acknowledgement (`ack.py`). It is local for cost: always-on listening must cost nothing per second (`CN-1`).
- **Wake-word engines. Built.** A detector implements one wake phrase behind the `Detector` protocol: `feed` takes the next frame of a stretch of speech, `end_of_speech` says the stretch has ended, and `reset` makes the detector forget what it has buffered. Two kinds exist, one module each under `src/autobot/listener/engines/`. `openwakeword` is the engine for 24x7 use and the configured kind: a small model trained here for the phrase `autobot` scores every frame it is fed, on openWakeWord's shared feature extractor, and the detector fires when the score reaches the phrase's `threshold`, then ignores the next two seconds so that one utterance is one wake (ADR 0005). The three ONNX models are run with `onnxruntime` on the CPU, one thread each; the `openwakeword` Python package is not a dependency. `text_match` is the bootstrap and fallback: it transcribes each stretch of speech with faster-whisper (`tiny.en`) and fires when the transcript contains the phrase. The model is trained by `src/autobot/wakeword/train.py`, which `make train-wakeword` runs: it synthesises the phrase, words that sound like it and ordinary speech with piper-tts in 20 voices, adds reverb and noise, computes openWakeWord features, trains the classifier against them and openWakeWord's general negative features, evaluates it on 7 voices it never trained on, and writes `models/wakeword/autobot.onnx` with its evaluation beside it in `autobot.json`.
- **Speech-to-text (STT).** Local transcription of the speech captured after a wake phrase, with faster-whisper (ADR 0003, accepted by ADR 0004). Not built as a part of its own: faster-whisper runs today only inside the `text_match` detector. Never a cloud service (`CN-1`).
- **Rules layer (control plane). Not built.** Classic rules that validate a transcript, decide which kind of AI call it feeds, route it to an agent, or answer locally with low latency. This is the control plane above the listener, comparable to Dialogflow in Google Cloud: it is where the rules are managed. Rules accumulate over time into conversational pathways toward the planned agent tools. This is the "rules-first" part of rules-first AI agents.
- **Agent host. Not built.** The server-side part that defines persistent agents with Gemini ADK, opens interactive sessions with them against Gemini models in the owner's Google Cloud project, and persists each agent's state and memory locally. Creating new agents that serve from configured wake phrases and saving their state locally is a core purpose of this part (ADR 0002).
- **Text-to-speech (TTS).** Local synthesis in a configurable voice, with piper-tts (ADR 0003, accepted by ADR 0004). Its one use today is the listener's acknowledgement: the text in `[ack].text` is rendered once, when the listener starts, and that clip is played on every wake. Speaking an agent's response is not built. Never a cloud service (`CN-2`).
- **Wall UI. Not built.** A screen-backed web interface, probably a PWA shown full-screen in Chrome on the local host, that displays the interaction. It is driven by voice, not keyboard and mouse. It is phased in later: the first builds are headless and all voice, and as much as possible is built in Python first.
- **Local store.** Where wake phrase configuration, rules, agent definitions and agent memory live on the local machine. The built part is `config/autobot.toml`, the one configuration file, and `models/`, the model files, which are not committed. The form of the rest is not yet decided; see `SCHEMA.md`.
- **Gemini agents (external). Not built.** The Gemini models in the owner's Google Cloud project that the ADK calls for agent reasoning. The only part that is not local.
- **Make (control layer). Built.** The `Makefile` is the runtime control layer below the application. A bare `make` prints a summary of every target. `make up`, `make down`, `make status` and `make logs` run `python -m autobot.listener.control`; `make audio-check` checks the NUC's audio hardware; `make test`, `make lint` and `make typecheck` are the checks; `make setup` creates the environment; `make fetch-models` downloads the published model files into `models/`, and is the only target that downloads; `make train-wakeword` trains the wake-word model. No front-end targets exist, because the build is headless.
- **Terminal user interface (control layer). Built.** A 90s-style full-screen `curses` interface, `src/autobot/tui/`, opened by `make deploy`. It is a front for `config/autobot.toml` and for the listener's control commands, and holds no configuration of its own. Its screens are Audio (devices, PipeWire's default microphone and speaker, a level meter, recording and saving test clips), Wake phrase (threshold, kind, training, playing the acknowledgement), Service (the systemd unit) and Host (naming this machine as the NUC). Its Cloud deployment and Identity and access management entries are placeholders that say the Google Cloud project does not exist yet.

## How they fit

What runs today, on the NUC:

1. The listener runs continuously, as the systemd user unit `autobot-listener.service`. It reads the microphone through PipeWire and holds every model it needs as a local file.
2. Each 80 ms frame goes through the speech gate. Silence is dropped. A stretch of speech is released to the detectors with a few frames of lead-in so that the first syllable is not clipped. One VAD scores each frame once; a detector that needs wider margins is fed through a gate of its own, driven by the same score. The `openwakeword` detector asks for 0.96 s before the speech and 0.8 s after it, because its model wants to have heard what came before the phrase and scores highest just after the phrase ends.
3. The detector for each configured wake phrase is fed the stretch. When one hears its phrase, anywhere in the utterance, it returns a `WakeEvent`.
4. The listener writes the event as one JSON line to its log and to every reader connected to `run/listener.sock`.
5. When the event's target is `ack`, the listener plays the acknowledgement ("Yes?") on the speaker. While it plays, and for a moment after, the microphone's audio is discarded and the detectors are reset, so the listener does not wake itself.

Nothing in those steps leaves the machine. The listener opens no network connection: every model is a local file under `models/`, a missing one is an error rather than a download (`make fetch-models` is the separate command that downloads them, and the listener never runs it), and the process sets its offline environment (`autobot.listener.models.enforce_offline`) before any speech library is imported. That step matters for `CN-1`: `onnxruntime`, which runs the VAD, the wake-word model and piper, otherwise uploads usage telemetry a few seconds after it is imported, and `ORT_DISABLE_TELEMETRY=1` is what switches that off.

The full exchange the intake describes continues from step 4, and is not built:

1. The wake phrase's target selects an agent or rule set, and the listener captures the speech that follows.
2. STT transcribes the capture locally.
3. The rules layer validates the transcript. If a rule can answer locally, it does so at once, with low latency. Otherwise a rule decides which kind of AI call the query feeds and routes it to an agent.
4. The agent host opens or continues a session with that agent through Gemini ADK. The reasoning runs against Gemini in Google Cloud; the agent reads and writes its memory in the local store.
5. The response comes back as text. TTS speaks it in the configured voice, and, once the wall UI exists, the wall UI shows it.

Only the agent reasoning leaves the machine, and only as text. Audio never does.

The control layers stand beside this flow, not in it. `python -m autobot.listener.control` has two modes behind one set of commands. Without the systemd unit installed, `make up` starts the listener in the background with its pid in `run/listener.pid`. With the unit installed, which it is on the NUC, the same commands drive `systemctl --user`. Either way the listener's output goes to `run/listener.log`. The unit is rendered from the template `deploy/autobot-listener.service` into `~/.config/systemd/user/`; it restarts the listener after a fault that may pass (exit status 1, such as the audio device not being ready) and leaves it down after one that will not (exit status 2, such as a bad configuration or a missing model). With the unit enabled and lingering on for the user, the listener starts at boot with nobody logged in. The terminal user interface drives the listener only by running those commands as subprocesses; it does not import the listener.

## The seams

1. **Wake-word listener**: the microphone stream, wake phrase detection, and the hand-off to what comes next. This is the seam between the data plane and the control plane, and its built form is `run/listener.sock`: a Unix domain stream socket, mode 0600, that the listener binds while it runs. Any number of readers may connect; each `WakeEvent` is broadcast as one JSON line to the readers connected at that moment, with no backlog and no replay, and a reader that falls behind is dropped rather than allowed to block the audio loop. The log holds the history. The capture of the speech after the wake, and its hand-off to STT, are not built. Governed by [wake-word-listener-SPEC.md](../specs/active/wake-word-listener-SPEC.md).
2. **Detector kinds**: inside the listener, the `Detector` protocol in `src/autobot/listener/detector.py`. A new kind of wake phrase is one new module under `engines/`, found by its name, plus its name in the list of valid kinds in `src/autobot/config.py`.
3. **Rules-first routing**: the transcript in, and either a local response or a validated, routed query out. Governed by [rules-first-routing-SPEC.md](../specs/active/rules-first-routing-SPEC.md).
4. **Agent sessions**: agent definitions, sessions through Gemini ADK, and locally persisted memory. Governed by [agent-sessions-SPEC.md](../specs/active/agent-sessions-SPEC.md).
5. **Spoken response**: the text response in, spoken audio in a configurable voice out. No spec yet.
6. **Wall UI**: what the display shows and how it follows the voice interaction. Deferred until after the headless build. No spec yet.
7. **Runtime control**: the subcommands of `python -m autobot.listener.control` (`up`, `down`, `status`, `logs`, `install`, `uninstall`, `enable`, `disable`). The `Makefile` and the terminal user interface both go through them, so there is one implementation of starting and stopping the listener.

## Open Questions

- Do the agent host and the local store sit in the control plane with the rules layer, or in a third process of their own?
  The listener is a separate data-plane process from the rules layer, but the split between the rules layer and the agent host is not stated.
- How is the control plane's rule definition turned into what the listener runs, and is that a compile step?
  The listener is "probably compiled somehow" from the control plane. Today the listener reads `config/autobot.toml` when it starts, and nothing is compiled.
- Does the rules layer read `run/listener.sock` as a separate process, as the seam allows, or is it loaded into the listener's process?
- Where does the capture of the speech after a wake happen: in the listener, which already holds the microphone, or in whatever reads the `WakeEvent`?
