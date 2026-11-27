---
type: glossary
title: "autobot — Glossary"
authority: prescriptive
status: active
provenance:
  - written: schiele/gpu-inference-server-host-on-wsl
    at: 2026-11-27T20:49:06Z
    mode: revise
---

# autobot — Glossary

The glossary is autobot's vocabulary: the words the project uses, what each means here, and what it does not mean. Specs cite it instead of redefining terms. The entries also prime CR2's in-browser speech-to-text, so each starts with its term in bold, spelled exactly as the project spells it, and the terms most likely to be misheard come first.

Revised 2026-11-27: the WSL entry no longer says the Windows machine is unsupported, and the inference server and `GAMEON1` are added, since the owner made that machine the GPU inference server.

## One entry per term: what it means here, and what it does not

**Gemini ADK.** Google's Agent Development Kit, the SDK autobot uses to define and run its AI agents against Gemini models served from the owner's Google Cloud project. The ADK runs the agent's reasoning; autobot keeps the agent's state locally. Not a speech service, and not where agent memory lives.

**faster-whisper.** The local speech-to-text engine the owner has had good results with, proposed for autobot by ADR 0003 and accepted by ADR 0004. It turns speech captured after a wake phrase into a transcript on the local machine. Its first use is inside the `text_match` detector, with the `tiny.en` model. Not a cloud API; never called over the network.

**piper-tts.** The local text-to-speech engine the owner has had good results with, proposed for autobot by ADR 0003 and accepted by ADR 0004. It speaks the assistant's responses in a configurable voice on the local machine. Its first uses are the listener's acknowledgement, spoken in the Joe Medium voice (`en_US-joe-medium`), and the synthetic speech that augments the wake-word model's training. Not a cloud API.

**openWakeWord.** The local wake-word engine autobot uses (ADR 0005): an open-source project that turns audio into speech embeddings with a shared feature extractor, two small ONNX models, and scores them with a small model trained for one phrase. autobot takes the feature extractor and the method from it, trains its own model for the phrase `autobot` (`make train-wakeword`) from the voice dataset with synthetic speech as augmentation (ADR 0006), and runs all three models on the NUC's CPU with `onnxruntime`; the `openwakeword` Python package itself is not used. It is the `openwakeword` kind of wake phrase. Not a cloud service, and not speech-to-text: it recognises only the phrase it was trained for.

**Silero VAD.** The local voice-activity detection model the listener runs on every frame of audio to tell speech from silence. The listener's speech gate uses it so that silence never reaches a detector, and to find where a stretch of speech ends. Not a wake-word detector: it knows that someone is speaking, not what is said.

**sherpa-onnx.** A local speech toolkit whose open-vocabulary keyword spotter is the `sherpa_kws` kind of wake phrase: a small streaming recogniser, fetched into `models/sherpa-kws/`, that listens for the configured phrase without any training. It is the baseline the trained model is measured against (TS-14). Not a cloud service, and not the engine for 24x7 use.

**WakeEvent.** The record the listener publishes when it hears a wake phrase: the phrase, its target, the kind of detector that heard it, a score and the time. It is written as one JSON line to the listener's log and to `run/listener.sock`; see `SCHEMA.md`. Not the query: nothing of what was said after the wake phrase is in it.

**ScoreEvent.** The record the listener publishes for every stretch of speech, one per configured wake phrase: what that detector made of the stretch, with the stretch's identifier. It goes to `run/listener.sock` only, never to the log; see `SCHEMA.md`. Not a wake: most stretches are scored and wake nothing.

**NUC.** The dedicated Intel NUC running Ubuntu that is the 24x7 home of autobot, with its own local audio hardware. Its hostname is `branch`. It is the only machine autobot's audio paths and front end support, and builds are run on it; the inference server runs on `GAMEON1`.

**WSL.** The Windows Subsystem for Linux layer on `GAMEON1`, the owner's Windows 11 PC, running the `Ubuntu-24.04` distribution in which the inference server is to run (`INFRASTRUCTURE.md`). Not a place where the listener, the studio or any audio path runs: those stay on the NUC.

**Inference server.** The second program of autobot, planned to run in WSL on `GAMEON1` and to serve the NUC over the LAN with GPU speech-to-text, a local language model for routing and chat, and possibly text-to-speech (`autobot-gpu-inference-server-SPEC.md`, IS-1 to IS-14). Not a cloud service, not the front end, and not where agent state or memory lives. Also called the GPU server.

**GAMEON1.** The hostname of the owner's Windows 11 PC with the RTX 2080 Ti, at `192.168.86.53` on the LAN, the home of the inference server. Not the NUC, and not a machine autobot's audio paths support.

**PWA.** A progressive web app: the form the intake described for autobot's user interface, shown full-screen in Chrome on the local host. No longer planned: the application's interface is the terminal user interface (ADR 0006). See wall UI.

**gcloud.** The Google Cloud command-line tools the owner uses to manage the Google Cloud side of autobot. Not part of the running system.

**Cloud Identity.** Google's identity service, which the owner uses alongside gcloud for the `cambiumplatform.com` organization. Not an authentication system for autobot's users; autobot has one user.

**autobot.** This project: the owner's personal always-on voice assistant and the server that hosts its agents. Not a product for other people.

**wake phrase.** A configurable spoken phrase (also called a wake word, wake keyword or activation phrase) that the listener watches for all the time. Hearing one starts a capture and selects which agent or rule set the capture is for. Not the query itself: the query is what follows the wake phrase.

**wake-word modeling.** The local detection of wake phrases in the microphone stream, including making new kinds of wake phrases configurable. Always local, never a cloud service (`CN-1`). Not speech-to-text: it recognises only wake phrases.

**listener.** The always-on local process that reads the microphone, detects wake phrases, and hands the speech that follows to speech-to-text. Not the agent host and not the terminal user interface, which reaches it only through its control commands and its socket.

**kind.** The kind of a wake phrase is the detector that implements it, named by the phrase's `kind` field: `text_match`, a test of whether the local transcript of a stretch of speech contains the phrase, which is the kind of a fresh install; `openwakeword`, a model file trained for the phrase; or `sherpa_kws`, a keyword spotter that needs no training (WW-7). Not the phrase itself: two phrases can share a kind, and one phrase has one kind.

**acknowledgement.** The short phrase, "Yes?" by default, that the listener says aloud when it hears a wake phrase whose target is `ack` (WW-8). It is synthesised locally with piper-tts in the Joe Medium voice, once, when the listener starts. Not an agent's response.

**STT.** Speech-to-text: turning captured speech into a transcript. In autobot it is always local (`CN-1`). Not wake-word detection, which runs before it.

**TTS.** Text-to-speech: speaking a text response aloud in a configurable voice. In autobot it is always local (`CN-2`).

**voice.** The configurable TTS voice the assistant answers in; today piper's Joe Medium. Not the owner's voice, which is what the voice dataset holds, and not a wake phrase.

**rule.** A classic, non-AI matching and validation step applied to a transcript before any AI call: it decides whether a query is understood, which kind of AI call it feeds, which agent it goes to, or whether it can be answered locally at once. Rules are meant to be accumulated over time into a synthesized, procedural set. Not a prompt and not a model.

**rules-first.** autobot's principle that every query passes through rules before an AI agent is called, so that validation and routing are deterministic and local responses are low-latency. Not a ban on AI: it decides when AI is called.

**conversational pathway.** A sequence of matching rules that leads a spoken exchange toward one of the planned agent tools. Not a single rule.

**agent.** A persistent AI agent defined with Gemini ADK, reached through one or more configured wake phrases, with its own locally saved state and memory. A list-making agent is the owner's example. Not a coding agent working in this repository, which this glossary calls a CR2 agent when it must.

**agent tool.** A voice-interactive capability an agent exposes, reached through conversational pathways. Not a build tool.

**session.** One interactive exchange between the owner and an agent, opened after a rule routes a validated query, and spoken back through TTS. Not the agent's long-term memory, which outlives sessions, and not a recording session.

**memory.** The persistent state an agent keeps between sessions, such as a list-making agent's lists. Always saved locally on the server (ADR 0002). Not the Gemini model's context window.

**wall UI.** The web UI the intake described, shown on a screen on the wall and driven by voice. No longer planned, and never built: ADR 0006 made the terminal user interface the application. Not the terminal user interface.

**terminal user interface.** autobot's application: one full-screen `curses` interface on the NUC, opened by `make launch`, whose first job is the studio (TS-1). Also written TUI. Not a control panel beside some other application, and not a second store of configuration: every setting it changes is written to `config/autobot.toml`.

**studio.** The wake-word training studio: the screens of the terminal user interface that lead from an untrained listener to an installed model, namely the recording session, the training, the candidate, the live monitor and the dataset. Not a separate program.

**voice dataset.** The recordings of the owner's voice, and of anyone else in the house who records, under `data/voice/`, with the one manifest `manifest.jsonl` that says what each clip is (TS-4). It is never committed and never leaves the NUC. Not the test fixtures, which are a few clips exported from it on purpose, and not the synthetic speech the training generates.

**recording session.** One speaker working through the recording script in the terminal user interface: one prompt at a time, each take stored with the prompt that asked for it, so every clip is labelled by construction (TS-3). It can be left at any prompt and resumed later. Not a session with an agent.

**held-out set.** The share of the voice dataset that is never trained on and is what a model is judged on: one script prompt in five, one unprompted clip in five, and the last fifth of every ambient recording (TS-6). The split is stable between runs. Not the synthetic held-out voices, whose figures are reported apart.

**candidate.** The model a training run writes, `models/wakeword/candidate.onnx` with its evaluation `candidate.json`, awaiting the owner's decision. There is one at a time. Not the installed model: nothing uses it until the owner installs it from the Candidate screen (TS-8).

**installed model.** The model the listener runs for a phrase: the file at the phrase's model path, when the configuration names `kind = "openwakeword"` and the file exists. The model an install replaces is kept beside it as `<name>.prev.onnx`. Not any file that happens to be at that path; see leftover model.

**leftover model.** A model file at the phrase's model path that the configuration does not name as `kind = "openwakeword"`, such as the synthetic-only `models/wakeword/autobot.onnx` of the runs before the studio. It is kept, never deleted, does not count as installed, and is not compared with a candidate (TS-2). Not the previous model, which an install put aside.

**live monitor.** The screen of the terminal user interface that shows the running listener's scores and wakes as they happen, and lets the owner mark a detection right or wrong or mark a missed wake; each mark saves that utterance into the voice dataset (TS-9). Not a recorder: the listener writes audio only when a mark asks.

**stretch.** One stretch of speech as the listener's speech gate lets it through: the pre-roll from just before the speech, the speech, and the quiet that ends it. Each gets an identifier that starts at 1 with every run of the listener. The listener holds the audio of the last five. Not an utterance by definition: a long utterance with a pause can be two stretches.

**interrogatory use.** The first intended use of autobot: asking it questions and being answered. Not desktop control, which is a secondary goal.

**desktop direction.** The secondary goal of telling autobot to do things with the desktop of the PC it runs on. Not in the initial scope.

## Open Questions

- Should "wake word", "wake keyword" and "activation phrase" be collapsed into "wake phrase" in all documents, as this glossary does, or does the owner distinguish them?
