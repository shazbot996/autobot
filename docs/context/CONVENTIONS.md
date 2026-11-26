---
type: conventions
title: "autobot — Conventions"
authority: prescriptive
status: active
---

# autobot — Conventions

Conventions are the house rules for how autobot is written: the choices that are reasonable either way but must be made one way, so that the codebase reads as if one person wrote it. The intake asked for a stack recommendation rather than naming one; `docs/decisions/0001-python-for-the-listener-and-agent-host-ADR.md` proposes Python. The code conventions below are the mainstream Python defaults and take effect when that recommendation is accepted. The documentation conventions hold now. A rule that must never be broken belongs in `CONSTRAINTS.md`, not here.

## One section per area, each convention with its reason

### Language and formatting (pending acceptance of ADR 0001)

- Code is Python, at the current stable release the chosen libraries support. Reason: one language for the listener, the rules layer, the speech bindings and the ADK agent host keeps the always-on process simple to run on the NUC.
- Formatting and linting use `ruff`, with its default formatter settings, applied to the whole tree. Reason: one tool for both jobs, no style arguments, and a check an agent can run.
- Public functions and module boundaries carry type hints and the tree passes a static type checker. Reason: the listener, rules and agent host hand data to each other across process and module boundaries, and types make those hand-offs checkable before the microphone is involved.

### Layout (pending acceptance of ADR 0001)

- The project is a single Python package under `src/`, with `pyproject.toml` at the repository root as the only project definition. Reason: the `src` layout prevents accidental imports from the working directory, and one definition file is the mainstream default.
- Tests live under `tests/` mirroring the package layout. Reason: this is where `pytest` discovers them by default and where readers expect them.

### Naming (pending acceptance of ADR 0001)

- Modules, functions and variables are `snake_case`; classes are `CapWords`; constants are `UPPER_SNAKE_CASE`, as PEP 8 prescribes. Reason: the standard library and every chosen dependency follow PEP 8, so anything else reads as foreign.
- Project terms are spelled as `GLOSSARY.md` spells them: `wake_phrase`, `rule`, `agent`, `session`, `memory`. Reason: the glossary is the single vocabulary and the code should be searchable by it.

### Testing (pending acceptance of ADR 0001)

- The test runner is `pytest`. Reason: it is the mainstream Python default and what agents will run before reporting.
- Tests never require a microphone, a speaker or a network connection; audio and cloud boundaries are exercised through recorded fixtures and fakes. Reason: tests must pass on the WSL test machine and in sandboxes without audio hardware, and a test that reached a cloud speech service would itself violate `CONSTRAINTS.md`.

### Documentation

- Every managed document under `docs/` and at the repository root, except `AGENTS.md`, starts with YAML frontmatter declaring its `type`, `title`, `authority` and `status`. Reason: CR2 reads these fields to place the document on the board and to decide which actions it admits.
- Each document keeps the section headings its type declares, in order. Reason: agents and the board find content by those headings; renaming one hides what is under it.
- Specs cite constraints and glossary terms by their numbers and spellings rather than restating them. Reason: a rule described once cannot drift into two versions.

## Open Questions

- Are the Python conventions above accepted? They depend on ADR 0001, which is a proposed recommendation, not a decision the owner has made.
- Which static type checker (for example `mypy` or `pyright`) will be used? The mainstream default is not settled and the intake does not say.
- How is the wall UI written? Q3 leaves the interface undecided, so no front-end conventions exist yet.
