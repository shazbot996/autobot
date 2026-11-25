---
run: schiele/0016
at: 2026-11-25T22:57:40.185Z
mode: answer
agent: answer
model: claude:claude-sonnet-5-5
target: README.md
template: answer
prompt-sha: 254e73bd0f91
template-sha: 90976540f104
commit: f469ee2+
attachments: []
read:
  - AGENTS.md
  - README.md
outcome: success
usage:
  input: 12011
  output: 2057
  peak: 12011
  window: 1000000
cost: 0
---

---
type: readme
title: "autobot"
authority: descriptive
status: active
---

# autobot

## What it is, in a paragraph a stranger can follow

autobot is a personal, always-on voice assistant for one house. A local process listens to a microphone around the clock for configurable wake phrases, such as a home-made "hey google". When it hears one, it transcribes what follows with a local speech-to-text model, runs the words through a set of plain rules first, and only then, when a rule says so, opens an interactive session with one of several persistent AI agents built on Gemini ADK and served from Google Cloud in the `us-east5` region. Each agent is reached by its own wake phrase and keeps its memory on the local machine, so that, for example, a list-keeping agent remembers its lists between conversations. The assistant answers out loud with a local text-to-speech voice that the owner can choose, and shows what it is doing on a screen on the wall, operated by voice rather than keyboard and mouse. Speech recognition, wake-word detection and speech synthesis never leave the machine; only the agent reasoning goes to the cloud. It is a platform for voice-first applications, and no application exists yet: the framework comes first. That framework is the local process that listens, responds and catches wake words, and captures commands through a rules-based engine that leads into agent activation, in the manner of Google's Dialogflow. Simple question-and-answer use comes first once the framework works.

## Running it

Nothing runs yet. The repository holds only its context documents. The intake leans toward Python for the always-on listener, and `docs/decisions/0001-python-for-the-listener-and-agent-host-ADR.md` records that as a proposed recommendation; once it is accepted and the first code lands, this section is expected to name the conventional Python commands (an environment setup step, `pytest`, `ruff`), the command-line tools that get the system running, and the way the listener process is started on the Intel NUC. The main user interface is a progressive web application served locally by that process and shown full-screen in Chrome on the NUC itself. None of those work today.

## Where things are

- `README.md`, `CHANGELOG.md`, `AGENTS.md`: the Root row. What the project is, what has changed, and how coding agents must work here.
- `docs/context/`: the Core row. `BRIEF.md` (what autobot is and is not), `GLOSSARY.md` (its vocabulary), `CONVENTIONS.md` (house rules for the code), `CONSTRAINTS.md` (the rules that must never break: local-only speech processing), `SCHEMA.md` (shared data shapes), `ARCH.md` (the parts and the seams) and `INFRASTRUCTURE.md` (the NUC, the WSL test machine and the Google Cloud project).
- `docs/decisions/`: Architectural Decision Records. `0001` proposes Python, `0002` records Gemini ADK with locally kept state, `0003` proposes faster-whisper and piper-tts as the local speech stack.
- `docs/specs/active/`: the behaviour each feature must have. Stub specs exist for the wake-word listener, rules-first routing, and persistent agent sessions.
- `docs/plans/active/` and `docs/plans/archive/`: implementation plans. Empty until the first spec is planned.
- `docs/remediations/`, `docs/publish/`, `docs/attachments/`, `docs/images/`: security remediations, external publications, and attached files. Empty.
- `.cr2/`: CR2's own project configuration.

## Open Questions

- Which wake-word detection engine runs locally? The intake rules out cloud services for it but names no engine.
  The owner does not know yet and wants an agent's help choosing. A machine-learning model of some kind will be needed; the owner built one before for a system with a built-in tensor processing unit (a Coral Dev Board), but that was a while ago and the details are not remembered.
- Which Google Cloud project id and region will host the Gemini agents? Q4 says a project will be created in the `cambiumplatform.com` organization but gives no id.
  The region is `us-east5`. No project exists yet; an id will be made up and the project created to match it.
