---
run: schiele/0020
at: 2026-11-25T23:11:05.980Z
mode: answer
agent: answer
model: claude:claude-sonnet-5-5
target: docs/context/ARCH.md
template: answer
prompt-sha: f2216a491e45
template-sha: 90976540f104
commit: 3fb1da8+
attachments: []
read:
  - AGENTS.md
  - docs/context/ARCH.md
outcome: success
usage:
  input: 11468
  output: 2402
  peak: 11468
  window: 1000000
cost: 0
---

---
type: arch
title: "autobot — Architecture"
authority: descriptive
status: active
---

# autobot — Architecture

The architecture document is the map of autobot: the parts, how speech and data move between them, and the seams along which its specs divide. No code exists yet, so this map is the design the Smart Start intake describes, not a report of a built system. `Derive` replaces it once the first parts land.

autobot is not one large process. It has two layers: a data plane that handles live audio, and a control plane above it that manages the rules. The system supports the Ubuntu NUC alone.

## What the parts are

- **Listener (data plane).** The always-on local process that reads the microphone on the NUC and detects configurable wake phrases with a local wake-word model. It is the data plane: it runs the audio processing and the machine-learning model that captures the words. It must be fast, and is likely to be compiled from definitions made in the control plane. It is local for cost: always-on listening must cost nothing per second (`CN-1`). Folder not yet decided.
- **Speech-to-text (STT).** Local transcription of the speech captured after a wake phrase. The owner has had good results with faster-whisper; ADR 0003 proposes it. Never a cloud service (`CN-1`).
- **Rules layer (control plane).** Classic rules that validate a transcript, decide which kind of AI call it feeds, route it to an agent, or answer locally with low latency. This is the control plane above the listener, comparable to Dialogflow in Google Cloud: it is where the rules are managed. Rules accumulate over time into conversational pathways toward the planned agent tools. This is the "rules-first" part of rules-first AI agents.
- **Agent host.** The server-side part that defines persistent agents with Gemini ADK, opens interactive sessions with them against Gemini models in the owner's Google Cloud project, and persists each agent's state and memory locally. Creating new agents that serve from configured wake phrases and saving their state locally is a core purpose of this part (ADR 0002).
- **Text-to-speech (TTS).** Local synthesis of the response in a configurable voice. The owner has had good results with piper-tts; ADR 0003 proposes it. Never a cloud service (`CN-2`).
- **Wall UI.** A screen-backed web interface, probably a PWA shown full-screen in Chrome on the local host, that displays the interaction. It is driven by voice, not keyboard and mouse. It is phased in later: the first builds are headless and all voice, and as much as possible is built in Python first.
- **Local store.** Where wake phrase configuration, rules, agent definitions and agent memory live on the local machine. Form not yet decided; see `SCHEMA.md`.
- **Gemini agents (external).** The Gemini models in the owner's Google Cloud project that the ADK calls for agent reasoning. The only part that is not local.

## How they fit

One typical exchange, as the intake describes it:

1. The listener runs continuously on the NUC, reading local audio hardware. It detects a configured wake phrase locally.
2. The wake phrase selects a target (an agent or rule set) and the listener captures the speech that follows.
3. STT transcribes the capture locally.
4. The rules layer validates the transcript. If a rule can answer locally, it does so at once, with low latency. Otherwise a rule decides which kind of AI call the query feeds and routes it to an agent.
5. The agent host opens or continues a session with that agent through Gemini ADK. The reasoning runs against Gemini in Google Cloud; the agent reads and writes its memory in the local store.
6. The response comes back as text. TTS speaks it in the configured voice, and, once the wall UI exists, the wall UI shows it.

Only step 5 leaves the machine, and only as text. Audio never does.

## The seams

1. **Wake-word listener**: the microphone stream, wake phrase detection, and the hand-off of a capture plus its target to STT. This is the seam between the data plane and the control plane. Governed by [wake-word-listener-SPEC.md](../specs/active/wake-word-listener-SPEC.md).
2. **Rules-first routing**: the transcript in, and either a local response or a validated, routed query out. Governed by [rules-first-routing-SPEC.md](../specs/active/rules-first-routing-SPEC.md).
3. **Agent sessions**: agent definitions, sessions through Gemini ADK, and locally persisted memory. Governed by [agent-sessions-SPEC.md](../specs/active/agent-sessions-SPEC.md).
4. **Spoken response**: the text response in, spoken audio in a configurable voice out. No spec yet.
5. **Wall UI**: what the display shows and how it follows the voice interaction. Deferred until after the headless build. No spec yet.

## Open Questions

- Which local wake-word engine does the listener use? The intake names none.
  The owner does not know yet. The aim is to build the wake-word engine here, which needs a machine-learning model; the owner built one before for a system with a tensor processing unit (a Coral dev board), but it was long ago and the details are uncertain. Not settled.
- Do the agent host and the local store sit in the control plane with the rules layer, or in a third process of their own?
  The listener is a separate data-plane process from the rules layer, but the split between the rules layer and the agent host is not stated.
- How is the control plane's rule definition turned into what the listener runs, and is that a compile step?
  The listener is "probably compiled somehow" from the control plane.
