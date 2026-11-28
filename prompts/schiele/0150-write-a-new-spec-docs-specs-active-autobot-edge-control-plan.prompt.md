---
run: schiele/0150
at: 2026-11-28T13:41:45.939Z
mode: create
agent: create
model: claude:claude-opus-5-5
target: docs/specs/active/autobot-edge-control-plane-SPEC.md
template: create
prompt-sha: 4d4bc6fb39e2
template-sha: d7922285ccf1
commit: 21b6aa4
attachments: []
read:
  - AGENTS.md
  - prompts/builtin/examples/spec.md
  - docs/specs/active/autobot-gpu-inference-server-SPEC.md
  - docs/context/CONSTRAINTS.md
  - config/autobot.toml
  - src/autobot/listener/audio.py
  - src/autobot/listener/engines/text_match.py
  - src/autobot/listener/service.py
  - src/autobot/routing.py
  - src/autobot/cascade.py
  - src/autobot/agents/core.py
  - src/autobot/agents/confirm.py
  - docs/context/GLOSSARY.md
  - docs/context/ARCH.md
  - src/autobot/agents/speech.py
  - src/autobot/agents/words.py
  - docs/specs/active/autobot-agents-and-the-agent-tree-SPEC.md
  - docs/specs/active/autobot-the-face-SPEC.md
  - docs/specs/active/wake-word-listener-SPEC.md
  - src/autobot/server/client.py
  - src/autobot/tui/router.py
  - server/src/autobot_server/chat.py
  - docs/context/INFRASTRUCTURE.md
outcome: success
usage:
  input: 908862
  output: 31837
  peak: 135389
  window: 1000000
cost: 0
---

Write a new spec: docs/specs/active/autobot-edge-control-plane-SPEC.md, "autobot — edge control plane". Pick an unused two-letter rule prefix (propose EC- if it is free).
Read first: AGENTS.md, docs/context/*.md (including the amended CN-1 and ADR 0007), the GPU inference server spec, src/autobot/ audio capture, the wake word (kind = "text_match"), the listener, routing.match_route, cascade.py, agents/core.py, and the confirmation convention in config/autobot.toml.
What it is: the always-on layer on the NUC that keeps listening during every session and gives the owner control outside the language model.
Rules to cover, each with a Reason:
- One audio capture process fans the same frames out to several consumers: the reflex listener (tiny.en, always on) and, after the wake word, a stream to GAMEON1 /v1/stt. When the server is away, the session falls back to tiny.en.
- Echo cancellation: speaker output is the reference signal, and every consumer reads the echo-cancelled stream, never the raw mic. Choose the canceller (WebRTC AEC or speexdsp) or a hardware echo-cancelling mic, and say how to test it.
- While Autobot speaks, the GPU speech-to-text stream is paused, but the reflex listener stays on (barge-in).
- Reflexes: a short configured list of exact "Autobot + phrase" commands ([[reflexes]] or similar, decide which), matched only on the full utterance. They work with GAMEON1 off. Each reflex can require confirmation, using the existing convention. Initial set: cancel, yes/no, check progress, plus any others you'd suggest.
- Cancel really aborts: it stops TTS, ends the session and cancels any in-flight /v1/route or /v1/chat request. Say whether the server needs a cancel path or the NUC just closes the stream.
- Side tasks: a reflex can run a preconfigured NUC action during a session without interrupting it (for example, check progress reads session state and answers briefly).
- Spoken replies are scrubbed of reflex phrases, so Autobot never triggers itself.
- Latency budget for reflexes and the fallback behaviour.
Add the terms reflex, control plane and barge-in to GLOSSARY.md. List the changes needed elsewhere (ARCH, SCHEMA, INFRASTRUCTURE, other specs). Out of scope: the console, and moving agents to tools. Put the questions only the owner can answer under Open Questions (mic hardware, reflex list, confirmation per reflex).
