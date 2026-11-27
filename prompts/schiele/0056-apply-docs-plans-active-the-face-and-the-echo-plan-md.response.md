---
run: schiele/0056
at: 2026-11-27T05:21:47.080Z
mode: apply
agent: apply
model: claude:claude-opus-5-5
target: docs/plans/active/the-face-and-the-echo-PLAN.md
template: apply
prompt-sha: ec21427671ce
template-sha: bf7cd0cb8062
commit: 8867681+
attachments: []
read:
  - AGENTS.md
  - docs/plans/active/the-face-and-the-echo-PLAN.md
  - src/autobot/listener/service.py
  - src/autobot/listener/control.py
  - tests/listener/test_service.py
  - src/autobot/listener/events.py
  - tests/listener/test_events.py
  - src/autobot/tui/runner.py
outcome: success
usage:
  input: 884265
  output: 16253
  peak: 91495
  window: 1000000
cost: 0
harness:
  provider: claude
  kind: sdk
  workspace: self
  access: build
  turns: 25
  sandbox: true
  changed:
    - src/autobot/listener/control.py
    - src/autobot/listener/service.py
    - tests/listener/test_control.py
    - tests/listener/test_service.py
---

## Result

I applied step 2 of 9. Step 1 was already in place from the previous run, and steps 3 to 9 were outside this run's scope. `make typecheck` passes and every new test passes. The test and lint runs still exit with errors, but none of those failures come from this change (details under "Checks").

## Steps

### 1. The listener publishes transcripts: done in the previous run (schiele/0054), not touched here

### 2. The listener can be told to hold its ears: done, verified (except on the NUC)

**`service.py`**
- **New constants:** `HOLD_REQUEST = run/hold.request` and `MAX_HOLD_SECONDS = 60.0`.
- **`Hold` class:** it takes a clock.
  - `extend(S)` sets the deadline to now + S + `ACK_TAIL_SECONDS`, and only moves it later, never earlier.
  - `active` says whether the hold is on now.
  - `ended()` returns true once, the first time it is called after the hold runs out.
- **`answer_hold_request(hold, run_dir)`:** it reads S, sets the hold and logs `hold: S s`.
  - A request that is missing, not a number, 0 or less, over 60, `nan` or `inf` is logged as `hold refused: …` and leaves no hold.
  - Either way it removes the request file, and it never raises.
- **Signal and main loop:** `SIGUSR2` only sets a flag. The main loop answers the request before the next frame, after the snapshot check.
  - While the hold lasts, frames are still read (so the stall check still works) and are then dropped.
  - On the first frame after the hold ends, the listener calls `microphone.discard()`, `pipeline.reset()` and `recent.reset()`, and drops that frame too.
  - The listener's own "Yes?" still plays inside the loop iteration of the wake, so the next request is only seen after it.
- **Cleanup:** `run/hold.request` and its `.tmp` are removed when the listener starts and when it stops.
- **Docstring:** the module docstring now has a "The hold" paragraph.

**Additions the plan didn't name, needed to do the above:**
- `HoldError`, a one-line error for a request that can't be taken.
- `parse_hold_seconds`, which checks the number.
- `hold_path`, `clear_hold_files` and `hold_files_cleared`, which mirror the snapshot helpers.

**`control.py`**
- **New `hold --seconds S` subcommand:** argparse refuses anything that isn't a number greater than 0 and at most 60.
- **`cmd_hold`:**
  - It writes the request through a temporary file and a rename, sends `SIGUSR2`, and waits up to `HOLD_TIMEOUT_SECONDS = 5.0` for the listener to remove the file.
  - On success it prints `listener holding for S s` and exits 0.
  - Otherwise it exits 1 with `the listener is not running` or `the listener did not take the hold within 5 s`.
- **Docstring:** the subcommand list and the module docstring now cover `hold`.

**What confirmed it**
- `make typecheck`: no issues in 80 source files.
- Each "Done when", and the new test that covers it:
  - **The hold is active for S + 0.25 s and off after, with a fake clock:** `test_a_hold_lasts_its_seconds_and_the_acknowledgements_tail`. A second test, `test_a_new_hold_extends_and_never_shortens`, checks that a new request only moves the deadline later.
  - **A bad request leaves no hold and removes the file:** `test_a_bad_hold_request_leaves_no_hold_and_removes_the_file`, run with 7 different bad values.
  - **`cmd_hold` with no listener running exits 1 with `the listener is not running`:** `test_hold_fails_in_one_line_when_the_listener_is_not_running`, in the new `tests/listener/test_control.py`.
  - **`hold --help` lists the option:** running `python -m autobot.listener.control hold --help` prints `--seconds S`, and a test checks the same.
- The tests also cover the success path and the five-second timeout of `cmd_hold`, the checks on `--seconds`, and the cleanup of the hold files.

**Not confirmed:** the main loop actually dropping frames on the NUC. There is no microphone here, and `run` has no test that drives it.

**Checks**
- `make test`: 766 passed, 14 failed, the same 14 failures as before this step.
  - 13 are tests that need a Unix socket, which the sandbox refuses.
  - The other is `test_the_committed_config_is_a_fresh_install`, which was already failing because the committed config has `theme = "sun"`.
- `make lint`: `ruff check` passes, and every file it could read is formatted. It still exits with an error because the sandbox won't let `ruff format --check` read `.claude/loop.md`.

### 3. The interface's plumbing: argv, the wake colour, the voice seam: not started (out of scope)
### 4. The ticker model: not started (out of scope)
### 5. Eyes and reactions as data: not started (out of scope)
### 6. The meters: not started (out of scope)
### 7. The panels and the experience frame: not started (out of scope)
### 8. The echo: not started (out of scope)
### 9. The Face screen: not started (out of scope)

## Files changed

- `src/autobot/listener/service.py`: adds the hold constants, `Hold`, request parsing and answering, clearing of the hold file at start and stop, the `SIGUSR2` flag, and dropping frames and resetting in the main loop; the docstring gains a paragraph on the hold.
- `src/autobot/listener/control.py`: adds the `hold --seconds S` subcommand (`cmd_hold`, `_hold_seconds`, `HOLD_TIMEOUT_SECONDS`) and its docstring text.
- `tests/listener/test_service.py`: adds tests for `Hold` timing and extension, taking a request, refusing a bad or missing one, and clearing the files.
- `tests/listener/test_control.py` (new): tests for parsing `--seconds`, the help text, and `cmd_hold` when the listener is not running, when it answers, and when it times out.

## For you to do

- Outside the sandbox, run `make test` and `make lint` to confirm the socket tests and the format check pass.
- On the NUC, with `make up` running, run `python -m autobot.listener.control hold --seconds 3`. Then check that `make logs` shows `hold: 3 s` and that a wake spoken during those 3.25 s is not heard.
