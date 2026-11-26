---
type: decision
title: "autobot — ADR 0002: Gemini ADK agents with locally persisted state"
authority: historical
status: active
---

# autobot — ADR 0002: Gemini ADK agents with locally persisted state

This records a decision the owner made in the Smart Start intake (Q1, Q4, Q5).

## Context

autobot's agents need a model to reason with and a framework to define them, their sessions and their tools. The owner wants several persistent agents, each reached by its own wake phrase, each with memory that outlives a session (a list-making agent with persistent lists is the example). The owner has a Google Cloud organization, `cambiumplatform.com`, and can create a project in it. At the same time the owner states that persisting the agents' actual memory locally, on the server autobot creates, is a core purpose of that server.

## Decision

Agents are built with Gemini ADK and connect to Gemini agents served from a Google Cloud project in the `cambiumplatform.com` organization. Agent state and memory are kept locally on the autobot server, not in the cloud. Creating new agents that serve from configured wake words or activation phrases, and saving their state locally, is a core purpose of the server.

## Alternatives

- **Cloud-hosted agent state** (the ADK's or Google Cloud's own session and memory services). Ruled out by the owner: state must be local.
- **A fully local model instead of Gemini.** Not chosen; the owner specifically wants Gemini via the ADK for the agent reasoning, keeping only speech and state local.
- **Another agent framework calling Gemini directly.** Not chosen; the owner named the ADK.

## Consequences

- The agent host must persist each agent's memory to the local store and supply it to ADK sessions; the shape of that store is open (`SCHEMA.md`).
- Only the text of validated queries and responses crosses to Google Cloud. Audio never does (`CN-1`, `CN-2`).
- A Google Cloud project, its credentials on the NUC, and the use of gcloud and Cloud Identity become part of the infrastructure (`INFRASTRUCTURE.md`).
- Each new agent needs an agent definition linking it to its wake phrases and its local state location.

## Open Questions

- Which ADK session and memory mechanism is pointed at local storage, and in what form?
- How does the NUC authenticate to the Google Cloud project?
- Which Gemini models do the agents use?
