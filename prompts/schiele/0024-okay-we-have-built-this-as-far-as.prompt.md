---
run: schiele/0024
at: 2026-11-25T23:17:47.114Z
mode: create
agent: create
model: claude:claude-fable-5-1
target: docs/plans/active/headless-wake-word-prototype-PLAN.md
template: create
prompt-sha: a933154fc182
template-sha: d7922285ccf1
commit: 6339fa0+
attachments: []
read:
  - AGENTS.md
  - prompts/builtin/examples/plan.md
  - docs/context/CONSTRAINTS.md
  - docs/context/ARCH.md
  - docs/context/CONVENTIONS.md
  - docs/specs/active/wake-word-listener-SPEC.md
  - docs/specs/active/rules-first-routing-SPEC.md
  - docs/specs/active/agent-sessions-SPEC.md
  - docs/context/SCHEMA.md
  - docs/context/INFRASTRUCTURE.md
  - docs/context/GLOSSARY.md
  - docs/context/BRIEF.md
  - docs/decisions/0001-python-for-the-listener-and-agent-host-ADR.md
  - docs/decisions/0003-faster-whisper-and-piper-tts-for-local-speech-ADR.md
  - README.md
  - docs/decisions/0002-gemini-adk-agents-with-local-state-ADR.md
outcome: success
usage:
  input: 87891
  output: 14282
  peak: 31594
  window: 1000000
cost: 0
---

Okay, we have built this as far as we can in spec.

Okay, I want to try to write a plan to build the first headless version of what we have here into a prototype we can test. Try to fill in the blanks as best as you can. Most of the questions in all of the specs that we wrote are asking about how what local wake-word engine we're using. I don't know the answer to that. That's really what we're building here, as the first step is a local system that I can run that will wake up, connect to an audio stream, stay running 24-7 as efficiently as possible, listening for the wake-up word that contains or look, we're listening for the word autobot. So I could say, hey, autobot, yo-autobot, as long as it hears autobot, it wakes up. And then it responds that it heard it. That's all we're trying for the first thing, but we're going to have that writing in code that we can then serve that into the rest of the application that we're going to custom build that defines that next layer of routing. So let's see if we can do this.

Since this is the first build, I can't direct you to a specific spec to build from. We have three specs built, and a whole bunch of core documentation built as well in terms of context that define as best as we can what we can use to write a plan to build this code. So let's incorporate as much context as we can. And try to build as effective a first test as we can, utilizing all layers. The makefile to do runtime control, a basic terminal user interface to manage configuration of it, including audio config and testing.

And then a service that we can run to test the core application.
