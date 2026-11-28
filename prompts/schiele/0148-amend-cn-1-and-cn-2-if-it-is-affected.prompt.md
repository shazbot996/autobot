---
run: schiele/0148
at: 2026-11-28T13:39:06.197Z
mode: revise
agent: revise
model: claude:claude-sonnet-5-5
target: docs/context/CONSTRAINTS.md
template: revise
prompt-sha: 1c1bbf1a23c1
template-sha: 80089d1722f6
commit: d316d08
attachments: []
read:
  - AGENTS.md
  - docs/context/CONSTRAINTS.md
  - docs/specs/active/autobot-gpu-inference-server-SPEC.md
outcome: success
usage:
  input: 78200
  output: 5275
  peak: 34722
  window: 1000000
cost: 0
---

Amend CN-1 (and CN-2 if it is affected) in docs/context/CONSTRAINTS.md. Read the GPU inference server spec (IS-1 to IS-27), docs/context/INFRASTRUCTURE.md and docs/decisions/ first.
New rule: after the wake word fires, the NUC may stream that utterance's audio to GAMEON1's /v1/stt over the LAN for transcription. Audio before the wake word never leaves the NUC. Audio never goes to any cloud service. GAMEON1 holds audio only in memory for the request, never logs or writes it (keep IS-6 and TS-5 intact). Give each changed rule a Reason.
Also write ADR 0007, "Home models beside Gemini", recording why a home model on GAMEON1 serves routing, chat and speech-to-text with Gemini as the handoff, and that post-wake audio now goes to GAMEON1. Update any spec rule that cites the old CN-1 wording. List what you changed.
