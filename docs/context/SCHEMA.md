---
type: schema
title: "autobot — Schema"
authority: descriptive
status: active
---

# autobot — Schema

The schema document records the data shapes that more than one part of autobot depends on, where each is defined, and its fields. The listener's shapes are built and are described from the code: the configuration file, the wake phrase configuration inside it, and the `WakeEvent`. The shapes of the parts not yet built (rules, agent definitions, agent memory) are still the sketch the Smart Start intake implies, listed with only the fields the intake supports, so that the specs and plans have a shared name for each.

## One section per shape: where it is defined, and its fields as a table

### Configuration file

Defined in `config/autobot.toml`, in TOML, and loaded and validated by `src/autobot/config.py` (`load_config`) into frozen dataclasses with the standard library's `tomllib`. It is the only place configuration lives: the terminal user interface writes its changes back to this file and keeps no store of its own. Setting the environment variable `AUTOBOT_CONFIG` loads a different file. An unknown table or key, a missing required one, or a value of the wrong type is a one-line `ConfigError`, not a traceback. Shared by the listener, the audio check and the terminal user interface.

| Table | Field | Type | Meaning |
|---|---|---|---|
| `[host]` | `nuc_hostname` | string, default empty | The hostname of the NUC. The listener, the audio check and the service installer refuse to run when this machine's hostname differs from it or it is empty. |
| `[audio]` | `input_device` | string or whole number, required | The microphone: a case-insensitive substring of a device name (an exact name wins), or a device index. On the NUC it is `pipewire`. |
| `[audio]` | `output_device` | string or whole number, required | The speaker, named the same way. On the NUC it is `pipewire`. |
| `[audio]` | `sample_rate` | whole number, default `16000` | Samples per second of the audio the listener reads. |
| `[audio]` | `frame_ms` | whole number, default `80` | The length of one frame in milliseconds. It must be a whole number of samples at `sample_rate`; the default gives 1280 samples. |
| `[[wake_phrases]]` | (list) | one or more tables | The wake phrases; see **Wake phrase configuration** below. |
| `[ack]` | `text` | string, default `Yes?` | What the listener says aloud when it hears a wake phrase whose target is `ack`. |
| `[ack]` | `voice` | string, default `en_US-lessac-medium` | The piper-tts voice that says it, the name of a voice file under `models/piper/`. |

### Wake phrase configuration

Defined as one `[[wake_phrases]]` table per phrase in `config/autobot.toml`, loaded as `WakePhraseConfig` in `src/autobot/config.py`. At least one entry is required, and no phrase may be configured twice (compared without regard to case). Shared by the listener (which detects the phrase) and, later, the agent host (which is selected by it). One phrase is configured: `autobot`.

| Field | Type | Meaning |
|---|---|---|
| `phrase` | string, required | The spoken phrase the listener watches for. |
| `kind` | `text_match` \| `openwakeword`, required | The detector that implements the phrase. `text_match` tests whether the local transcript of a stretch of speech contains the phrase; `openwakeword` scores the audio with a model file trained for the phrase. |
| `model` | string, default empty | The path of the model file, relative to the repository root. Required for `openwakeword`, unused by `text_match`. |
| `threshold` | number above 0 and at most 1, default `0.5` | The score at which the detector fires. |
| `target` | string, required | What the wake is for. `ack`, a spoken acknowledgement, is the only target built; an agent or rule set is the intended target later. |

The valid kinds are listed in `WAKE_PHRASE_KINDS` in `src/autobot/config.py`. A new kind is added there and as a module `src/autobot/listener/engines/<kind>.py`.

### WakeEvent

Defined as the frozen dataclass `WakeEvent` in `src/autobot/listener/detector.py`. A detector returns one when it hears its phrase. `src/autobot/listener/events.py` writes each as one JSON line, UTF-8 and ending in a newline, to the listener's log and to every reader connected to the Unix domain socket `run/listener.sock`. Shared by the listener (which publishes it) and, later, the rules layer (which will read it).

| Field | Type | Meaning |
|---|---|---|
| `event` | the string `wake` | Present in the JSON line only, to tell this line from any later event type. |
| `phrase` | string | The configured phrase, as written in the configuration. |
| `target` | string | The phrase's configured target. |
| `kind` | string | The detector kind that heard it. |
| `score` | number from 0 to 1 | The detector's confidence. |
| `at` | timestamp | When it was heard, in UTC. In the JSON line it is ISO 8601 with milliseconds, such as `2026-11-25T21:40:03.512+00:00`. |

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

- In what format are rules and agent definitions stored on the local machine? Wake phrases are TOML in `config/autobot.toml`; whether rules and agent definitions join that file or get their own is not decided.
- How does a wake phrase's `target` name an agent or a rule set? It is a plain string today, and `ack` is its only value.
- What is the storage form of agent memory: files, a local database, or the ADK's own session storage pointed at local disk?
- Does a session have a persisted shape of its own, or only the memory it leaves behind?
