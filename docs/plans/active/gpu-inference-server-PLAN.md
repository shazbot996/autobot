---
parent: docs/specs/active/autobot-gpu-inference-server-SPEC.md
type: plan
title: GPU inference server
authority: prescriptive
status: active
describes:
  - config/autobot.toml
  - pyproject.toml
  - Makefile
  - deploy/autobot-server.service
  - server/pyproject.toml
  - server/contract/contract.json
  - server/contract/health.json
  - server/contract/stt-request.json
  - server/contract/stt-response.json
  - server/contract/route-request.json
  - server/contract/route-response.json
  - server/contract/chat-request.json
  - server/contract/chat-response.json
  - server/contract/chat-stream.txt
  - server/eval/utterances.jsonl
  - server/src/autobot_server/__init__.py
  - server/src/autobot_server/__main__.py
  - server/src/autobot_server/config.py
  - server/src/autobot_server/contract.py
  - server/src/autobot_server/hostcheck.py
  - server/src/autobot_server/auth.py
  - server/src/autobot_server/gpu.py
  - server/src/autobot_server/job.py
  - server/src/autobot_server/health.py
  - server/src/autobot_server/stt.py
  - server/src/autobot_server/runtime.py
  - server/src/autobot_server/ollama.py
  - server/src/autobot_server/route.py
  - server/src/autobot_server/chat.py
  - server/src/autobot_server/app.py
  - server/src/autobot_server/http.py
  - server/src/autobot_server/control.py
  - server/src/autobot_server/fetch_models.py
  - server/src/autobot_server/token.py
  - server/src/autobot_server/check.py
  - server/src/autobot_server/eval.py
  - server/tests/server_fakes.py
  - server/tests/test_config.py
  - server/tests/test_contract.py
  - server/tests/test_auth.py
  - server/tests/test_health.py
  - server/tests/test_job.py
  - server/tests/test_stt.py
  - server/tests/test_route.py
  - server/tests/test_chat.py
  - server/tests/test_app.py
  - server/tests/test_control.py
  - server/tests/test_eval.py
  - src/autobot/config.py
  - src/autobot/routing.py
  - src/autobot/cascade.py
  - src/autobot/embedding.py
  - src/autobot/fetch_models.py
  - src/autobot/server/__init__.py
  - src/autobot/server/client.py
  - src/autobot/server/contract.py
  - src/autobot/server/hints.py
  - src/autobot/server/watch.py
  - src/autobot/server/ping.py
  - src/autobot/server/token.py
  - src/autobot/agents/base.py
  - src/autobot/agents/gemini.py
  - src/autobot/agents/local_model.py
  - src/autobot/agents/models.py
  - src/autobot/agents/hearing.py
  - src/autobot/agents/question.py
  - src/autobot/agents/core.py
  - src/autobot/agents/registry.py
  - src/autobot/agents/tree.py
  - src/autobot/tui/app.py
  - src/autobot/tui/face.py
  - src/autobot/tui/face_reactions.py
  - src/autobot/tui/router.py
  - src/autobot/tui/tree_game.py
  - src/autobot/tui/server_state.py
  - src/autobot/tui/agent_edit.py
  - src/autobot/tui/screens/main.py
  - src/autobot/tui/screens/agent_editor.py
  - src/autobot/tui/screens/routing.py
  - tests/test_config_server.py
  - tests/test_config_agents.py
  - tests/test_cascade.py
  - tests/test_embedding.py
  - tests/server/__init__.py
  - tests/server/server_fakes.py
  - tests/server/test_client.py
  - tests/server/test_contract.py
  - tests/server/test_hints.py
  - tests/server/test_watch.py
  - tests/agents/agent_fakes.py
  - tests/agents/test_agent_models.py
  - tests/agents/test_agent_hearing.py
  - tests/agents/test_agent_question.py
  - tests/agents/test_agent_core.py
  - tests/tui/test_tui_router.py
  - tests/tui/test_tui_server_state.py
  - tests/tui/test_tui_main.py
  - tests/tui/test_tui_agent_edit.py
  - docs/context/INFRASTRUCTURE.md
provenance:
  - run: schiele/0133
    at: 2026-11-27T21:24:04.890Z
    mode: create
  - run: schiele/0134
    at: 2026-11-27T21:29:48.265Z
    mode: revise
  - run: schiele/0135
    at: 2026-11-27T21:34:07.324Z
    mode: revise
---

# GPU Inference Server — Implementation Plan

Implements the autobot — GPU inference server spec (`docs/specs/active/autobot-gpu-inference-server-SPEC.md`). Nothing of the server exists today: `server/` is absent, `config/autobot.toml` has no `[server]` table, the agents have no `model` field, routing has one tier, and the interface knows only the listener's state. This is an initial plan. The work runs in two lanes that meet at the contract: the server on `GAMEON1` (steps 2 to 6, 12) and the NUC's client, cascade and agents (steps 7 to 11). Step 1, the shared configuration, and step 2, the contract, come first because both lanes read them.

## 0. The delta

| Spec Rule | Current Implementation | Planned Change |
|---|---|---|
| IS-1 host and client | Nothing; `INFRASTRUCTURE.md` records the host only | Server refuses any hostname but `[server] hostname`; binds `0.0.0.0:8300` inside WSL; `make server-check` is the one loopback caller (steps 3, 6) |
| IS-2 HTTP, JSON, one port, SSE | Nothing | `autobot_server.http` on port 8300 from the config, standard library `http.server`, SSE for chat only (steps 3, 5) |
| IS-3 `[server]` table | `parse_config` rejects an unknown table | `ServerConfig` in `src/autobot/config.py`, the table in `config/autobot.toml`, and the server's own reader of the same table (steps 1, 3) |
| IS-4 never depends; timeouts; budget | Agents call Gemini only; `LocalHearing` only | Client timeouts per call, no retry, fallback per job (steps 7, 9, 10); budget measured by `make server-eval` and shown in detail lines (step 12) |
| IS-5 stateless | Nothing | Server keeps only loaded models; one log line per request; chat carries its history (steps 3, 5) |
| IS-6 audio dropped | Nothing | `/v1/stt` decodes the WAV in memory and returns; nothing written (step 4) |
| IS-7 no cloud, no credential | Nothing | `enforce_offline()` before every speech import; models from local paths; `make server-fetch-models` the only downloader; no `gcp` code in `server/` (steps 4, 6) |
| IS-8 `POST /v1/stt` | Nothing | `autobot_server.stt` with faster-whisper `large-v3-turbo` on CUDA; hints as hotwords (step 4). Takes effect on the NUC once `CN-1` is amended (Changes needed elsewhere) |
| IS-9 `POST /v1/route`, `POST /v1/chat` | Nothing | `route.py` builds the constrained JSON schema from the agent list; `chat.py` with `response_schema`, `tools` and SSE (step 5) |
| IS-10 models and budget | Nothing | Budget constants and a start-up check in `gpu.py`; models named in `[server]`; Ollama behind the `Runtime` seam; `tts: false` (steps 3, 4, 5) |
| IS-11 `GET /health` | Nothing | `health.py`: versions, readiness of each job with reason, model names, budget, used and free memory, without touching a model (step 3) |
| IS-12 `server/` package, tests, contract | Nothing | `server/pyproject.toml`, `autobot_server`, `server/tests/` on fakes, `server/contract/` read from both suites; root `pyproject.toml` covers `server/` for ruff, mypy and pytest (steps 2, 3) |
| IS-13 address filter and token | Nothing | Bearer check in `auth.py` (401); token files at `secrets/server-token` and `~/.config/autobot-server/token`, mode `0600`; `make server-token` on each host; missing token is a reason on the main screen (steps 3, 6, 7, 8). Firewall rules stay the owner's, recorded in `INFRASTRUCTURE.md` |
| IS-14 starts at boot | Nothing | `deploy/autobot-server.service` user unit; `make server-install` with lingering; the Windows task, reservation and `apt` steps listed as the owner's preconditions (step 6) |
| IS-15 wake path local | Listener transcribes locally and never calls out | No change to the listener. The server is called only from the face's session paths; each transcript carries its source (step 7) |
| IS-16 hints | `LocalHearing` passes no hint | `autobot.server.hints` builds the capped hint list from the config; `/v1/stt` passes it as hotwords (steps 4, 7) |
| IS-17 cascade | `match_route` alone, in `Router._listen` | `autobot.cascade`: tier 1 `match_route`, tier 2 local embedding, tier 3 server route; one admission check; tier marked on the map edge; core agent or "did not catch that" (step 9) |
| IS-18 agent tiers | `AgentConfig` has no `model`, `fallback`, `parameters` | Fields added with validation; `Model` seam gains a chat shape and a local implementation; `Seams.models` keyed by kind; fallback chain (steps 1, 10) |
| IS-19 core agent | Nothing | `autobot.agents.core`, model `local`, multi-turn within a session, history on the NUC, trimmed to the context budget (step 11) |
| IS-20 handoff | Nothing | Core's `response_schema` includes a `handoff` to one of the `gemini` agents; the NUC runs it as the service account after AG-13's confirmation (step 11) |
| IS-21 tools | Nothing | `tools` in the definition, sent as schemas; the NUC runs tool calls for at most `[server] tool_rounds` rounds; the server executes none (steps 1, 5, 11) |
| IS-22 versioned contract | Nothing | `/v1` prefix; `contract_version` in `server/contract/contract.json`, reported by `/health`, checked by the watcher; mismatch is a one-line reason and the server counts as down (steps 2, 7) |
| IS-23 watcher | Main menu polls the listener only | `autobot.server.watch`: `unknown`/`up`/`degraded`/`down`, 5 s up, 5 to 60 s back-off down, two failures down, one success up; main screen line; face reaction (steps 7, 8) |
| IS-24 GPU taken away | Nothing | `job.py`: one running plus one waiting request, `503` with `Retry-After` beyond that; CUDA OOM marks the job not ready and retries its warm-up later (step 3) |
| IS-25 `make` on `GAMEON1` | No server targets | `server-setup`, `server-fetch-models`, `server-up`, `server-down`, `server-status`, `server-logs`, `server-install`, `server-check`, `server-eval`, `server-token`; on the NUC `server-ping`; all in the bare summary (step 6) |
| IS-26 evaluation | Nothing | `autobot_server.eval` over `server/eval/utterances.jsonl` against the real runtime; writes `run/server-eval-<model>.json` (step 12) |
| IS-27 audio-native models out | Nothing | No change: nothing is built; the experiment profile is not in this plan |

## 1. The `[server]` table and the agent fields

**Files:** modified `src/autobot/config.py`, `config/autobot.toml`; new `tests/test_config_server.py`; modified `tests/test_config_agents.py`.

- `ServerConfig`, frozen, parsed from `[server]`, every key defaulted and every unknown key refused: `enabled` (true), `url` (`http://192.168.86.53:8300`), `token_file` (`secrets/server-token`, resolved with `resolve_repo_path`), `hostname` (`GAMEON1`), the timeouts of IS-4 (`health_timeout`, `stt_timeout_base`, `stt_timeout_per_second`, `route_timeout`, `chat_first_token_timeout`, `chat_timeout`), the poll intervals of IS-23 (`poll_up_seconds`, `poll_down_min_seconds`, `poll_down_max_seconds`), the thresholds of IS-17 (`tier2_threshold`, `tier3_threshold`), `hint_chars` (the cap of IS-16), `tool_rounds` (IS-21), `core_listen_seconds` (10, how long the core agent's session stays open for the next turn, separate from the router's `LISTEN_SECONDS`), and the server's own settings read on `GAMEON1`: `stt_model` (`models/faster-whisper/large-v3-turbo`), `stt_compute_type` (`int8_float16` or `float16`), `llm_model` (empty until `make server-eval` names one), `llm_context` (8192), `runtime` (`ollama`), `runtime_url` (`http://127.0.0.1:11434`), and `embedding_model` (the NUC's tier 2 model path: the int8 ONNX file of `all-MiniLM-L6-v2`, with its `tokenizer.json` beside it). `url` must parse as `http://host:port`; the port is what the server binds.
- `AgentConfig` gains `model` (`none`, `local`, `gemini`; default `none`, `gemini` for `question`), `fallback` (a list; each entry one of the three values or, last only, a spoken line), `parameters` (`[[agents.parameters]]`: `name`, `type` in `string`/`integer`/`number`/`boolean`, `values`, `required`) and `tools` (`[[agents.tools]]`: `name`, `description`, `parameters` in the same shape). `RouteConfig` gains `examples`, a list of phrases for tier 2. `AGENT_CORE = "core"` joins `ROUTE_AGENTS` and `DEFAULT_AGENTS`, with `model = "local"`, `fallback = ["gemini", "the server is away"]`, a first personality prompt and `listening = true`.
- `config/autobot.toml` gains the `[server]` table with comments, the `core` agent's table, `model` on `question`, and no route for `core` (it is the default of IS-17, not a command).
- Tests: the shipped file loads; each new key's refusal message names the key; a `fallback` with a spoken line not last fails; a `parameters` entry with an unknown type fails; `core_listen_seconds` defaults to 10 and a value of zero or below is refused.

**Done when:** `load_config()` on the shipped file returns `config.server.url == "http://192.168.86.53:8300"`, `config.server.core_listen_seconds == 10` and `config.agent("core").model == "local"`, and `make test` passes with the new tests.

## 2. The contract

**Files:** new `server/contract/contract.json`, `server/contract/health.json`, `server/contract/stt-request.json`, `server/contract/stt-response.json`, `server/contract/route-request.json`, `server/contract/route-response.json`, `server/contract/chat-request.json`, `server/contract/chat-response.json`, `server/contract/chat-stream.txt`, `src/autobot/server/__init__.py`, `src/autobot/server/contract.py`, `tests/server/__init__.py`, `tests/server/test_contract.py`.

- `contract.json` holds `{"contract_version": "1"}`; it is the one place the version lives. Each example file is one full request or response, as IS-8, IS-9 and IS-11 list their fields: the STT request's audio as a `base64` field beside `hints`, so the example stays JSON; the chat stream example is the SSE text of one streamed answer (`event: token`, `event: done`).
- `autobot.server.contract` holds the NUC's dataclasses of every shape, `from_json`/`to_json`, and `contract_dir()` (`repo_root() / "server" / "contract"`).
- `tests/server/test_contract.py` reads every example through those dataclasses and back, byte-for-byte equal after normalised JSON.

**Done when:** the test fails when a field is renamed in an example or in the dataclass, and passes on the committed set.

## 3. The server package: health, token, hostname, jobs

**Files:** new `server/pyproject.toml`, `server/src/autobot_server/__init__.py`, `__main__.py`, `config.py`, `contract.py`, `hostcheck.py`, `auth.py`, `gpu.py`, `job.py`, `health.py`, `http.py`, `app.py`, `server/tests/server_fakes.py`, `test_config.py`, `test_contract.py`, `test_auth.py`, `test_health.py`, `test_job.py`, `test_app.py`; modified `pyproject.toml`.

- `server/pyproject.toml`: package `autobot_server`, `where = ["src"]`, no base dependency beyond the standard library; extra `gpu = ["faster-whisper>=1.1", "nvidia-cublas-cu12", "nvidia-cudnn-cu12>=9"]`. The root `pyproject.toml` adds `server/src` and `server/tests` to ruff's `src`, mypy's `files` and pytest's `testpaths`, so `make lint`, `make typecheck` and `make test` cover it on the NUC; `make setup` installs `server/` editable without the extra.
- `config.py` reads only the `[server]` table of `config/autobot.toml` (`AUTOBOT_CONFIG` honoured) with `tomllib`, into the server's own frozen dataclass; it does not import `autobot`.
- `hostcheck.py`: `require_server_host(hostname, configured)` raises a one-line error unless equal, as TS-11 does on the NUC; `__main__` calls it before binding.
- `auth.py`: reads the token file (`~/.config/autobot-server/token`), refuses a file not at mode `0600`, compares with `hmac.compare_digest`; a request without the right bearer is `401`. The token is never logged.
- `gpu.py`: the budget table of IS-10 as constants; `memory()` reads used and free through `nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader,nounits` behind a `Gpu` protocol, faked in tests; the start-up check fails with one line when the budget exceeds the card.
- `job.py`: `Job` wraps one model: states `loading`, `ready`, `not ready (reason)`; `warm_up()` once after load; `run(request)` admits one running and one waiting request and raises `Busy` beyond that, which the app answers `503` with `Retry-After: 1`; a CUDA out-of-memory error marks the job not ready with the reason and schedules another warm-up attempt, never a restart.
- `health.py`: the IS-11 body from the jobs' states and `gpu.memory()`, `tts: false`, `contract_version` from `server/contract/contract.json`, never touching a model.
- `http.py`: `ThreadingHTTPServer` on the configured port, bound to `0.0.0.0`; `app.py` is the pure dispatch `handle(method, path, headers, body) -> Response`, with `/v1/tts` answering `501` and a reserved reason, and one log line per request (method, endpoint, size, duration, status). The tests drive `handle` and never open a socket.

**Done when:** `server/tests` pass on the NUC with no GPU; `handle("GET", "/health", ...)` on fakes equals `server/contract/health.json` with `tts: false`; a missing bearer gets `401`; a third concurrent request gets `503`.

## 4. Speech-to-text

**Files:** new `server/src/autobot_server/stt.py`, `server/tests/test_stt.py`.

- `WhisperJob` loads faster-whisper from `stt_model` under the checkout's `models/`, `device="cuda"`, `compute_type` from the config, `local_files_only=True`, with `enforce_offline()`'s environment set before the import (the server has its own copy of the `_OFFLINE_ENV` dict, since it does not import `autobot`). A missing directory is a not-ready reason, not a fetch.
- The request's WAV is decoded with the standard library `wave`; anything but 16 kHz mono 16-bit is `400`. The hints are joined as `hotwords` in the glossary wording of `text_match`. The response is the IS-8 body: text, language, segments with times, duration, time taken, model name.
- Nothing is written: the samples live in one local variable.

**Done when:** with the fake speech model, `POST /v1/stt` on the committed fixture `tests/fixtures/audio/hey_autobot.wav` returns a body equal in shape to `stt-response.json`; a request with 8 kHz audio gets `400`.

## 5. The runtime seam, routing and chat

**Files:** new `server/src/autobot_server/runtime.py`, `ollama.py`, `route.py`, `chat.py`, `server/tests/test_route.py`, `test_chat.py`.

- `runtime.py`: `Runtime` protocol, `generate(system, messages, schema, tools, stream) -> Answer | Iterator[Token]`; `ollama.py` implements it over `runtime_url` with the standard library `urllib`, `format` set to the JSON schema, `options.num_ctx = llm_context`, `num_parallel` 1, and `keep_alive = -1` so the model stays resident. No pull: a model Ollama does not have is a not-ready reason.
- `route.py`: builds the schema `{agent: enum[names + "none"], mode: enum[modes of that agent + ""], confidence: number, parameters: object}` from the request's agent list, asks the runtime with it, and validates the answer against it again before replying; an agent outside the list can neither be generated nor returned.
- `chat.py`: the IS-9 body; with `stream: true` the tokens go out as SSE (`event: token`, then `event: done` with the token counts); tool calls come back as `tool_calls` and are executed nowhere here.

**Done when:** with the fake runtime, `POST /v1/route` with the contract example yields exactly `route-response.json`, a fake answer naming an agent outside the list is rejected as not ready and never returned, and a streamed chat produces the lines of `chat-stream.txt`.

## 6. Runtime control on `GAMEON1`: `make`, the unit, the token, the models

**Files:** modified `Makefile`; new `deploy/autobot-server.service`, `server/src/autobot_server/control.py`, `fetch_models.py`, `token.py`, `check.py`, `server/tests/test_control.py`; modified `src/autobot/fetch_models.py`; new `src/autobot/server/token.py`, `src/autobot/server/ping.py`; modified `docs/context/INFRASTRUCTURE.md`.

- Targets, each with a `## ` comment: `server-setup` (`.venv-server` with `server[gpu]`), `server-fetch-models` (the `large-v3-turbo` CTranslate2 files into `models/`; the only downloader on that host; it tells the owner the `ollama pull` for `llm_model` and does not run it), `server-up`, `server-down`, `server-status`, `server-logs` (`FOLLOW=1`), `server-install` (writes `~/.config/systemd/user/autobot-server.service` from the template with `@REPO_ROOT@` replaced, enables it and runs `loginctl enable-linger`), `server-check` (loopback: `/health`, `/v1/stt` with `tests/fixtures/audio/hey_autobot.wav`, `/v1/route` and `/v1/chat` with the contract examples; exits non-zero on any failure), `server-token` and, on the NUC, `server-ping` (one `/health` call with the configured URL and token, printed as the watcher would read it). Every `server-*` target but `server-ping` and `server-token` refuses off the configured server hostname.
- `server-token`: on `GAMEON1` it writes a random 32-byte token to `~/.config/autobot-server/token` at `0600` when none exists, and prints the path only; on the NUC it reads the token from a hidden prompt (`getpass`) and writes `secrets/server-token` at `0600`. The owner carries the value between the hosts himself. Neither variant prints the token.
- `control.py` mirrors `autobot.listener.control`: pid file and log under `run/`, `systemctl --user` when the unit is installed.
- `INFRASTRUCTURE.md` gains the unit's name, the token files, the venv, the `make` targets and the owner's hand steps of IS-14 as a checklist, and marks the LAN section as built when it is.

**Done when:** a bare `make` lists every target above; on `GAMEON1`, `make server-install && make server-status` reports the unit running and `make server-check` passes; on the NUC `make server-ping` prints `up` and the contract version.

## 7. The NUC's client, hints and watcher

**Files:** new `src/autobot/server/client.py`, `hints.py`, `watch.py`, `src/autobot/agents/hearing.py` (modified), `tests/server/server_fakes.py`, `test_client.py`, `test_hints.py`, `test_watch.py`, `tests/agents/test_agent_hearing.py` (modified), `tests/agents/agent_fakes.py` (modified).

- `client.py`: `ServerClient` over `urllib` with the bearer token read from `[server] token_file` (never logged), one method per endpoint, each with its IS-4 timeout, the STT timeout computed from the clip's seconds; a timeout or any error raises `ServerUnavailable` once and never retries; `stream_chat` yields tokens and raises when the first token is late. A `Transport` protocol is what the tests fake.
- `hints.py`: the IS-16 list from the config (agent names, commands, modes, yes and no words, action keywords), de-duplicated and cut at `hint_chars`.
- `watch.py`: `ServerWatch` with the IS-23 states, polling by `clock()` from `Services`, back-off from 5 s to 60 s while down, two failures down, one success up, `degraded` when a job is not ready; a missing or unreadable token file and a contract major-version mismatch are each a reason string and the state is `down`. `ready(job)` is what every fallback asks.
- `hearing.py`: `ServerHearing(client, watch, local: LocalHearing)` implements `Hearing`; `transcribe` returns a `Heard(text, source)` where `source` is `server:<model>` or `local:tiny.en`; it asks the server only when `watch.ready("stt")` and falls to `local` on `ServerUnavailable`. Every detail line that quotes a transcript shows its source (IS-15). `FakeHearing` gains a `source`.

**Done when:** in the tests, a fake transport that times out makes `ServerHearing` answer from the fake local hearing with `source == "local:tiny.en"` and the watcher reads `down` after two failures and `up` after one success; a missing token file yields the reason "no server token: run make server-token".

## 8. The main screen and the face show the server

**Files:** new `src/autobot/tui/server_state.py`; modified `src/autobot/tui/app.py`, `src/autobot/tui/screens/main.py`, `src/autobot/tui/face.py`, `src/autobot/tui/face_reactions.py`; new `tests/tui/test_tui_server_state.py`, `tests/tui/test_tui_main.py`.

- `Services` builds one `ServerWatch` and the client; `App.poll_server()` ticks it as `poll_listener` does.
- `server_state.describe(watch)` gives the main screen's line, drawn beside the listener's: `server: up (stt, route, chat)`, `server: degraded: chat not ready (model missing)`, `server: down: no server token: run make server-token`, `server: down: contract 2 is not 1`, or `server: off` when `[server] enabled` is false; `BADGE_ROLES` gains its badge.
- `FaceModel` watches the state change and plays a *notice* reaction with a ticker line "server up" or "server away" (FS-13's network exception, as the spec amends it).

**Done when:** with a fake transport the main screen's state panel shows the server line changing from `down` to `up` within the test's frames, and the face's ticker records the change.

## 9. The routing cascade

**Files:** new `src/autobot/cascade.py`, `src/autobot/embedding.py`, `tests/test_cascade.py`, `tests/test_embedding.py`; modified `pyproject.toml`, `src/autobot/routing.py`, `src/autobot/fetch_models.py`, `src/autobot/tui/router.py`, `src/autobot/tui/tree_game.py`, `src/autobot/tui/screens/routing.py`, `tests/tui/test_tui_router.py`.

- `embedding.py`: `all-MiniLM-L6-v2` as an int8 ONNX file under `[server] embedding_model`, run with `onnxruntime` behind `enforce_offline()` and tokenized with the `tokenizers` package, which `pyproject.toml` gains as a NUC dependency. Both the model and its `tokenizer.json` load from a local path with no network at runtime; `make fetch-models` is the only thing that fetches them. Cosine similarity of the transcript against each route's `command` and `examples`, embedded once per config load. Behind an `Embedder` protocol with a fake.
- `cascade.py`: `decide(transcript, config, embedder, server) -> Decision | None`, running tier 1 (`match_route`), tier 2 (best route above `tier2_threshold`), tier 3 (`client.route` with the IS-9 agent list, when `watch.ready("route")`), and one `admit()` for all three: agent exists, mode among the agent's modes (its routes' modes plus `""`), parameters validate against the definition's `parameters`, confidence at or above the tier's threshold (tier 1 is 1.0). `Decision` carries the tier.
- `Router._listen` calls the cascade after the action keywords; the detail line names the tier (`tier 2: 0.81 -> weather agent`), and `Position` gains `tier` so the map's edge can mark it. When nothing is admitted: `core` is launched with the transcript if `watch.ready("chat")`, otherwise the summary says "I didn't catch that." and the router keeps listening. `Launch` gains `parameters`.
- The Agent routing screen edits `examples` on a rule.

**Done when:** in the tests, "what's it like outside" with no keyword match routes to `weather` by tier 2 on a fake embedder, a fake server answer naming an agent not in the config is refused and the router falls to `core`, and with the fake watcher `down` the same transcript ends in "I didn't catch that."; the embedding test loads the model and tokenizer from a temporary local path with the network blocked.

## 10. Agent tiers and the local model

**Files:** modified `src/autobot/agents/gemini.py`, `src/autobot/agents/base.py`, `src/autobot/agents/question.py`, `src/autobot/tui/face.py`, `src/autobot/tui/agent_edit.py`, `src/autobot/tui/screens/agent_editor.py`; new `src/autobot/agents/local_model.py`, `src/autobot/agents/models.py`, `tests/agents/test_agent_models.py`; modified `tests/agents/test_agent_question.py`, `tests/tui/test_tui_agent_edit.py`.

- `gemini.Model` keeps `ask(prompt, query)` and gains `chat(request: ChatRequest) -> ChatAnswer | Iterator[Token]` (system prompt, messages, `response_schema`, `tools`, `stream`); `AdkModel.chat` runs the messages as one ADK turn with the history as context and returns a whole answer (no stream). `local_model.ServerModel` implements both over `ServerClient`, `chat` streaming when asked.
- `models.py`: `choose(definition, seams, watch) -> Model`, walking `definition.model` then `fallback` in order, skipping `local` while `watch.ready("chat")` is false and `gemini` while no model is chosen; a spoken-line entry ends the walk with `Fallback(line)`, which the agent says. `Seams.model` becomes `Seams.models: Mapping[str, Callable[[Config, Path], Model]]` keyed `local` and `gemini`; `agent_fakes` gives `FakeModel` under both keys.
- `question` uses `choose` with its definition (`model = "gemini"` by default), so its behaviour today is unchanged. Agents with `model = "none"` never call `choose`.
- The Agent editor shows and edits `model`, `fallback`, `parameters` and `tools` as fields of the definition.

**Done when:** a `question` definition with `model = "local"` and the fake watcher `up` answers from the `local` fake and, with it `down` and `fallback = ["gemini"]`, from the `gemini` fake; a fallback that ends in a spoken line is spoken by the rig's voice.

## 11. The core agent, the handoff and tools

**Files:** new `src/autobot/agents/core.py`, `tests/agents/test_agent_core.py`; modified `src/autobot/agents/registry.py`, `src/autobot/agents/tree.py`, `src/autobot/agents/base.py`, `tests/agents/agent_fakes.py`.

- `Core` is an `Experience`: it keeps `messages` on the NUC for the session only (AG-11), sends them whole with the system prompt (the definition's `prompt`) and a `response_schema` that is AG-4's two parts plus an optional `handoff: {agent: enum[gemini agents], query}`, and the definition's `tools`. Before each request the oldest turns are dropped until the estimated tokens fit `llm_context` minus a reply margin.
- Streaming: tokens are gathered into sentences and each sentence is a `Speaker`, so speech starts at the first full sentence; the detail panel shows the time to the first token. After a reply it listens for the next turn for `[server] core_listen_seconds` (10 by default, not the router's `LISTEN_SECONDS`) and ends the session when that time elapses with no speech; `cancel` ends it at once.
- Handoff: when the answer names a `handoff`, the NUC asks that agent's `confirm_request` question if it is on (AG-13), shows a "thinking" cue at once, and calls the `gemini` model through `choose` for that agent, within GC-10's caps; the server is never asked to.
- Tools: a `tool_calls` answer runs each named tool from a `Tools` seam on the NUC (`agent_fakes.FakeTool` in tests) and sends the results back as tool messages, for at most `tool_rounds` rounds, then stops with a trouble line.
- `registry` adds `core` (uses_model, sends_requests); `tree` adds its node at the agents' level, drawn as the default of the level.

**Done when:** on the rig with a fake streaming model, the core agent speaks the first sentence before the stream ends, keeps the previous turn in its next request, ends its session when a fake clock passes `core_listen_seconds` with no speech, hands a `handoff` to the `question` agent's fake after a yes, runs a fake tool for one round, and says "the server is away" when the fake watcher is `down` and the `gemini` fake has no model.

## 12. The evaluation

**Files:** new `server/eval/utterances.jsonl`, `server/src/autobot_server/eval.py`, `server/tests/test_eval.py`; modified `Makefile`.

- `utterances.jsonl`: typed utterances, each with the expected `agent`, `mode` and `parameters`, covering every agent and `none`; no audio and nothing of the owner's voice.
- `eval.py` runs each utterance through `route.py` against the real runtime for the model named by `MODEL=` (default `[server] llm_model`) and the chat example through `chat.py`, and writes `run/server-eval-<model>.json`: route accuracy, JSON validity, time to first token, time to full answer, and whether each stays within IS-4's budget. `make server-eval` runs it on `GAMEON1` only; the tests cover the scoring on a fake runtime.

**Done when:** on `GAMEON1`, `make server-eval MODEL=<candidate>` writes a report file for each of the three candidates of IS-10, and the one chosen is written to `[server] llm_model` by the owner.

## 13. Choices the spec leaves open

1. **The server is standard library HTTP.** `http.server.ThreadingHTTPServer` with the dispatch as a pure function, as the interface is `curses` and the config `tomllib`: no framework, and the tests never open a socket.
2. **The server does not import `autobot`.** `autobot_server.config` reads the `[server]` table itself, so `GAMEON1` installs none of the NUC's audio and cloud dependencies; the two readers are checked against the same shipped file.
3. **GPU memory is read from `nvidia-smi`.** A subprocess behind a protocol, faked in tests, instead of a CUDA Python binding.
4. **Models live under `models/` in the checkout on both hosts**, as `CONVENTIONS.md` places them, fetched by that host's own target.
5. **The token exchange.** `make server-token` creates on `GAMEON1` and takes a hidden paste on the NUC; the owner carries the value. No target copies it over the LAN.
6. **`/v1/tts` answers `501`** with a reserved reason, so a client that asks early gets a clear line, and `/health` says `tts: false`.
7. **The server's tests run in `make test` on the NUC**, because `make setup` installs `server/` without its `gpu` extra and the tests use fakes; this is how IS-12's "no GPU, no model, no network" is kept honest.
8. **Streamed speech is cut at sentence ends** (`.`, `?`, `!` followed by space or end), one `Speaker` per sentence.
9. **No tool ships.** IS-21's mechanism is built and tested with a fake; the first real tool is a later definition change.
10. **Tier 2's model is `all-MiniLM-L6-v2`, int8 ONNX.** About 23 MB, run under `onnxruntime`, which is already a dependency; the `tokenizers` package is the one new NUC dependency, and the model and tokenizer load from a local path only.
11. **The core agent's follow-up wait is its own setting.** `[server] core_listen_seconds`, default 10, so it can be tuned apart from the router's `LISTEN_SECONDS`.

## 14. Out of scope

Text-to-speech on the server (`/v1/tts` is reserved only); an audio-native model (IS-27); the owner's Windows, router and `apt` steps of IS-14 (listed, not done); the firewall rules themselves; the choice of language model (made by `make server-eval`, step 12, after this plan lands); any change to the listener's wake path; the amendments to the context documents and other specs, listed below.

## Settled without asking

- **Derived (IS-17).** Tier 2's embedding model stays on the NUC and is fetched by `make fetch-models`, the NUC's only downloader (`CONVENTIONS.md` "Local-only speech").
- **Derived (IS-5, IS-19).** The core agent's history is session-only and lives in the `Experience`; nothing is written to disk, as no agent keeps state yet (`agents/base.py`).
- **Derived (IS-9, RR-3).** An agent's modes for the route schema are the `mode` values of its `[[routes]]` entries plus the empty mode.
- **Derived (IS-20).** The handoff's closed list is every agent whose `model` is `gemini`; today that is `question`.
- **Default.** `[server] enabled = true` ships, because the watcher backs off to a poll a minute while the server is away and the owner is bringing the host up.
- **Default.** `question` keeps `model = "gemini"` so the agent that works today is unchanged; the owner moves it to `local` in the Agent editor when the evaluation is in.
- **Default.** `server-check` transcribes `tests/fixtures/audio/hey_autobot.wav`, a committed synthetic fixture and not a recording of the owner (TS-5).

## Changes needed elsewhere

- **docs/context/CONSTRAINTS.md.** Amend `CN-1` and `CN-2` as the spec's own list says; step 7's server transcription is switched on in the shipped config only once this lands.
- **docs/context/CONVENTIONS.md.** "One package per host", and "Local-only speech" as speech kept inside the house; also that `server/tests` run under `make test` on fakes.
- **docs/context/SCHEMA.md.** The `[server]` fields of step 1, the agent fields `model`, `fallback`, `parameters`, `tools`, `examples` on routes, and the shapes of `server/contract/`.
- **docs/context/ARCH.md, GLOSSARY.md, AGENTS.md, docs/decisions/0007-…-ADR.md, docs/specs/active/rules-first-routing-SPEC.md, agent-sessions-SPEC.md, autobot-agents-and-the-agent-tree-SPEC.md, autobot-the-face-SPEC.md, autobot-wake-word-training-studio-SPEC.md.** As the spec's "Changes needed elsewhere" lists them.
- **docs/specs/active/autobot-gpu-inference-server-SPEC.md.** `describes:` gains `server/**` once step 3 lands.

## Open Questions

None.
