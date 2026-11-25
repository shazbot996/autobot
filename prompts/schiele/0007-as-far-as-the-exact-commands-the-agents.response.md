---
run: schiele/0007
at: 2026-11-25T22:39:49.360Z
mode: revise
agent: revise
model: claude:claude-opus-5-5
target: AGENTS.md
template: revise
prompt-sha: 93adb15e5fc4
template-sha: 80089d1722f6
commit: c1eef29+
attachments: []
read:
  - AGENTS.md
outcome: failed
usage:
  input: 13620
  output: 2307
  peak: 13620
  window: 1000000
cost: 0
error: no frontmatter title
---

# Agents Guide for autobot

This file orients the coding agents that work in autobot: what to read first, what to run before they report, and what they must never touch. autobot is a personal always-on voice assistant: a local wake-word listener and local speech-to-text and text-to-speech, a rules layer that validates queries before any AI call, and persistent Gemini ADK agents whose memory lives on the local machine. `AGENTS.md` is prescriptive: when an agent's behaviour and this file disagree, the agent is wrong. It carries no YAML frontmatter because agent tools read it as plain markdown.

## Context map (start here)

Read the context documents in `docs/context/` before searching the codebase. Each one covers one subject:

1. [BRIEF.md](docs/context/BRIEF.md): what autobot is, who it is for, and what it is not.
2. [GLOSSARY.md](docs/context/GLOSSARY.md): the project's vocabulary: wake phrase, rule, agent, session, memory, and the named tools.
3. [CONVENTIONS.md](docs/context/CONVENTIONS.md): coding, naming, layout, and documentation conventions.
4. [CONSTRAINTS.md](docs/context/CONSTRAINTS.md): the rules that must never break. Speech-to-text, wake-word detection and text-to-speech are processed locally, never by a cloud service. They override any plan or spec that disagrees.
5. [SCHEMA.md](docs/context/SCHEMA.md): the shared shapes: wake phrase configuration, rules, agent definitions, agent state.
6. [ARCH.md](docs/context/ARCH.md): the parts (listener, speech, rules, agent host, wall UI, local store) and the seams between them.
7. [INFRASTRUCTURE.md](docs/context/INFRASTRUCTURE.md): the Intel NUC, the WSL test machine, and the Google Cloud project.

`docs/decisions/` holds the decisions made so far, including proposed recommendations awaiting the owner's acceptance. `docs/specs/active/` holds the stub specs for the wake-word listener, rules-first routing, and agent sessions. `docs/plans/active/` is empty.

## What to run before reporting

Nothing runs yet. autobot has no code or build tooling, so there is no test, type check, lint or build to run. Until the first code lands, an agent reports by reading its changes back against the context documents above, and in particular by checking them against `CONSTRAINTS.md`.

Once `docs/decisions/0001-python-for-the-listener-and-agent-host-ADR.md` is accepted and the first build lands, this section is expected to name the Python checks the project adopts: the test runner (`pytest`), the linter and formatter (`ruff`), and a type checker. Those are expectations, not commands that work today. Replace this paragraph with the real commands when they exist.

Build a basic wake-word test first, using `autobot` as the wake word. `autobot` is the main wake word and people rarely say it by accident, so it is the first test wake word.

## Runtime control

The project has two control layers below the web interface. Build platform work into these layers, not into the main application:

- **Makefile.** Use `make` targets for runtime control, such as `make up` and `make down`, with separate targets for the front end and the back end. Running a bare `make` prints a summary of the available commands. When you add a target, add it to that summary.
- **Terminal user interface.** A 90s-style terminal user interface handles platform management, testing, control, configuration and deployment. For example, `make deploy` opens a terminal user interface that runs the cloud deployments and the identity and access management checks. The main application depends on that work but does not do it. Some runtime configuration is also done from this terminal user interface.

The running application uses its web interface for everything else.

Agents must be able to test and control the local audio hardware on the NUC. Build the audio hardware checks into one of these lower layers, either as a plain `make` target or inside the terminal user interface, so an agent can run them.

## What not to touch

- Never read or write secrets: `.env`, `.env.*`, or any credential file. Google Cloud credentials for the Gemini project are secrets.
- Never introduce a cloud service, API or hosted model for speech-to-text, wake-word detection or text-to-speech. `CONSTRAINTS.md` `CN-1` and `CN-2` forbid it. A task that seems to need one stops and says so.
- Never move agent memory or agent state off the local machine. The intake makes local persistence a core purpose of the server; see `docs/decisions/0002-gemini-adk-agents-with-local-state-ADR.md`.
- Never write inside `.git/`, and never commit, push, or rewrite history unless a person asks.
- Never edit CR2's run records under `prompts/<author>/`. They are write-once history.
- Never edit an existing Architectural Decision Record in `docs/decisions/` in place. Record a change of mind as a new, higher-numbered decision.
- Never edit CR2's project configuration under `.cr2/` unless the task is about it.

## Settled without asking

- **Resolved.** Every new `make` target goes into the summary that a bare `make` prints, because that summary exists to list the available commands.
- **Resolved.** "Nothing runs yet" still holds. Neither the Makefile nor the terminal user interface exists yet, so none of the commands under "Runtime control" work today.

## Changes needed elsewhere

- **docs/context/CONVENTIONS.md.** Record the two runtime-control layers. The first is Makefile targets: a bare `make` prints a summary, plus `make up`, `make down`, and separate front-end and back-end targets. The second is a 90s-style terminal user interface for platform management, testing, configuration and deployment (`make deploy`).
- **docs/context/ARCH.md.** Add the Makefile and terminal user interface layer. It handles deployment, the identity and access management checks, and the audio hardware checks outside the main application.
- **docs/specs/active/wake-word-listener-SPEC.md.** Record `autobot` as the main wake word and the first test wake word.

## Open Questions

- Which exact test, lint and type-check commands will agents run? Decided only when the stack recommendation in ADR 0001 is accepted and the first code lands.
- Should the audio hardware check start as a plain `make` target or inside the terminal user interface? Recommended default: a plain `make` target, listed in the `make` summary. It is the simpler layer, and a terminal user interface can wrap it later.
