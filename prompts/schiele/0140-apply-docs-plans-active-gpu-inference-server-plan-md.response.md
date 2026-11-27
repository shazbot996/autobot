---
run: schiele/0140
at: 2026-11-27T21:45:11.359Z
mode: apply
agent: apply
model: claude:claude-opus-5-5
target: docs/plans/active/gpu-inference-server-PLAN.md
template: apply
prompt-sha: aa3b94d49310
template-sha: bf7cd0cb8062
commit: dff8eda+
attachments: []
read:
  - AGENTS.md
  - docs/plans/active/gpu-inference-server-PLAN.md
  - src/autobot/config.py
  - config/autobot.toml
  - tests/test_config_server.py
  - tests/test_config_agents.py
  - pyproject.toml
  - docs/specs/active/autobot-gpu-inference-server-SPEC.md
  - docs/context/CONVENTIONS.md
  - src/autobot/wakeword/dataset.py
  - src/autobot/agents/response.py
outcome: success
usage:
  input: 2354790
  output: 37009
  peak: 118532
  window: 1000000
cost: 0
harness:
  provider: claude
  kind: sdk
  workspace: self
  access: build
  turns: 35
  sandbox: true
  changed:
    - server/contract/chat-request.json
    - server/contract/chat-response.json
    - server/contract/chat-stream.txt
    - server/contract/contract.json
    - server/contract/health.json
    - server/contract/route-request.json
    - server/contract/route-response.json
    - server/contract/stt-request.json
    - server/contract/stt-response.json
    - src/autobot/server/__init__.py
    - src/autobot/server/contract.py
    - tests/server/__init__.py
    - tests/server/test_contract.py
---

## Result

I applied step 2, the only step this run was asked to do. Steps 3 to 12 were not started, as instructed. The step is done and checked: the contract tests pass, the type check passes, and lint passes except on one file this change doesn't touch.

## Steps

### 1. The `[server]` table and the agent fields: already done (run schiele/0138)
Not touched in this run.

### 2. The contract: done, verified
- **`server/contract/`** holds the nine files the plan names:
  - `contract.json` is `{"contract_version": "1"}`, the one place the version lives.
  - There is one full example each for health, the speech-to-text request and response, the route request and response, and the chat request and response.
  - `chat-stream.txt` is one streamed answer: five `event: token` events, then `event: done`.
- **Example contents:**
  - The speech-to-text request carries a real 10 ms WAV of silence (16 kHz, mono, 16-bit), base64-encoded in an `audio_base64` field beside `hints`. It is not a recording of anyone.
  - The chat request covers a full tool round: an assistant message with a tool call, then a tool message answering it. It also carries a `response_schema` (the two parts of AG-4 plus an optional `handoff` limited to `question`) and one `tools` entry.
- **`src/autobot/server/contract.py`:**
  - A frozen dataclass for every shape, with `from_json`/`to_json`, plus `contract_dir()` and `contract_version()`.
  - `iter_stream` reads streamed chat events and `format_event` writes them.
  - Errors are a `ContractError` with a one-line message.
  - Every shape's JSON keys are its field names. `from_json` refuses an unknown key, a missing required key, or a value of the wrong type, and it does not accept `true` where an integer is expected.
- **`tests/server/test_contract.py`** has 33 tests:
  - Every example is read through its shape, written back, and must equal the file after both are normalised.
  - The stream file must be written back exactly as it stands.
  - A test fails if a file appears in `server/contract/` without a shape.
  - Others cover a renamed key, a missing key and wrong types, the WAV's format, that the route answer names an agent and mode from the request, and that health reports the version in `contract.json`.
- **Confirmed by:**
  - `pytest tests/server`: 33 passed.
  - "Done when": I renamed `SttResponse.elapsed_seconds` to `took_seconds` in the dataclass and 3 tests failed; with it put back, all 33 passed. Renaming a key in each example is a test of its own.
  - `make typecheck`: no issues in 162 files.
  - `ruff check .` passes and `ruff format --check` passes on everything except `.claude/loop.md`.
  - `make test`: 1477 passed and 15 failed. These are the same 15 the previous run recorded before any change: 13 socket tests the sandbox refuses, `test_the_committed_config_is_a_fresh_install` and `test_the_committed_definitions_seed_what_the_agents_need`.
- **Still fails:** `make lint` exits non-zero, but only because ruff cannot read the untracked `.claude/loop.md` (permission denied). That file is not part of this change.

**Choices the plan left open:**
- **Health:** the three jobs sit under `jobs` (`stt`, `route`, `chat`), each with `ready`, `model` and `reason`, and `tts: false` is a top-level field. Memory figures are whole MiB: `budget` holds the five figures of IS-10, adding up to 10752, and `gpu` holds `used_mib` and `free_mib`.
- **Times** are named with `_seconds`, as in `[server]`: `start_seconds`, `end_seconds`, `duration_seconds`, `elapsed_seconds`.
- **Route request:** each agent's `modes` leaves out the empty mode, which every agent has. The server adds it when building the schema in step 5.
- **Chat response:** `{message, model, tokens}`. The `message` is an ordinary assistant message, so the NUC can add it to the conversation as it is. Its `content` is the model's text, which is JSON text when a `response_schema` was sent; the NUC takes it apart.
- **Omitted fields:** `tool_calls`, `tool_call_id`, `response_schema` and `tools` are left out of the JSON when empty, and may be absent. `stream` is always written.
- **Stream end:** the `done` event carries `model`, `tokens` and, when there are any, `tool_calls`.
- **Example model names** are `large-v3-turbo` and `qwen3:8b`. These are examples only; `make server-eval` still chooses the language model.

### 3 to 12: not started
Outside this run's scope.

## Files changed

- `server/contract/contract.json`: new; the contract version.
- `server/contract/health.json`: new; the `/health` example.
- `server/contract/stt-request.json`: new; the speech-to-text request, with a base64 WAV and hints.
- `server/contract/stt-response.json`: new; the speech-to-text response.
- `server/contract/route-request.json`: new; a transcript and the list of agents that can be routed to.
- `server/contract/route-response.json`: new; the route answer.
- `server/contract/chat-request.json`: new; the chat request, with a tool round, the response schema and a tool.
- `server/contract/chat-response.json`: new; a whole chat answer.
- `server/contract/chat-stream.txt`: new; one streamed answer as SSE text.
- `src/autobot/server/__init__.py`: new; the package's docstring.
- `src/autobot/server/contract.py`: new; the shapes, JSON in and out, the stream reader and writer, `contract_dir()` and `contract_version()`.
- `tests/server/__init__.py`: new, empty, as in the other test packages.
- `tests/server/test_contract.py`: new; checks every example both ways, plus the refusal and stream tests.

## For you to do

- Run `make test` and `make lint` outside the sandbox to confirm the 13 socket tests and the `.claude/loop.md` read pass there.
- `docs/context/SCHEMA.md` still needs the shapes of `server/contract/`, as the plan's "Changes needed elsewhere" says.
- The plan's frontmatter still shows step 2 as pending; I was not allowed to edit the plan.
