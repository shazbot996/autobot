---
run: schiele/0180
at: 2026-11-28T15:47:32.034Z
mode: apply
agent: apply
model: claude:claude-opus-5-5
target: docs/plans/active/edge-control-plane-PLAN.md
template: apply
prompt-sha: 91d3ce91edad
template-sha: bf7cd0cb8062
commit: d70cca3+
attachments: []
read:
  - AGENTS.md
  - docs/plans/active/edge-control-plane-PLAN.md
  - src/autobot/listener/service.py
  - src/autobot/listener/events.py
  - src/autobot/listener/__init__.py
  - src/autobot/listener/audio.py
  - src/autobot/tui/state.py
  - config/autobot.toml
  - tests/listener/test_service.py
  - tests/listener/test_events.py
  - tests/listener/test_fanout.py
outcome: success
usage:
  input: 2521363
  output: 30936
  peak: 105178
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
    - src/autobot/listener/__init__.py
    - src/autobot/listener/events.py
    - src/autobot/listener/fanout.py
    - src/autobot/listener/service.py
    - tests/listener/test_fanout.py
    - tests/listener/test_service.py
---

## Result

I applied step 4 only, as scoped: the listener now opens the microphone once and gives each frame to every consumer through its own queue, and it publishes a new `level` event. The 13 new tests pass, and the type check passes. I couldn't confirm the rewritten main loop by running it, because it needs the microphone and the model files.

One thing to know first: the plan's frontmatter marks step 3 as completed by run schiele/0178, but that run stopped without changing anything (see that earlier report). Step 4 doesn't depend on step 3, so I went ahead, but the echo-cancel check (step 3) still isn't built and the frontmatter is wrong about it.

## Steps

### 1. to 3.: not part of this run
Steps 1 and 2 were done in earlier runs (schiele/0174, schiele/0176). Step 3 stopped in schiele/0178 and is still not built.

### 4. One capture, fanned out: done, verified (except the main loop)
- **New `listener/fanout.py`:**
  - Each consumer gets a fixed-length queue. When it is full, the oldest frame is dropped and counted against that consumer, and the capture never waits.
  - The fan-out reads the microphone on a thread of its own and does nothing else. It also reports how long it has been since the last frame, which the listener uses to notice a lost device.
  - A small timer decides when the dropped-frame counts are due in the log, and a level publisher turns the level queue into events.
- **The `level` event:** `{"event": "level", "dbfs", "peak_dbfs", "at"}`.
  - `dbfs` is the frame's peak; `peak_dbfs` is a held peak that decays by a tenth per frame, the same way the Audio screen's meter does it.
  - Silence is `null`, because JSON has no way to write minus infinity.
  - It is never logged, and it publishes at most one line per frame. Frames taken together give one line for the latest.
- **`service.run`:**
  - The wake pipeline, `RecentAudio`'s rolling buffer and the level publisher each have their own queue, holding 20 s like the microphone's queue, all drained on the main loop.
  - The no-audio timeout now uses the fan-out's last-frame time.
  - The pause after the acknowledgement and the hold now empty the wake and buffer queues instead of the microphone's.
  - At stop and every 10 minutes, the log gets one line per consumer, e.g. "fan-out: wake dropped 0 frames".
- **What behaves as before:** a hold still drops what the pipeline and the rolling buffer would have heard. The `level` event keeps publishing during a hold. Keeping the buffer fed during a hold belongs to step 6.
- **Checks:**
  - `pytest tests/listener/test_fanout.py`: 13 passed. One test registers a consumer that is never drained: the others get all 20 frames, and its dropped count rises 0, 0, 0, 0, 1 … 16. Another shows one `level` line per frame.
  - `make typecheck`: passed.
  - `ruff check`: passed. `ruff format --check` exits 2 only because the sandbox won't let it read `.claude/loop.md`; every other file is formatted.
  - `make test`: 15 failed, 1867 passed. These are the same 15 failures as after step 1: tests that need Unix sockets, which the sandbox refuses, plus `test_the_committed_config_is_a_fresh_install` and `test_the_committed_definitions_seed_what_the_agents_need`, which were already failing.
- **Not verified:** `run()` itself, because it opens the microphone and needs the model files.

### 5. to 15.: not started
Outside the scope of this run.

## Files changed

- `src/autobot/listener/fanout.py` (new): the fan-out, the per-consumer queues, the dropped-frame report and the level publisher.
- `src/autobot/listener/events.py`: the `level` event and its JSON line, plus the docstring describing it.
- `src/autobot/listener/service.py`: the main loop now runs on the fan-out. `RecentAudio.hear` feeds the rolling buffer, so `observe` no longer does. Also added `queue_frames`, the dropped-frame log lines, and docstring updates.
- `src/autobot/listener/__init__.py`: lists `fanout.py` and the new event in the module overview.
- `tests/listener/test_fanout.py` (new): the 13 tests above.
- `tests/listener/test_service.py`: not named in step 4. Its test helper `drive` now also calls `recent.hear(frame)`, because the rolling buffer no longer fills inside `observe`. This is the smallest change that keeps the existing buffer tests valid.

## For you to do

- Run `make test` outside the sandbox to confirm the socket tests still pass.
- On the NUC, run `make up` and then `make logs`. Check that the listener says "listening", that `level` lines appear on `run/listener.sock`, and that `make down` logs one "fan-out: … dropped N frames" line per consumer.
- Fix the plan's frontmatter, which marks step 3 completed, and revise step 3 as the earlier report proposed.
- When the docs are next updated, SCHEMA.md needs the `level` event; the plan's "Changes needed elsewhere" already lists it.
