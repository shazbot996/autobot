---
run: schiele/0138
at: 2026-11-27T21:35:25.848Z
mode: apply
agent: apply
model: claude:claude-opus-5-5
target: docs/plans/active/gpu-inference-server-PLAN.md
template: apply
prompt-sha: 6f8374b37f17
template-sha: bf7cd0cb8062
commit: 259d5c0+
attachments: []
read:
  - AGENTS.md
  - docs/plans/active/gpu-inference-server-PLAN.md
  - src/autobot/config.py
  - config/autobot.toml
  - tests/test_config_agents.py
  - docs/specs/active/autobot-gpu-inference-server-SPEC.md
  - tests/test_config_gcp.py
outcome: success
usage:
  input: 3675795
  output: 39147
  peak: 138011
  window: 1000000
cost: 0
harness:
  provider: claude
  kind: sdk
  workspace: self
  access: build
  turns: 66
  sandbox: true
  changed:
    - config/autobot.toml
    - src/autobot/config.py
    - src/autobot/routing.py
    - tests/agents/test_agent_question.py
    - tests/agents/test_agent_tree.py
    - tests/test_config_agents.py
    - tests/test_config_server.py
---

## Result

Applied step 1 of 13, the only step this run was asked to do. Steps 2 to 12 were not started, as instructed. Step 1 needed small changes to three files it does not name; they are explained below.

## Steps

### 1. The `[server]` table and the agent fields: done, verified
- **`src/autobot/config.py`:**
  - New `ServerConfig` with every key from the plan, a default for each, and unknown keys refused. `url` and `runtime_url` must be `http://host:port`, checked by a new `url_complaint`. Timeouts, poll intervals and `core_listen_seconds` must be numbers above zero. The two thresholds must be above 0 and at most 1. `poll_down_max_seconds` may not be below `poll_down_min_seconds`. It has helpers for the token path, the embedding path, the host and the port.
  - `AgentConfig` gains `model`, `fallback`, `parameters` and `tools`, using two new shapes, `ParameterConfig` and `ToolConfig`. A spoken line in `fallback` is allowed only as the last entry, and each parameter's `values` are checked against its type.
  - `RouteConfig` gains `examples`.
  - `AGENT_CORE` joins `ROUTE_AGENTS` and `DEFAULT_AGENTS` with `model = "local"`, `fallback = ("gemini", "the server is away")`, a first personality prompt and `listening = True`. `question` now defaults to `model = "gemini"`.
- **`config/autobot.toml`:** gains a commented `[server]` table, `model = "gemini"` on `question`, the `core` agent's table and no route for it. The agents and routes comments now describe the new keys.
- **Tests:** new `tests/test_config_server.py` (44 cases), and new cases in `tests/test_config_agents.py`.
- **Confirmed by:**
  - Loading the shipped file returns `server.url == "http://192.168.86.53:8300"`, `server.core_listen_seconds == 10` and `agent("core").model == "local"`.
  - `make typecheck` passes.
  - `ruff check` and `ruff format --check` over `src` and `tests` pass.
  - `make test`: 1444 passed and 15 failed. These are the same 15 that failed before I changed anything:
    - 13 socket tests that the sandbox refuses;
    - `test_the_committed_config_is_a_fresh_install`, because the shipped theme is `sun`;
    - `test_the_committed_definitions_seed_what_the_agents_need`, because the shipped `camera` has `confirm_entry = true`.

  The last two come from the owner's current config. I left them alone.
- **`make lint` exits non-zero,** but only on the untracked `.claude/loop.md`, which is not part of this change.

**Files outside the step.** Adding `core` to `ROUTE_AGENTS` broke three tests. Each assumes every agent in that list has a note, a node in the tree and code in the registry, and `core` has only its definition until steps 9 and 11. I made the smallest fix in each:
- `src/autobot/routing.py`: added a note for `core` to `AGENT_NOTES`.
- `tests/agents/test_agent_tree.py`: the tree is now compared with `ROUTE_AGENTS` minus `core`, which has no rule.
- `tests/agents/test_agent_question.py`: the registry is now compared with `ROUTE_AGENTS` minus `core`, which has no code yet. Step 11 should put this back to `tuple(AGENTS) == ROUTE_AGENTS` when it registers `core`.

**Values the plan left open, and what I chose:**
- `tier2_threshold = 0.6` and `tier3_threshold = 0.7`.
- `hint_chars = 500` and `tool_rounds = 3`.
- `stt_compute_type` defaults to `int8_float16`.
- `runtime` accepts only `ollama` for now.
- `embedding_model = "models/embedding/all-MiniLM-L6-v2/model_int8.onnx"`. Step 9's fetch should use this path, or change it.
- `core_listen_seconds` is a number, not a whole number, so `10` loads as `10.0`.
- `llm_model` may be empty; every other string key may not.

### 2 to 12: not started
Outside this run's scope.

## Files changed

- `src/autobot/config.py`: `ServerConfig`, the new agent and route fields with their checks, and the `core` agent.
- `config/autobot.toml`: the `[server]` table, `model` on `question`, and the `core` agent's table.
- `tests/test_config_server.py`: new; tests for the `[server]` table, each refusal naming its key.
- `tests/test_config_agents.py`: covers seven agents, the new fields, the fallback order, parameter types, tools and route examples.
- `src/autobot/routing.py`: one `AGENT_NOTES` entry for `core` (outside the step).
- `tests/agents/test_agent_tree.py`: leaves out `core`, which has no rule (outside the step).
- `tests/agents/test_agent_question.py`: leaves out `core`, which has no registry entry yet (outside the step).

## For you to do

- `docs/context/SCHEMA.md` still needs the new `[server]` and agent fields, as the plan's "Changes needed elsewhere" says.
- Run `make test` outside the sandbox to confirm the 13 socket tests pass there.
