---
run: schiele/0120
at: 2026-11-27T18:29:17.390Z
mode: apply
agent: apply
model: claude:claude-opus-5-5
target: docs/plans/active/agents-and-the-agent-tree-PLAN.md
template: apply
prompt-sha: 59dcb2a192df
template-sha: bf7cd0cb8062
commit: 939fdae+
attachments: []
read:
  - AGENTS.md
  - docs/plans/active/agents-and-the-agent-tree-PLAN.md
  - src/autobot/tui/echo.py
  - src/autobot/tui/router.py
  - tests/tui/test_tui_router.py
  - src/autobot/config.py
  - src/autobot/routing.py
  - src/autobot/tui/config_edit.py
  - config/autobot.toml
  - tests/test_config.py
  - tests/test_routing.py
  - tests/tui/test_tui_config_edit.py
  - docs/specs/active/autobot-agents-and-the-agent-tree-SPEC.md
  - src/autobot/tui/face.py
  - tests/tui/test_tui_echo.py
outcome: success
usage:
  input: 2193345
  output: 27585
  peak: 121179
  window: 1000000
cost: 0
harness:
  provider: claude
  kind: sdk
  workspace: self
  access: build
  turns: 29
  sandbox: true
  changed:
    - src/autobot/agents/__init__.py
    - src/autobot/agents/base.py
    - src/autobot/agents/registry.py
    - src/autobot/agents/response.py
    - src/autobot/agents/speech.py
    - src/autobot/tui/echo.py
    - src/autobot/tui/router.py
    - tests/agents/__init__.py
    - tests/agents/agent_fakes.py
    - tests/agents/test_agent_registry.py
    - tests/agents/test_agent_response.py
    - tests/tui/test_tui_router.py
---

## Result

I applied step 2 of 13, the only step in scope. Steps 3 to 13 were not started.

`make typecheck` passes, and `ruff check` and `ruff format --check` pass over `src` and `tests`. `make test` gives 1139 passed and 14 failed. Those 14 are the same ones that failed before step 1 and are not caused by this change:
- **Thirteen socket tests:** the sandbox refuses them with `PermissionError: Operation not permitted`.
- **`test_the_committed_config_is_a_fresh_install`:** it expects the theme `light`, but the committed file says `sun`.

## Steps

### 2. The agent shape, the registry and the two-part response: done, verified
- **`agents/base.py`:** defines `Launch` (the agent's definition, the rule's mode and the matched transcript) and the `Agent` protocol: `name`, `about`, `uses_model`, `sends_requests`, `settings` and `make`. Its docstring says where an agent's state will live (AG-11).
  - **Change from the plan:** `make` takes one `Launch`, not `(definition, mode)` as the protocol bullet says. The plan's own router line passes `agent.make(Launch(...))`, and `Launch` carries the definition and the mode as well as the transcript.
- **`agents/response.py`:**
  - `Response(summary, detail)` holds the two parts of an answer.
  - `layout` fits the detail to the panel: it folds each line at word breaks, cuts a word that is too long, and keeps the first `rows` lines.
  - `parse_model_response` reads the JSON shape, including one inside a fenced block. Any other text gives its first sentence as the summary and its lines as the detail.
- **`agents/speech.py`:** `Speaker` does what the echo did by hand: show and prepare the line, wait for the render, hold the listener, play it with the eyes *speaking*, and finish when playback ends. A failure ends up as one line in `error`. A `held` helper keeps the old hold rule: a listener that is not running is not trouble, and any other hold failure is one detail line.
- **The echo:** its speaking step now runs on `Speaker`. Its playback step uses `held`. `LISTENER_NOT_RUNNING` can still be imported from `autobot.tui.echo`.
- **`agents/registry.py`:** `AGENTS` holds `EchoAgent`. Its `about` is the echo's `AGENT_NOTES` text, it neither uses a model nor sends requests, and it has no settings of its own.
- **The router:** it now takes its agents from the registry and calls `agent.make(Launch(face.config.agent(name), route.mode, transcript))`.
- **`tests/agents/agent_fakes.py`:** holds `FakeModel`, `FakeLookup`, `FakeLaunch` and `FakeSecrets`, all unused until later steps. `FakeLookup` is generic over what it answers, because the `Forecast` type only arrives in step 8.

**How I checked:**
- **Registry tests:** the echo is registered under its own name, every registered name is in `ROUTE_AGENTS`, and `make` gives an experience that is not done.
- **Response tests:** a JSON answer gives its two parts, a fenced answer is unwrapped, and a plain-text answer still gives both parts. A twelve-line detail laid out in a 5×40 panel comes out as five lines of at most forty characters.
- **`tests/tui/test_tui_echo.py`:** passes and is unchanged (`git diff --stat` shows nothing for it).
- **`tests/tui/test_tui_router.py`:** passes after updating its fake to the agent shape. A new test checks the agent receives its definition and the transcript in its `Launch`.

### 3 to 13: not started
Outside the scope of this run.

## Files changed

- `src/autobot/agents/__init__.py` (new): package docstring.
- `src/autobot/agents/base.py` (new): `Launch` and the `Agent` protocol, plus where an agent's state will live.
- `src/autobot/agents/response.py` (new): `Response`, `layout` and `parse_model_response`.
- `src/autobot/agents/speech.py` (new): `Speaker`, `held` and `LISTENER_NOT_RUNNING`.
- `src/autobot/agents/registry.py` (new): `EchoAgent` and `AGENTS`.
- `src/autobot/tui/echo.py`: the speaking step runs on `Speaker` and playback uses `held`; the old hand-written steps are removed.
- `src/autobot/tui/router.py`: takes agents from the registry and launches them with a `Launch`.
- `tests/agents/__init__.py` (new): empty package marker.
- `tests/agents/agent_fakes.py` (new): the four fakes for later agent tests.
- `tests/agents/test_agent_registry.py` (new): registry tests.
- `tests/agents/test_agent_response.py` (new): parsing and layout tests.
- `tests/tui/test_tui_router.py`: the fake agent now has the agent shape; added the launch test.

## For you to do

- Run `make test` and `make lint` outside the sandbox. The socket tests need it, and so does `ruff format --check .`, which the sandbox stops from reading `.claude/loop.md`.
- Fix `test_the_committed_config_is_a_fresh_install`: either the test or the theme in `config/autobot.toml` needs to change.
- Mark step 2 as done in the plan's frontmatter yourself: I was not allowed to edit the plan.
