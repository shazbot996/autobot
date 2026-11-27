---
run: schiele/0066
at: 2026-11-27T05:43:32.987Z
mode: apply
agent: apply
model: claude:claude-opus-5-5
target: docs/plans/active/the-face-and-the-echo-PLAN.md
template: apply
prompt-sha: 09706e5f8d41
template-sha: bf7cd0cb8062
commit: e89f60d+
attachments: []
read:
  - AGENTS.md
  - docs/plans/active/the-face-and-the-echo-PLAN.md
  - src/autobot/tui/face_meters.py
  - tests/tui/test_tui_face_meters.py
outcome: success
usage:
  input: 1591693
  output: 27829
  peak: 107464
  window: 1000000
cost: 0
harness:
  provider: claude
  kind: sdk
  workspace: self
  access: build
  turns: 20
  sandbox: true
  changed:
    - src/autobot/tui/face.py
    - tests/tui/test_tui_face.py
---

## Result

I applied step 7 of 9. Steps 1 to 6 were already in place from earlier runs, and steps 8 and 9 were outside this run's scope. The 19 new tests pass, and so do `make typecheck`, `ruff check` and `ruff format --check` over `src` and `tests`.

`make test` and `make lint` still exit with errors, but none of the failures comes from this step:
- **`make test`:** 864 passed and 14 failed, the same 14 as before. Thirteen need a Unix socket, which the sandbox refuses. The fourteenth is `test_the_committed_config_is_a_fresh_install`, which fails because the committed config sets `theme = "sun"`.
- **`make lint`:** its only finding is that it cannot read `.claude/loop.md`, an untracked file this run did not touch.

## Steps

### 1. The listener publishes transcripts: done in run schiele/0054, not touched here
### 2. The listener can be told to hold its ears: done in run schiele/0056, not touched here
### 3. The interface's plumbing: argv, the wake colour, the voice seam: done in run schiele/0058, not touched here
### 4. The ticker model: done in run schiele/0060, not touched here
### 5. Eyes and reactions as data: done in run schiele/0062, not touched here
### 6. The meters: done in run schiele/0064, not touched here

### 7. The panels and the experience frame: done, verified
`src/autobot/tui/face.py` is new and does no drawing.

- **`SummaryPanel`:** keeps lines with the newest at the bottom. It starts with `Say "autobot" to wake me.`, using the first wake phrase. It has `add`, `trouble` (a line flagged for the warning role), `clear`, and `update`, which replaces the newest line.
- **`DetailPanel`:** has `append`, `update` (replaces the last line) and `clear`. Scrolling works like the Live monitor's: `up`, `down`, `page_up`, `page_down` and `end`, a scroll offset, and a `page` set by `shown(rows)`. A panel scrolled back stays on the lines it shows when new lines arrive.
- **`Experience` protocol:** `reactions`, `done`, `start(face)` and `tick(face)`.
- **`FaceContext`:** what an experience drives:
  - the two panels;
  - `react(name)`, using the experience's own set;
  - `say(text)`: shows the line in the summary, queues the same text in `speech`, and starts rendering it;
  - `capture`, which is the meter's own `Capture`;
  - `voice`, a `Voice` helper that renders each line once through `Services.spawn` and gives back `clip(text)` or `error(text)`;
  - `hold(seconds)`, which starts `listener_hold_argv` through the runner and mutes the ticker, and `release()`, which unmutes it;
  - `play(clip)` on the resolved `[audio] output_device`, with the mouth following, and `stop()`;
  - `on_nuc` and `refusal(what)`;
  - the clock.
- **`FaceModel`:** owns the ticker, the eyes, the meter, the mouth, the panels and the running experience.
  - `feed(events, connected=...)` passes every event to the ticker and plays *wake* on every wake. If nothing is running, it also clears the panels and starts a new experience.
  - `tick()` advances the ticker, runs the experience and moves the eyes on.
  - `open()` opens the input device into the meter.
  - `close()` stops playback and recording, drops the stopped recording and the experience, and unmutes the ticker.
  - `from_services` builds it from the interface's `Services`.

**Checked by:** `tests/tui/test_tui_face.py` (new), 19 tests on fakes, all passing. They cover the "Done when":
- Two wakes 1 s apart start one experience. The second wake doesn't restart it, and both wakes pop `autobot` on the ticker.
- The summary shows the prompt before the first wake, is cleared at that wake, keeps the finished run's lines, and is cleared again at the second session's wake.

The other tests cover the panels' scrolling, say-and-speak being one text, rendering once, the voice's one-line error, the hold's argv and muting, playback and the mouth, an output device that won't resolve, the off-NUC line, and closing.

**Choices the plan didn't settle:**
- **No echo yet.** `echo.py` is step 8, so `FaceModel` takes the experience as a factory argument rather than building an echo itself. Step 9's screen will pass the echo's constructor.
- **`SummaryPanel.update`.** I added it so that step 8's `Recording: N s left` countdown won't need to change `face.py`, which step 8 doesn't list among its files.
- **The off-NUC wording.** `FaceContext.refusal` repeats `App.require_nuc`'s wording, because the model doesn't import `App`.
- **The stopped recording.** When the face closes, it takes and drops the clip that stopping the recording leaves in the capture, so the owner's voice isn't kept in memory.

### 8. The echo: not started (out of scope)
### 9. The Face screen: not started (out of scope)

## Files changed

- `src/autobot/tui/face.py` (new): `SummaryPanel`, `DetailPanel`, `Voice`, the `Experience` protocol, `FaceContext` and `FaceModel`.
- `tests/tui/test_tui_face.py` (new): 19 tests of the panels, the context and the model, using the fake audio backend, runner and voice, a fake clock and a fake experience.

## For you to do

- Run `make test` outside the sandbox to confirm that the 13 socket tests pass.
- Decide whether `.claude/loop.md` should be excluded from `make lint`. It is the target's only finding.
