---
type: changelog
title: "Changelog — autobot"
authority: historical
status: active
---

# Changelog — autobot

This file records what changed in autobot between releases, for the people who use it. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

`CHANGELOG.md` is a **historical** Root document. It records what happened, and a released entry is never rewritten afterwards. For that reason CR2 does not offer `Revise` or `Answer questions` on this square. The log grows instead. New work is added under **Unreleased** as it lands. When you cut a release, those entries move under a new version heading, with the newest version first, grouped as Added, Changed, Deprecated, Removed, Fixed, and Security. A coding agent can add these entries during a build run, for example through `Direct`. You can also add them by hand.

Unlike the other starter documents, the entry below is not tutorial text to sweep away. It is the true first record of this project: the moment it was seeded.

## Unreleased

### Added

- Smart Start intake (2026-11-25) replaced the `cr2-empty` tutorial with autobot's first real context. Rung 0 rewrote `README.md`, `AGENTS.md`, and the Core documents `BRIEF.md`, `GLOSSARY.md`, `CONVENTIONS.md`, `CONSTRAINTS.md` (`CN-1`, `CN-2`: local-only speech-to-text, wake-word modeling and text-to-speech), `SCHEMA.md` and `ARCH.md`. Rung 1 added `docs/context/INFRASTRUCTURE.md` (Intel NUC, WSL test machine, `cambiumplatform.com` Google Cloud organization). Rung 2 added ADR 0001 (Python, proposed), ADR 0002 (Gemini ADK with locally persisted state, active) and ADR 0003 (faster-whisper and piper-tts, proposed). Rung 3 added stub specs `wake-word-listener-SPEC.md`, `rules-first-routing-SPEC.md` and `agent-sessions-SPEC.md`.

## 0.1.0 - Seeded Workspace

### Added

- Project workspace initialized from the CR2 `cr2-empty` template.
- The canonical CR2 folder hierarchy: `docs/context/`, `docs/decisions/`, `docs/specs/`, `docs/plans/`, `docs/remediations/`, `docs/publish/`, `docs/attachments/`, `docs/images/`, and `.cr2/`.
- Starter tutorial Root documents (`README.md`, `CHANGELOG.md`, `AGENTS.md`) and Core context documents (`BRIEF.md`, `GLOSSARY.md`, `CONVENTIONS.md`, `CONSTRAINTS.md`, `SCHEMA.md`, `ARCH.md`) under `docs/context/`.
