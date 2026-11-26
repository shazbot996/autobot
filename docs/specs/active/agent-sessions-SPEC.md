---
type: spec
title: "autobot — Agent Sessions"
authority: prescriptive
status: active
---

# autobot — Agent Sessions

Agent sessions are the interactive exchanges between the owner and one of autobot's persistent agents. An agent is defined with Gemini ADK, reached through its configured wake phrases, reasons against Gemini in the owner's Google Cloud project, keeps its memory on the local server, and answers in speech through local text-to-speech in a configurable voice. This stub records what the intake supports; everything else is an open question. It governs the **Agent sessions** seam in `ARCH.md`.

## Numbered rules, each with its reason

1. **AS-1.** Agents are defined and run with Gemini ADK, and their reasoning calls Gemini served from the owner's Google Cloud project. Reason: the owner wants "to use adk to connect to a gemini agent in gcp" (ADR 0002).
2. **AS-2.** Each agent's state and memory are persisted on the local server, and survive across sessions and restarts. Reason: the owner wants "to persist the actual memory for the agents we make locally on the server", and gives a list-making agent with persistent memory as the example (ADR 0002).
3. **AS-3.** New agents can be created and bound to configured wake words or activation phrases, and each saves its own state locally. Reason: the owner states this is "a core purpose of the server".
4. **AS-4.** An agent's response is delivered as text that is spoken through local text-to-speech in the configured voice, and shown on the wall UI. Reason: the interaction is two-way, "it responds with text to speech with a configurable voice", and the interface is "screen backed"; `CN-2` requires the synthesis to be local.
5. **AS-5.** A session may be interactive across more than one turn: the agent can continue the exchange after its first response. Reason: the owner describes "interactive sessions" and "conversational pathways", not single-shot answers.

## Open Questions

- How is an agent defined: what does the owner write to create one, and where does it live?
- How does a session end, and how does the listener know a follow-up utterance belongs to the open session rather than being a new wake?
- How is agent memory stored locally and handed to the ADK on each session?
- How does the server authenticate to the Google Cloud project, and how are its credentials kept out of the repository?
- Which Gemini model does each agent use, and is it configurable per agent?
- How does the wall UI learn what a session is doing: polling, a push channel, or something else?
