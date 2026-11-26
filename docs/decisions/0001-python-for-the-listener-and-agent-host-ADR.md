---
type: decision
title: "autobot — ADR 0001: Python for the listener and agent host"
authority: historical
status: proposed
---

# autobot — ADR 0001: Python for the listener and agent host

This is a recommendation made by the Smart Start agent, not a decision the owner has made. The owner asked for a recommendation (Q3) and leaned toward Python. It stays `proposed` until a later, higher-numbered ADR accepts or rejects it.

## Context

The intake asks for a stack recommendation given the scope: an always-on local listener running a local wake-word model, local speech-to-text and text-to-speech, a rules layer, and a host for persistent agents built with Gemini ADK. The owner noted that "the always-on listener with a local model sounds right up python's alley", has had good luck with faster-whisper and piper-tts, and wants to use Gemini ADK. The interface is undecided, probably full-screen Chrome on the local host. The system runs on an Intel NUC under Ubuntu and is tested under WSL.

## Decision

Recommendation: write the listener, the speech bindings, the rules layer and the agent host in Python, as one package. The reasons are that the tools the owner already named are Python-first (faster-whisper and piper-tts ship Python packages, and Gemini ADK has a Python SDK), that local audio capture and model inference on Linux have mature Python bindings, and that one language keeps a 24x7 process on a small machine simple to run and to test on WSL. The web UI is left open; a Python process can serve it, but its own language is not decided here.

## Alternatives

- **Node.js / TypeScript throughout.** Gemini ADK also has a TypeScript SDK, and it would unify the wall UI with the backend. Rejected for the recommendation because the local speech tools the owner trusts are Python-native and would need subprocess or binding wrappers.
- **A compiled language (Go, Rust) for the listener, Python for the agents.** Lower overhead for the always-on process. Not recommended at the start: two languages double the tooling for a one-person project, and the owner has not reported a resource problem with Python for this workload.
- **Python only where unavoidable, with the UI deciding the rest.** Deferred: the UI is undecided, so letting it drive the choice would stall the listener.

## Consequences

- `CONVENTIONS.md` records mainstream Python conventions (ruff, pytest, `src/` layout, PEP 8 naming) that take effect when this ADR is accepted.
- The wall UI's technology remains an open question and may introduce a second language later.
- If the owner rejects this recommendation, `CONVENTIONS.md` and the expectations in `README.md` and `AGENTS.md` must be rewritten for the chosen stack.

## Open Questions

- Does the owner accept Python as the implementation language? A later ADR records the answer.
- Which Python version and which static type checker?
- In what language is the wall UI written, and is it served by the Python process?
