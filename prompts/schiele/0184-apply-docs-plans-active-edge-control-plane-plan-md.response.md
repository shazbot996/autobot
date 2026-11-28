---
run: schiele/0184
at: 2026-11-28T16:14:37.418Z
mode: apply
agent: apply
model: claude:claude-opus-5-5
target: docs/plans/active/edge-control-plane-PLAN.md
template: apply
prompt-sha: faa808377488
template-sha: bf7cd0cb8062
commit: a50e99a+
attachments: []
read:
  - AGENTS.md
  - docs/plans/active/edge-control-plane-PLAN.md
  - src/autobot/listener/ack.py
  - src/autobot/listener/service.py
  - tests/listener/test_service.py
  - src/autobot/listener/reflex.py
  - tests/listener/test_reflex.py
  - src/autobot/listener/events.py
outcome: success
usage:
  input: 2801600
  output: 39179
  peak: 125108
  window: 1000000
cost: 0
harness:
  provider: claude
  kind: sdk
  workspace: self
  access: build
  turns: 50
  sandbox: true
  changed:
    - src/autobot/listener/ack.py
    - src/autobot/listener/service.py
    - tests/listener/test_control.py
    - tests/listener/test_service.py
---

I've applied step 6. While autobot is playing sound, wakes are now paused but reflexes are still heard. Type check and lint pass, and the listener tests pass apart from ones the sandbox blocks. I couldn't run the listener's main loop itself, because it needs the microphone and the model files.

## Result

Applied step 6 of 15. Steps 1 to 5 were done in earlier runs. Steps 7 to 15 are not started (outside this run's scope).

## Steps

### 1. to 5.: not part of this run
Step 3 is still not built, although the plan's frontmatter marks it completed (run schiele/0178 stopped without changing anything).

### 6. Barge-in: playback pauses wakes and turns, never the reflexes: done, verified (except the main loop)
- **`ack.py`:** `Acknowledgement.play` now uses `sd.play(..., blocking=False)` and returns at once. It returns the time the clip ends and also keeps it as `ends_at`. A new `wait()` lets `python -m autobot.listener.ack` still play the whole clip before it exits.
- **`service.py`: `Hold` is now `Speaking`.**
  - It closes until 0.25 s (`ACK_TAIL_SECONDS`) after the acknowledgement ends, or after a hold ends. A new closing never shortens it.
  - `closed` says whether it is closed now; `reopened()` says once that it has opened again.
  - The `control hold` command, its request file and its log lines are unchanged.
- **`service.py`: a new `Listening` class** is the main loop's path for each frame.
  - While the gate is open, a frame goes through `step` as before. A wake whose target is `ack` starts the acknowledgement and closes the gate until it ends. If it can't be played, the gate closes for the 0.25 s tail only.
  - While the gate is closed, the frame is still scored for speech and given to the reflex listener, whose transcript and `reflex` lines are published. No detector hears it, so no score, wake or detector transcript goes out. The rolling buffer keeps every frame too.
  - When the gate reopens, the wake pipeline is reset, as before.
  - The old blocking `_acknowledge` and its `time.sleep` are gone. Nothing discards queued frames any more, and the reflex listener is no longer reset at the acknowledgement.
- **Checks:**
  - `pytest tests/listener/`: 223 passed, 12 failed. All 12 failures are the sandbox refusing Unix sockets (`PermissionError`).
  - The new test `test_a_reflex_said_over_the_acknowledgement_is_heard_and_nothing_wakes` meets the "Done when". A fake acknowledgement plays, and "autobot cancel" is said during it. The test sees one `reflex` event with action `cancel`, no `wake` or `score` event, no frame reaching the detector, and the pipeline reset once the gate reopens.
  - Other new tests: a hold mutes wakes but not reflexes; an acknowledgement that can't be played closes the gate for the tail only; and `play` doesn't wait (checked against a fake sound library).
  - `make typecheck`: passed. `ruff check` and `ruff format --check` over `src` and `tests`: passed.
  - `make lint` itself stops because the sandbox can't read the untracked `.claude/loop.md`.
  - `make test`: 16 failed, 1888 passed. 14 failures are the sandbox socket refusals. The other two are outside the listener: a `TuiConfig` theme of `sun` against `light`, and an assertion about `camera`.

**Things to know:**
- **"No turn":** turn events and `/v1/stt` don't exist until step 7. This step closes the gate they will check, so the test can only show that no `wake` or `score` event goes out.
- **Stretch numbers:** when the reflex listener finishes a stretch while the gate is closed, that stretch takes the next number for itself. Otherwise the next stretch after the gate reopens would get the same number. Under `openwakeword`, a reflex spoken across the wake therefore gets its own number, not the wake's.
- **Until step 3 is built (no echo canceller), the reflex listener transcribes autobot's own sound**, both the "Yes?" and the face's speech. Speech that contains the wake phrase plus a reflex phrase could set off a reflex. Step 13's scrub and step 3's canceller are what prevent that.
- **"ack spoken"** is now logged when the clip starts, not when it finishes.

### 7. to 15.: not started
Outside the scope of this run.

## Files changed

- `src/autobot/listener/ack.py`: `play` no longer waits and gives the end time; new `wait()`; the command-line run waits for the clip.
- `src/autobot/listener/service.py`: `Hold` is now `Speaking`; the new `Listening` frame path; `Pipeline.score`; `RecentAudio.skip_identifier`; the main loop uses `Listening`; old `_acknowledge` removed; docstring explains the speaking gate.
- `tests/listener/test_service.py`: hold tests moved to `Speaking`; five new tests for barge-in and the acknowledgement.
- `tests/listener/test_control.py` (not named in step 6): only the `Hold` import and `.active` updated to `Speaking` and `.closed`, the smallest change the rename needed.

## For you to do

- Run `make test` and `make lint` outside the sandbox to confirm the socket tests and the lint run pass. Check whether the `TuiConfig` theme and `camera` failures were already there before this change.
- On the NUC: `make up`, say "autobot", then "autobot, cancel" while "Yes?" plays. Check that a `reflex` line appears on `run/listener.sock` and no second `wake` does.
- Correct the plan's frontmatter, which still marks step 3 completed.
