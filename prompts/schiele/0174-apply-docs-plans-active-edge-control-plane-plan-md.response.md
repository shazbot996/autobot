---
run: schiele/0174
at: 2026-11-28T15:24:28.498Z
mode: apply
agent: apply
model: claude:claude-opus-5-5
target: docs/plans/active/edge-control-plane-PLAN.md
template: apply
prompt-sha: a28e1f3a73c9
template-sha: bf7cd0cb8062
commit: 10bbe29
attachments: []
read:
  - AGENTS.md
  - docs/plans/active/edge-control-plane-PLAN.md
  - src/autobot/config.py
  - config/autobot.toml
  - docs/specs/active/autobot-edge-control-plane-SPEC.md
outcome: success
usage:
  input: 1259957
  output: 13638
  peak: 95937
  window: 1000000
cost: 0
harness:
  provider: claude
  kind: sdk
  workspace: self
  access: build
  turns: 19
  sandbox: true
  changed:
    - config/autobot.toml
    - src/autobot/config.py
    - tests/test_config_reflexes.py
---

I've done step 1 only, as scoped: `[[reflexes]]`, `[session]` and `[audio] raw_input_device` now load and validate. The config tests that fail predate my change. Type check and lint pass, and every other test failure comes from the sandbox.

## Result

Applied step 1 of 16. Steps 2–15 were not started (out of scope for this run).

## Steps

### 1. Configuration: `[[reflexes]]` and the shared timings: done, verified
- **New types in `config.py`:** `ReflexConfig(phrase, action, confirm=True, confirm_question="")`, `REFLEX_ACTIONS`, a default question for each action, and `DEFAULT_REFLEXES`, which are the five reflexes of EC-8, all asking first.
- **New settings:** `SessionConfig(confirm_seconds=5.0, idle_seconds=5.0)` for the `[session]` table, and `Config.reflexes` and `Config.session`.
- **Raw microphone:** `AudioConfig.raw_input_device` defaults to `""` and is checked like the other devices only when it is set.
- **Refusals:** the loader refuses each of these with one line:
  - an empty phrase, or one with no words;
  - a phrase given twice;
  - a phrase equal to a `[[routes]]` command, naming the rule and its agent;
  - an unknown `action`;
  - an unknown key;
  - a non-boolean `confirm`;
  - a `[session]` timing that is not above 0 and at most 60.
- **Phrase matching:** phrases are compared after the same normalisation as `autobot.routing.normalise`. I copied that normalisation into `config.py` as `_phrase_key`, because `routing.py` imports `config` and the reverse import would be circular.
- **Committed file:** `config/autobot.toml` now has `[session]` and the five `[[reflexes]]` entries, with comments.

**Checks:**
- `tests/test_config_reflexes.py` passes. It shows the committed file loading five reflexes, all with `confirm=True`, and `confirm_seconds == idle_seconds == 5.0`. It also shows a reflex phrase of `weather` failing with one line naming the `[[routes]]` rule `'weather'` and its agent.
- `make typecheck` passes.
- `ruff check src tests` and `ruff format --check src tests` pass. `make lint` itself fails only because the sandbox will not let ruff read `.claude/loop.md`.
- `make test`: 1819 passed, 15 failed, none from this change:
  - 13 are `PermissionError: Operation not permitted` from opening Unix sockets in the sandbox: 10 in `tests/listener/test_events.py`, 2 in `tests/tui/test_tui_monitor.py`, and 1 in `tests/listener/test_service.py`.
  - 2 fail on values already in `config/autobot.toml` that I didn't touch. `tests/test_config.py::test_the_committed_config_is_a_fresh_install` expects `theme = "light"` but the file has `"sun"`. `tests/test_config_agents.py::test_the_committed_definitions_seed_what_the_agents_need` expects camera `confirm_entry` to be false but it is `true`.

### 2. to 15.: not started
Outside the scope of this run.

## Files changed

- `src/autobot/config.py`: adds the reflex and session types and defaults, their loaders and refusals, and `[audio] raw_input_device`.
- `config/autobot.toml`: adds the `[session]` table and the five `[[reflexes]]` entries, with comments.
- `tests/test_config_reflexes.py`: new tests for the defaults, the committed file, every refusal, the session limits and `raw_input_device`.

## For you to do

- Run `make test` and `make lint` outside the sandbox to confirm the socket tests and the lint pass there.
- Decide whether the two committed-config tests or the file's `theme = "sun"` and camera `confirm_entry = true` should change.
- `raw_input_device` is not in `config/autobot.toml` yet, because I didn't want to guess the raw node's name. Step 3 or 15 should add it with the real node name.
