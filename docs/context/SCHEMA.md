---
type: schema
title: "autobot — Schema"
authority: descriptive
status: active
---

# autobot — Schema

The schema document records the data shapes that more than one part of autobot depends on, where each is defined, and its fields. The built shapes are described from the code: the configuration file and the wake phrase configuration inside it; the listener's two events, `WakeEvent` and `ScoreEvent`, and its snapshot hand-off; the manifest line of the voice dataset; and the evaluation file a training run writes beside a model. The shapes of the parts not yet built (rules, agent definitions, agent memory) are still the sketch the Smart Start intake implies, listed with only the fields the intake supports, so that the specs and plans have a shared name for each.

## One section per shape: where it is defined, and its fields as a table

### Configuration file

Defined in `config/autobot.toml`, in TOML, and loaded and validated by `src/autobot/config.py` (`load_config`) into frozen dataclasses with the standard library's `tomllib`. It is the only place configuration lives: the terminal user interface writes its changes back to this file and keeps no store of its own. Setting the environment variable `AUTOBOT_CONFIG` loads a different file. An unknown table or key, a missing required one, or a value of the wrong type is a one-line `ConfigError`, not a traceback. Shared by the listener, the audio check, the training and the terminal user interface.

| Table | Field | Type | Meaning |
|---|---|---|---|
| `[host]` | `nuc_hostname` | string, default empty | The hostname of the NUC. The listener, the audio check and the service installer refuse to run when this machine's hostname differs from it or it is empty. |
| `[audio]` | `input_device` | string or whole number, required | The microphone: a case-insensitive substring of a device name (an exact name wins), or a device index. On the NUC it is `pipewire`. |
| `[audio]` | `output_device` | string or whole number, required | The speaker, named the same way. On the NUC it is `pipewire`. |
| `[audio]` | `sample_rate` | whole number, default `16000` | Samples per second of the audio the listener reads. |
| `[audio]` | `frame_ms` | whole number, default `80` | The length of one frame in milliseconds. It must be a whole number of samples at `sample_rate`; the default gives 1280 samples. |
| `[[wake_phrases]]` | (list) | one or more tables | The wake phrases; see **Wake phrase configuration** below. |
| `[ack]` | `text` | string, default `Yes?` | What the listener says aloud when it hears a wake phrase whose target is `ack`. |
| `[ack]` | `voice` | string, default `en_US-joe-medium` | The piper-tts voice that says it, the name of a voice file under `models/piper/`. The default is piper's Joe Medium voice (TS-15). |
| `[tui]` | `theme` | `light` \| `dark` \| `sun`, default `light` | The colour theme of the terminal user interface. The table is optional. Pressing `T` in the interface switches to the next theme and writes it here (TS-13). The valid names are `TUI_THEMES` in `src/autobot/config.py`. |

### Wake phrase configuration

Defined as one `[[wake_phrases]]` table per phrase in `config/autobot.toml`, loaded as `WakePhraseConfig` in `src/autobot/config.py`. At least one entry is required, and no phrase may be configured twice (compared without regard to case). Shared by the listener (which detects the phrase), the training and the install (which read the phrase and write its `kind`, `model` and `threshold`) and, later, the agent host (which is selected by it). One phrase is configured: `autobot`. On a fresh install it has `kind = "text_match"`, an empty `model` and `threshold = 0.5`; installing a trained candidate from the interface writes `kind = "openwakeword"`, the model path and the recommended threshold (TS-2, TS-8).

| Field | Type | Meaning |
|---|---|---|
| `phrase` | string, required | The spoken phrase the listener watches for. |
| `kind` | `text_match` \| `openwakeword` \| `sherpa_kws`, required | The detector that implements the phrase. `text_match` tests whether the local transcript of a stretch of speech contains the phrase; `openwakeword` scores the audio with a model file trained for the phrase; `sherpa_kws` is sherpa-onnx's open-vocabulary keyword spotter, which needs no training. |
| `model` | string, default empty | The path of the model file, relative to the repository root. Required for `openwakeword`, unused by `text_match` and `sherpa_kws`. With none configured, the studio installs to `models/wakeword/<phrase>.onnx`. |
| `threshold` | number above 0 and at most 1, default `0.5` | The score at which the detector fires. For `sherpa_kws` it is twice sherpa-onnx's own keyword threshold, so `0.5` is that library's default. |
| `target` | string, required | What the wake is for. `ack`, a spoken acknowledgement, is the only target built; an agent or rule set is the intended target later. |

The valid kinds are listed in `WAKE_PHRASE_KINDS` in `src/autobot/config.py`. A new kind is added there and as a module `src/autobot/listener/engines/<kind>.py`.

### WakeEvent

Defined as the frozen dataclass `WakeEvent` in `src/autobot/listener/detector.py`. A detector returns one when it hears its phrase. `src/autobot/listener/events.py` writes each as one JSON line, UTF-8 and ending in a newline, to the listener's log and to every reader connected to the Unix domain socket `run/listener.sock`. Shared by the listener (which publishes it), the live monitor of the terminal user interface (which reads it) and, later, the rules layer (which will read it).

| Field | Type | Meaning |
|---|---|---|
| `event` | the string `wake` | Present in the JSON line only, to tell this line from a `score` line. |
| `phrase` | string | The configured phrase, as written in the configuration. |
| `target` | string | The phrase's configured target. |
| `kind` | string | The detector kind that heard it. |
| `score` | number from 0 to 1 | The detector's confidence. |
| `at` | timestamp | When it was heard, in UTC. In the JSON line it is ISO 8601 with milliseconds, such as `2026-11-25T21:40:03.512+00:00`. |

### ScoreEvent

Defined as the frozen dataclass `ScoreEvent` in `src/autobot/listener/events.py`: what one detector made of one stretch of speech. The listener writes it as one JSON line to the readers of `run/listener.sock` only, never to the log. A stretch yields one line per configured wake phrase, in the order of the configuration, all with the same `stretch`. A stretch's score lines are published before any `wake` it causes, so a reader ties a `wake`, which carries no identifier, to the stretch of the score lines just before it. Shared by the listener (which publishes it) and the live monitor (`src/autobot/tui/monitor.py`, which reads it without importing the listener).

| Field | Type | Meaning |
|---|---|---|
| `event` | the string `score` | Tells this line from a `wake` line. |
| `phrase` | string | The configured phrase. |
| `kind` | string | The detector kind that scored the stretch. |
| `score` | number from 0 to 1, or `null` | The detector's score for the stretch, rounded to three decimals: for `openwakeword` the model's highest score over the stretch; for `text_match` the confidence of the stretch's last transcript; for `sherpa_kws` the configured threshold when it fired. `null` when the detector produced no score for the stretch. |
| `stretch` | whole number | The stretch identifier: 1 for the first stretch of a listener run, rising by one per stretch. It does not survive a restart of the listener. It is the name `control snapshot --stretch` takes. |
| `at` | timestamp | When the line was published: the end of the stretch, or the wake that closed its score early. ISO 8601 in UTC with milliseconds. |

An example line: `{"event": "score", "phrase": "autobot", "kind": "openwakeword", "score": 0.912, "stretch": 7, "at": "2026-11-26T18:02:11.480+00:00"}`.

### Snapshot hand-off

Defined in `src/autobot/listener/service.py` (the listener's side) and `src/autobot/listener/control.py` (the `snapshot` subcommand). The listener holds the audio of the last five scored stretches, each with its pre-roll and at most its last 30 seconds, and the last ten seconds of microphone audio (125 frames), in memory, and writes one of them only when asked. Shared by the listener, the control command and the live monitor, which runs the command as a subprocess.

| File | Written by | Holds |
|---|---|---|
| `run/snapshot.request` | `control snapshot` | A stretch identifier (`snapshot --stretch ID`) or the word `buffer` (`snapshot --buffer`). The command then sends the listener `SIGUSR1` and waits up to five seconds. The listener removes the file when it has answered. |
| `run/snapshot.wav` | the listener | The audio asked for: 16 kHz mono 16-bit WAV, written through a temporary name and `os.replace`. Overwritten by the next snapshot. |
| `run/snapshot.error` | the listener | Instead of the WAV, the one-line reason it could not be written: the stretch is no longer among the five held, nothing has been heard yet, or the request is unreadable. |

All three files are removed when the listener starts and when it stops.

### Voice dataset manifest line

Defined as the frozen dataclass `ClipRecord` in `src/autobot/wakeword/dataset.py`. `data/voice/manifest.jsonl` holds one JSON object per line, one per clip, and is the only record of what each clip is: a WAV in the directory with no line does not exist to the training, and neither does a line whose WAV is gone. The clips are 16 kHz mono 16-bit WAV files beside the manifest, named `<speaker>-<prompt_id or source>-<YYYYmmdd-HHMMSS>.wav`, with `-2`, `-3` added when a name is taken. `data/` is git-ignored: the dataset is never committed and never leaves the NUC (TS-4, TS-5). Shared by the terminal user interface (which writes it), the training and the evaluation (which read it).

| Field | Type | Meaning |
|---|---|---|
| `file` | string | The WAV's file name inside `data/voice/`: a plain name, no directory. |
| `phrase` | string | The wake phrase the clip was recorded for. |
| `prompt` | string | The text of the script prompt that asked for the clip, or a description for a clip with no script prompt: `audio screen`, `monitor: right detection`, `monitor: false accept` or `monitor: missed wake`. |
| `prompt_id` | string, may be empty | The prompt's identifier in the recording script, such as `bare-01`. Empty for a clip with no script prompt. |
| `label` | `positive` \| `negative` \| `ambient` | What the clip is: the phrase, speech that is not the phrase, or the room with nobody speaking. Set by the prompt or the mark, never by hand afterwards. |
| `source` | `session` \| `monitor` | Whether the clip came from a recording session (or the Audio screen), or from a mark in the live monitor. |
| `speaker` | string | The name the speaker gave. |
| `device` | string | The name of the input device the clip was recorded through. |
| `recorded_at` | timestamp | Local date and time of recording, ISO 8601 with seconds and the UTC offset, such as `2026-11-26T17:40:03-05:00`. |

The split between trained and held-out audio is not stored. It is computed from the line, by `split` and `ambient_slices` in the same module, and never changes between runs (TS-6):

- A positive or negative clip with a `prompt_id` is held out when the SHA-1 of `speaker + "/" + prompt_id`, taken as a number, is divisible by five. Every take of a prompt by a speaker lands on the same side.
- A positive or negative clip with an empty `prompt_id` is held out when the SHA-1 of its `file` is divisible by five.
- An ambient clip is split by time: its first four fifths are trained on and its last fifth is held out.

### Model evaluation file

Defined by `stage_eval` and `real_section` in `src/autobot/wakeword/train.py` and `RealScores.to_json` in `src/autobot/wakeword/evaluate.py`. A training run writes it as JSON beside the model it describes: `models/wakeword/candidate.json` beside `candidate.onnx`. An install moves it with the model, so the installed model's is `<name>.json` (`autobot.json`) and the previous model's `<name>.prev.json`. Shared by the training (which writes it), the install (which reads `recommended_threshold` from it and may add `installed_threshold`) and the terminal user interface.

The top level carries the synthetic figures, as before the studio (`sweep`, `metrics_at_threshold`, `worst_hard_negatives`, `near_homophones`), the run's settings (`phrase`, `scale`, `steps`, `hidden`, `exclude_near_homophones`, `trained`) and these:

| Field | Type | Meaning |
|---|---|---|
| `recommended_threshold` | number | The threshold the training recommended, on the dataset as it was then. An install writes it to the configuration file only when the interface has no fresh recommendation (nothing is held out). |
| `installed_threshold` | number, optional | Added by an install when the threshold it wrote differs from `recommended_threshold`: the one the Candidate screen recommended on the held-out set as it was at the install. A revert to the model restores this threshold, and `recommended_threshold` when the field is absent. |
| `recommended_on` | `real` \| `synthetic` | Which figures it was chosen on. It is `real` when the held-out real set has at least one positive and gave a recommendation, and `synthetic` otherwise. |
| `synthetic_recommended_threshold` | number | The threshold the synthetic figures alone recommend. |
| `real` | object | The real-set part, below. |

The `real` object says which recordings were trained on and which were held out (TS-6):

| Field | Type | Meaning |
|---|---|---|
| `trained` | list of file names | The positive and negative clips trained on. |
| `heldout` | list of file names | The positive and negative clips held out. |
| `excluded` | list of file names | Training-side recordings of phrases that sound the same as the wake phrase, left out of the training by default: neither trained on nor held out. |
| `ambient_trained` | list of `{file, start, end}` | The slice of each ambient clip trained on, in seconds. |
| `ambient_heldout` | list of `{file, start, end}` | The slice of each ambient clip held out, in seconds. |
| `counts` | object | `trained_positives`, `trained_negatives`, `excluded_negatives`, `heldout_positives`, `heldout_negatives`, `ambient_clips`, `ambient_trained_minutes`, `ambient_heldout_minutes`. |
| `manifest` | string | A hash of the manifest lines the run read. A changed dataset gives a new one, and a fresh training run. |
| `note` | string | Present only when there are no recordings for the phrase: `no real recordings: synthetic clips only`. |

When there are recordings, the exported model is scored on the held-out ones and `real` also carries (TS-7):

| Field | Type | Meaning |
|---|---|---|
| `heldout_positives`, `heldout_negatives` | whole numbers | How many held-out clips the figures rest on. |
| `minutes_ambient` | number | How many minutes of held-out ambient audio they rest on. |
| `sweep` | list of rows | One row per threshold: `threshold`, `detected` and `recall` (held-out positives), `false_accepts` and `fa_rate` (held-out negatives), `ambient_false_accepts`, and `ambient_fa_per_hour`, which is `null` below twelve minutes of held-out ambient. |
| `recommended_threshold` | number or `null` | The threshold recommended on the real figures. |
| `metrics_at_threshold` | row or `null` | The sweep row at that threshold. |
| `positive_peaks`, `negative_peaks` | object | The peak score of each held-out clip, by file name. |

The interface does not trust these stored figures for a comparison: the Candidate screen scores both models afresh on the dataset as it is now, and installs at the threshold that scoring recommends.

### Rule

Not yet defined. A classic, non-AI matching and validation step applied to a transcript before any AI call. Shared by the rules layer (which evaluates it) and the agent host (which receives what it routes). The intake says rules validate queries, decide which kind of AI call to feed, may answer locally with low latency, and accumulate into conversational pathways.

| Field | Type | Meaning |
|---|---|---|
| match | expression | What in the transcript the rule matches. Form not given. |
| outcome | one of: local response, route to agent | What happens when the rule matches. |
| target | reference | The agent or agent tool a routing rule hands the query to. |

### Agent definition

Not yet defined. Describes one persistent Gemini ADK agent the server hosts. Shared by the agent host and the rules layer.

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
