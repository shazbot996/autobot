---
type: glossary
title: "autobot — Glossary"
authority: prescriptive
status: active
---

# autobot — Glossary

The glossary is autobot's vocabulary: the words the project uses, what each means here, and what it does not mean. Specs cite it instead of redefining terms. The entries also prime CR2's in-browser speech-to-text, so each starts with its term in bold, spelled exactly as the project spells it, and the terms most likely to be misheard come first.

## One entry per term: what it means here, and what it does not

**Gemini ADK.** Google's Agent Development Kit, the SDK autobot uses to define and run its AI agents against Gemini models served from the owner's Google Cloud project. The ADK runs the agent's reasoning; autobot keeps the agent's state locally. Not a speech service, and not where agent memory lives.

**faster-whisper.** The local speech-to-text engine the owner has had good results with, proposed for autobot by ADR 0003 and accepted by ADR 0004. It turns speech captured after a wake phrase into a transcript on the local machine. Its first use is inside the `text_match` detector, with the `tiny.en` model. Not a cloud API; never called over the network.

**piper-tts.** The local text-to-speech engine the owner has had good results with, proposed for autobot by ADR 0003 and accepted by ADR 0004. It speaks the assistant's responses in a configurable voice on the local machine. Its first uses are the listener's acknowledgement and the synthetic speech the wake-word model is trained on. Not a cloud API.

**openWakeWord.** The local wake-word engine autobot uses (ADR 0005): an open-source project that turns audio into speech embeddings with a shared feature extractor, two small ONNX models, and scores them with a small model trained for one phrase. autobot takes the feature extractor and the method from it, trains its own model for the phrase `autobot` from synthetic speech (`make train-wakeword`), and runs all three models on the NUC's CPU with `onnxruntime`; the `openwakeword` Python package itself is not used. It is the `openwakeword` kind of wake phrase. Not a cloud service, and not speech-to-text: it recognises only the phrase it was trained for.

**Silero VAD.** The local voice-activity detection model the listener runs on every frame of audio to tell speech from silence. The listener's speech gate uses it so that silence never reaches a detector, and to find where a stretch of speech ends. Not a wake-word detector: it knows that someone is speaking, not what is said.

**WakeEvent.** The record the listener publishes when it hears a wake phrase: the phrase, its target, the kind of detector that heard it, a score and the time. It is written as one JSON line to the listener's log and to `run/listener.sock`; see `SCHEMA.md`. Not the query: nothing of what was said after the wake phrase is in it.

**NUC.** The dedicated Intel NUC running Ubuntu that is the 24x7 home of autobot, with its own local audio hardware. Its hostname is `branch`. It is the only machine autobot supports, and builds are run on it.

**WSL.** The Windows Subsystem for Linux layer on the owner's Windows 11 dev server. autobot does not support it: support is limited to the NUC (`INFRASTRUCTURE.md`). Not a test machine for autobot.

**PWA.** A progressive web app: the form the owner describes for autobot's user interface, probably shown full-screen in Chrome on the local host. Not a keyboard-and-mouse application; see wall UI.

**gcloud.** The Google Cloud command-line tools the owner uses to manage the Google Cloud side of autobot. Not part of the running system.

**Cloud Identity.** Google's identity service, which the owner uses alongside gcloud for the `cambiumplatform.com` organization. Not an authentication system for autobot's users; autobot has one user.

**autobot.** This project: the owner's personal always-on voice assistant and the server that hosts its agents. Not a product for other people.

**wake phrase.** A configurable spoken phrase (also called a wake word, wake keyword or activation phrase) that the listener watches for all the time. Hearing one starts a capture and selects which agent or rule set the capture is for. Not the query itself: the query is what follows the wake phrase.

**wake-word modeling.** The local detection of wake phrases in the microphone stream, including making new kinds of wake phrases configurable. Always local, never a cloud service (`CN-1`). Not speech-to-text: it recognises only wake phrases.

**listener.** The always-on local process that reads the microphone, detects wake phrases, and hands the speech that follows to speech-to-text. Not the agent host and not the UI.

**kind.** The kind of a wake phrase is the detector that implements it, named by the phrase's `kind` field: `openwakeword`, a model file trained for the phrase, or `text_match`, a test of whether the local transcript of a stretch of speech contains the phrase (WW-7). Not the phrase itself: two phrases can share a kind, and one phrase has one kind.

**acknowledgement.** The short phrase, "Yes?" by default, that the listener says aloud when it hears a wake phrase whose target is `ack` (WW-8). It is synthesised locally with piper-tts, once, when the listener starts. Not an agent's response.

**STT.** Speech-to-text: turning captured speech into a transcript. In autobot it is always local (`CN-1`). Not wake-word detection, which runs before it.

**TTS.** Text-to-speech: speaking a text response aloud in a configurable voice. In autobot it is always local (`CN-2`).

**voice.** The configurable TTS voice the assistant answers in. Not the owner's voice, and not a wake phrase.

**rule.** A classic, non-AI matching and validation step applied to a transcript before any AI call: it decides whether a query is understood, which kind of AI call it feeds, which agent it goes to, or whether it can be answered locally at once. Rules are meant to be accumulated over time into a synthesized, procedural set. Not a prompt and not a model.

**rules-first.** autobot's principle that every query passes through rules before an AI agent is called, so that validation and routing are deterministic and local responses are low-latency. Not a ban on AI: it decides when AI is called.

**conversational pathway.** A sequence of matching rules that leads a spoken exchange toward one of the planned agent tools. Not a single rule.

**agent.** A persistent AI agent defined with Gemini ADK, reached through one or more configured wake phrases, with its own locally saved state and memory. A list-making agent is the owner's example. Not a coding agent working in this repository, which this glossary calls a CR2 agent when it must.

**agent tool.** A voice-interactive, screen-backed capability an agent exposes, reached through conversational pathways. Not a build tool.

**session.** One interactive exchange between the owner and an agent, opened after a rule routes a validated query, and spoken back through TTS. Not the agent's long-term memory, which outlives sessions.

**memory.** The persistent state an agent keeps between sessions, such as a list-making agent's lists. Always saved locally on the server (ADR 0002). Not the Gemini model's context window.

**wall UI.** The web UI shown on a screen on the wall, backing the voice interaction with a display. Driven by voice; not operated by keyboard and mouse.

**interrogatory use.** The first intended use of autobot: asking it questions and being answered. Not desktop control, which is a secondary goal.

**desktop direction.** The secondary goal of telling autobot to do things with the desktop of the PC it runs on. Not in the initial scope.

## Open Questions

- Should "wake word", "wake keyword" and "activation phrase" be collapsed into "wake phrase" in all documents, as this glossary does, or does the owner distinguish them?
