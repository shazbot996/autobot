---
run: schiele/0166
at: 2026-11-28T14:51:30.113Z
mode: revise
agent: revise
model: claude:claude-opus-5-5
target: docs/specs/active/autobot-inference-server-console-SPEC.md
template: revise
prompt-sha: 7d3759d46874
template-sha: 80089d1722f6
commit: 3f5e28b
attachments: []
read:
  - AGENTS.md
  - docs/specs/active/autobot-inference-server-console-SPEC.md
  - docs/specs/active/autobot-edge-control-plane-SPEC.md
  - server/src/autobot_server/stt.py
  - docs/context/CONSTRAINTS.md
  - server/src/autobot_server/config.py
outcome: success
usage:
  input: 179811
  output: 22177
  peak: 59807
  window: 1000000
cost: 0
---

Revise docs/specs/active/autobot-inference-server-console-SPEC.md:
- Add a Speech-to-text screen: the server STT model, beam and VAD settings, hotwords and initial prompt, with plain explanations like the Parameters screen. Speech-to-text settings are now in scope. Audio capture and the NUC listener stay out of scope.
- The CN-1 amendment and ADR 0007 are done; cite them instead of listing them as missing.
- Reference the edge control plane spec for anything about cancel or reflexes, and add nothing on the NUC.
- The playground's chat and dry-run must respect the cancel behaviour the server exposes, if the control plane spec added a cancel path.
