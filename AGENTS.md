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

The first test to build is a basic wake-word test, and its wake phrase is `autobot`. People rarely say the word, so it is the first test wake phrase.

## Runtime control and audio checks

autobot has two control layers below the application. An agent builds runtime, platform and hardware controls into these layers, and never into the main application:

- **Make.** A `Makefile` handles runtime control. A bare `make` prints a summary of the available commands. Servers are started and stopped with targets such as `make up` and `make down`, with separate targets for front-end and back-end controls.
- **Terminal user interface.** A 90s-style terminal user interface manages runtime, configuration and deployment. `make deploy` opens it. It handles the cloud deployments, the identity and access management checks, and the other platform work that the application depends on but does not handle itself. Some runtime configuration is also done from this interface.

The application itself runs with a web interface that covers everything else.

Build the checks for the local audio hardware on the NUC in one of these layers, as a plain `make` target or inside the terminal user interface, so that an agent can test and control the audio hardware itself. The audio hardware check target is `make audio-check`. It runs on the NUC only. On any other machine, including the WSL test machine, it fails with a clear message, because the NUC's local audio hardware is what is being checked. Neither layer exists yet. Each new target must appear in the bare `make` summary.

## What not to touch

- Never read or write secrets: `.env`, `.env.*`, or any credential file. Google Cloud credentials for the Gemini project are secrets.
- Never introduce a cloud service, API or hosted model for speech-to-text, wake-word detection or text-to-speech. `CONSTRAINTS.md` `CN-1` and `CN-2` forbid it. A task that seems to need one stops and says so.
- Never move agent memory or agent state off the local machine. The intake makes local persistence a core purpose of the server; see `docs/decisions/0002-gemini-adk-agents-with-local-state-ADR.md`.
- Never write inside `.git/`, and never commit, push, or rewrite history unless a person asks.
- Never edit CR2's run records under `prompts/<author>/`. They are write-once history.
- Never edit an existing Architectural Decision Record in `docs/decisions/` in place. Record a change of mind as a new, higher-numbered decision.
- Never edit CR2's project configuration under `.cr2/` unless the task is about it.

## Settled without asking

- **Resolved.** The Make targets and the terminal user interface don't exist yet. To stay consistent with "Nothing runs yet", they are written as layers to build, not as commands an agent can run today.

## Changes needed elsewhere

- **docs/context/CONVENTIONS.md.** Record the convention for the two control layers: a `Makefile` whose bare `make` prints a summary of commands, with `make up` / `make down` and separate front-end and back-end targets, and a 90s-style terminal user interface opened by `make deploy` for deployment, identity and access management checks and platform configuration.
- **docs/context/ARCH.md.** Add the Make and terminal user interface layers as the platform-control parts that sit below the application and its web interface.
- **docs/specs/active/ (wake-word listener spec).** State that `autobot` is the first test wake phrase, chosen because people rarely say it.

## Open Questions

- Which exact test, lint and type-check commands will agents run? Decided only when the stack recommendation in ADR 0001 is accepted and the first code lands.
