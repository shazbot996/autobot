---
run: schiele/0122
at: 2026-11-27T18:34:41.589Z
mode: apply
agent: apply
model: claude:claude-opus-5-5
target: docs/plans/active/agents-and-the-agent-tree-PLAN.md
template: apply
prompt-sha: 4c26ba3523a4
template-sha: bf7cd0cb8062
commit: 22fbc9a+
attachments: []
read:
  - AGENTS.md
  - docs/plans/active/agents-and-the-agent-tree-PLAN.md
  - src/autobot/agents/__init__.py
  - src/autobot/agents/base.py
  - src/autobot/agents/registry.py
  - src/autobot/agents/response.py
  - src/autobot/agents/speech.py
  - tests/agents/__init__.py
  - tests/agents/agent_fakes.py
  - tests/agents/test_agent_registry.py
  - tests/agents/test_agent_response.py
  - src/autobot/tui/echo.py
  - src/autobot/tui/router.py
  - tests/tui/test_tui_router.py
outcome: success
usage:
  input: 1343647
  output: 19319
  peak: 92464
  window: 1000000
cost: 0
harness:
  provider: claude
  kind: sdk
  workspace: self
  access: build
  turns: 24
  sandbox: true
  changed:
    - src/autobot/agents/confirm.py
    - src/autobot/agents/words.py
    - tests/agents/test_agent_confirm.py
    - tests/agents/test_agent_words.py
---

## Result

I applied step 3 of 13, the only step in scope. Steps 1 and 2 were already done, and steps 4 to 13 were not started.

The step's tests pass (27 in `tests/agents`), and so do `make typecheck`, `ruff check` and `ruff format --check` over `src` and `tests`. `make test` gives 1154 passed and 14 failed, the same 14 that failed before step 1 and still not caused by this work: 13 socket tests the sandbox refuses with `PermissionError`, and `test_the_committed_config_is_a_fresh_install`.

## Steps

### 3. Words: global action keywords and confirmation answers: done, verified, with one deviation

**Deviation: how a word is matched.** The plan says `match_word` uses the matching rule of `routing.contains_command`, and also that "cancellation" must not match `cancel`. Those two can't both hold. I ran `contains_command("cancellation", "cancel")` and it returns `True`, because the rule only checks where a match starts. It also finds `no` in "nothing", which would turn a stray "nothing" into a "no" answer.

So `words.stands_in` keeps that rule but adds one condition: the match must also end where a word ends. "cancel", "Cancel!" and "could you cancel" match; "cancellation", "precancel" and "nothing" don't. `routing.contains_command` is unchanged, so the routing rules behave as before. If you'd rather keep the rule exactly as written, the "cancellation" check in the "Done when" has to go.

**`words.py`:**
- `match_word` gives the first word of a list that appears in the transcript.
- `action_keyword` tries the `[words] actions` list.

**`confirm.py`:**
- `answer` gives `NO` when a no word or an action keyword is in the transcript, otherwise `YES` for a yes word, otherwise `NOTHING`.
- `Confirmation` runs in `tick` and never blocks:
  - It speaks the question through `Speaker`, which holds the listener for the clip's length.
  - It then clears whatever was heard before the question, plays *listening*, and reads what is heard next.
  - It ends `yes`, `no`, or `timed out` after `router.LISTEN_SECONDS`.
  - A sentence that is neither yes nor no adds one detail line.

**Two choices the plan didn't cover:**
- **Unspoken question:** a question that can't be spoken is shown in the summary, its reason goes in the detail panel, and it still listens for an answer.
- **Where `LISTEN_SECONDS` is read:** `confirm.py` reads it from the router module each time it's needed, rather than importing the constant once. That way, when step 6 has the router import `confirm`, the two modules won't fail on a circular import.

**How I checked:** `pytest tests/agents`, 27 passed.
- `test_agent_words.py` covers "cancel", "Cancel!" and "could you cancel" matching, and "cancellation" not matching.
- `test_agent_confirm.py` checks that "you betcha" is `YES`, "nah" and "cancel" are `NO`, and "maybe later" is `NOTHING`. Run with a fake voice and a fake clock, a `Confirmation`:
  - speaks its question once and holds the listener for the clip's length;
  - ends `yes` on "sure" and `no` on "nope";
  - is still waiting at 9.5 silent seconds and ends `timed out` once ten seconds have passed.

**Test fakes:** this test brings its own small audio and runner fakes. `tui_fakes` sits in `tests/tui`, which isn't importable from `tests/agents` when the agent tests are collected first.

### 4 to 13: not started
Outside the scope of this run.

## Files changed

- `src/autobot/agents/words.py` (new): `stands_in`, `match_word`, `action_keyword`.
- `src/autobot/agents/confirm.py` (new): `Answer`, `answer`, and the `Confirmation` state machine.
- `tests/agents/test_agent_words.py` (new): word matching and action keyword tests.
- `tests/agents/test_agent_confirm.py` (new): answer tests, and a `Confirmation` run on a fake face and clock.

## For you to do

- Decide on the word-matching deviation above: keep the word-end condition, or change the plan's "Done when".
- Run `make test` outside the sandbox so the socket tests can run, and fix `test_the_committed_config_is_a_fresh_install`, which expects the theme `light` while the committed file says `sun`.
- Mark step 3 as done in the plan's frontmatter yourself; I'm not allowed to edit the plan.
