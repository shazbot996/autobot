---
run: schiele/0176
at: 2026-11-28T15:32:28.897Z
mode: apply
agent: apply
model: claude:claude-opus-5-5
target: docs/plans/active/edge-control-plane-PLAN.md
template: apply
prompt-sha: 38d86b026266
template-sha: bf7cd0cb8062
commit: f340aaf+
attachments: []
read:
  - AGENTS.md
  - docs/plans/active/edge-control-plane-PLAN.md
  - src/autobot/config.py
  - config/autobot.toml
  - tests/test_config_reflexes.py
  - src/autobot/listener/audio.py
  - src/autobot/tui/state.py
outcome: success
usage:
  input: 551828
  output: 11760
  peak: 71591
  window: 1000000
cost: 0
harness:
  provider: claude
  kind: sdk
  workspace: self
  access: build
  turns: 10
  sandbox: true
  changed:
    - src/autobot/reflexes.py
    - tests/test_reflexes.py
---

## Result

I applied step 2 only, as you scoped it. The new matching and scrub code is written, its 35 tests pass, and the type check and lint over `src` and `tests` pass. Steps 3 to 15 were not started. The full suite still has 15 failures, the same ones as after step 1, and none of them come from this change.

## Steps

### 1. Configuration: `[[reflexes]]` and the shared timings: done in an earlier run (schiele/0174)

### 2. Reflex matching and the scrub, as pure code: done, verified
I added `src/autobot/reflexes.py`. It makes no network call, uses no audio and loads no model. It has three functions:

- **`match_reflex(transcript, wake_phrases, reflexes)`** normalises the transcript with `autobot.routing.normalise` and removes the spaces. It then checks whether what is left is exactly a wake phrase followed by a reflex phrase. If more than one reflex fits, the first one listed wins.
- **`scrub(text, wake_phrases, reflexes)`** looks for the pair word by word, across punctuation and sentence ends, and drops only the wake phrase:
  - When the wake phrase has a space before it, the space goes with it: "Say autobot. Cancel now." becomes "Say. Cancel now."
  - When it starts the text, it takes everything up to the reflex phrase with it: "Autobot, cancel." becomes "cancel."
- **`ends_with_wake(sentence, wake_phrases)`** tells whether a sentence's last words are a wake phrase. Step 13's sentence cut will use it.

The plan left one thing open, so I chose: a wake phrase or reflex phrase counts only when it fills whole words. "auto bot" counts as `autobot`, but "autobots cancel" is neither a reflex nor scrubbed. The reflex match needs the whole utterance to be exact, so a longer word could never set one off anyway.

`tests/test_reflexes.py` covers the "Done when" cases:
- "Autobot, cancel." and "auto bot cancel" match.
- "hey autobot cancel" and "tell autobot to cancel it" do not match.
- `scrub("Say autobot cancel to stop")` gives "Say cancel to stop".
- `scrub("Say autobot. Cancel now.")` gives "Say. Cancel now."

It also tests reflexes of several words, more than one wake phrase, every pair in a text being dropped, text that must stay as it was, and `ends_with_wake`.

**Checks:**
- `pytest tests/test_reflexes.py`: 35 passed.
- `make typecheck`: no issues.
- `ruff check src tests` and `ruff format --check src tests`: clean. As in step 1, plain `make lint` fails only because the sandbox won't let ruff read `.claude/loop.md`.
- `make test`: 1854 passed, 15 failed. These are the same 15 as after step 1:
  - 13 can't open Unix sockets in the sandbox (`PermissionError`).
  - 2 test the committed config and expect values it doesn't have: `theme = "sun"` instead of `"light"`, and camera `confirm_entry = true` instead of false.

### 3. to 15.: not started
Outside the scope of this run.

## Files changed

- `src/autobot/reflexes.py`: new; holds `match_reflex`, `scrub` and `ends_with_wake`.
- `tests/test_reflexes.py`: new; tests for all three functions.

## For you to do

- Run `make test` and `make lint` outside the sandbox to confirm the socket tests and the lint pass there.
- The two committed-config test failures from step 1 are still open: either the tests or the config values need to change.
