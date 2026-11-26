---
type: schema
title: "autobot — Schema"
authority: descriptive
status: active
---

# autobot — Schema

The schema document records the data shapes that more than one part of autobot depends on, where each is defined, and its fields. autobot has no code yet, so none of the shapes below is defined anywhere. They are the shapes the Smart Start intake implies, listed with only the fields the intake supports, so that the first specs and plans have a shared name for each. `Derive` replaces this sketch once code exists.

## One section per shape: where it is defined, and its fields as a table

### Wake phrase configuration

Not yet defined. The intake says wake phrases are configurable, that it must be possible to configure new kinds of wake phrases, and that each agent is served from configured wake words or activation phrases. Shared by the listener (which detects it) and the agent host (which is selected by it).

| Field | Type | Meaning |
|---|---|---|
| phrase | text | The spoken phrase the listener watches for. |
| target | reference | The agent or rule set the phrase activates. |

The kind or model of a wake phrase, and how a new kind is described, are not given by the intake.

### Rule

Not yet defined. A classic, non-AI matching and validation step applied to a transcript before any AI call. Shared by the rules layer (which evaluates it) and the agent host (which receives what it routes). The intake says rules validate queries, decide which kind of AI call to feed, may answer locally with low latency, and accumulate into conversational pathways.

| Field | Type | Meaning |
|---|---|---|
| match | expression | What in the transcript the rule matches. Form not given. |
| outcome | one of: local response, route to agent | What happens when the rule matches. |
| target | reference | The agent or agent tool a routing rule hands the query to. |

### Agent definition

Not yet defined. Describes one persistent Gemini ADK agent the server hosts. Shared by the agent host, the rules layer and the wall UI.

| Field | Type | Meaning |
|---|---|---|
| name | text | The agent's name, such as the list-making agent of the owner's example. |
| wake phrases | list of wake phrase configuration | The phrases that reach this agent. |
| state location | local path | Where the agent's memory is persisted on the local machine (ADR 0002). |

How an ADK agent's model, instructions and tools are declared is not given by the intake.

### Agent memory

Not yet defined. The persistent state an agent keeps between sessions, saved locally. The intake gives the example of a list-making agent's lists and says saving agent state locally is a core purpose of the server. No fields are given; its shape is per agent.

### Document frontmatter

Defined in the YAML block at the top of every managed markdown file under `docs/` and at the repository root, except `AGENTS.md`. CR2 reads these fields to place each document on the board and to decide which actions it admits. The document types and their sections are configured in `.cr2/config.json`.

| Field | Type | Meaning |
|---|---|---|
| `type` | string | The document type, such as `brief`, `schema` or `spec`. It selects the sections the document must have. |
| `title` | string | The document's display title on the board. |
| `authority` | `prescriptive` \| `descriptive` \| `historical` | Which way a disagreement with the code is repaired. |
| `status` | string | The document's lifecycle state. |

## Open Questions

- In what format are wake phrase configuration, rules and agent definitions stored on the local machine? The intake says they are configurable and local but names no format.
- What is the shape of a wake phrase "kind", so that new kinds can be configured?
- What is the storage form of agent memory: files, a local database, or the ADK's own session storage pointed at local disk?
- Does a session have a persisted shape of its own, or only the memory it leaves behind?
