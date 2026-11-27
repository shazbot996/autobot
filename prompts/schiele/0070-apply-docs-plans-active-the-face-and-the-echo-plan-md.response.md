---
run: schiele/0070
at: 2026-11-27T05:53:13.239Z
mode: apply
agent: apply
model: claude:claude-opus-5-5
target: docs/plans/active/the-face-and-the-echo-PLAN.md
template: apply
prompt-sha: 642a078d937a
template-sha: bf7cd0cb8062
commit: d9540f8+
attachments: []
read:
  - AGENTS.md
  - docs/plans/active/the-face-and-the-echo-PLAN.md
  - src/autobot/tui/screens/main.py
  - tests/tui/test_tui_app.py
  - src/autobot/tui/echo.py
  - tests/tui/test_tui_echo.py
  - src/autobot/tui/face.py
  - src/autobot/tui/app.py
  - src/autobot/tui/screens/monitor.py
  - src/autobot/tui/ui.py
  - src/autobot/tui/face_ticker.py
  - src/autobot/tui/face_meters.py
  - src/autobot/tui/face_reactions.py
  - tests/tui/tui_fakes.py
  - tests/tui/test_tui_themes.py
  - src/autobot/tui/screens/audio.py
outcome: success
usage:
  input: 2863521
  output: 29689
  peak: 150154
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
    - src/autobot/tui/screens/face.py
    - src/autobot/tui/screens/main.py
    - tests/tui/test_tui_app.py
    - tests/tui/test_tui_face_screen.py
---

## Result

I applied step 9 of 9, the Face screen. Steps 1 to 8 were already in place from earlier runs. The 8 new screen tests pass, the updated menu tests pass, `make typecheck` passes, and `ruff check` and `ruff format --check` pass over `src` and `tests`.

The three `make` checks don't all pass in the sandbox, for the same reasons as in earlier runs:
- **`make test`:** 884 passed and 14 failed, the same 14 as before. Thirteen need a Unix socket, which the sandbox refuses. The fourteenth is `test_the_committed_config_is_a_fresh_install`, which fails because the committed config sets `theme = "sun"`.
- **`make lint`:** ruff stops on `.claude/loop.md`, which the sandbox won't let it read. Run over `src` and `tests` directly, it passes.

## Steps

### 1–8: done in runs schiele/0054 to schiele/0068, not touched here

### 9. The Face screen: done, verified except the on-NUC check
The new `FaceScreen` has the title `Face`:
- **Opening:** on the NUC it opens `[audio] input_device` into the meter's capture and starts rendering the speech line. Off the NUC it calls `require_nuc("the face's microphone and voice")` and leaves the meter and mouth idle.
- **Each frame:** it polls the event stream and feeds the model, which moves the ticker and the eyes and runs the echo.
- **Closing:** it closes the model (stream, playback, recording) and the event stream.
- **Drawing:**
  - The header has `MIC` and a 12-cell meter in the three bands, `FACE` in the middle, and `listener: connected` or `listener: down` at the right.
  - The upper band is `max(6, body // 3)` rows: Summary on the left (3/5 of the width) and the autobot panel with the eyes centred and the mouth meter under them.
  - The Detail panel takes the rest of the body. When it is scrolled back, its edge says so, as on the Live monitor.
  - The ticker is the last row inside the frame, with wake cells in `theme.wake`.
- **Keys:** Up, Down, PgUp, PgDn and End scroll the detail panel, and F1 lists them.
- **Menu:** Face is the tenth entry (key `0`) with the plan's hint. Quit is now the eleventh entry: it has no number and keeps `q` and F10.

**Checked by:** `tests/tui/test_tui_face_screen.py`, 8 tests:
- At 72×22 and at 120×40, opening Face with `0` shows the frame title, `MIC`, the three panels and the prompt `Say "autobot" to wake me.`, with no row wider than the terminal.
- After a fake transcript and wake, the ticker row shows `hey autobot` with `autobot` in the `wake` attribute, and the eye rows change.
- When the stream drops, the ticker reads the listener-down sentence.
- Off the NUC, the message line reads `the face's microphone and voice runs on the NUC only; this host is testbox`, and nothing is opened or rendered.
- Extra: the detail panel scrolls with its keys, and F1 lists them.
- Extra, on the NUC with a fake clock: a wake runs the whole echo on screen, with two `control hold` commands sized to the speech and to 5.0 s. Nothing heard while autobot speaks reaches the ticker, and no file is created.

In `tests/tui/test_tui_app.py` I updated the three main-menu tests for the eleven entries.

I printed the screen at both sizes to check it by eye. That led to one change: the hint is now `Say the wake phrase and autobot echoes your voice back`, because the longer text was cut off at 72 columns.

**A choice the plan didn't settle:** at the minimum size the upper band leaves only four rows inside the autobot panel, which the eyes fill. There the mouth meter is drawn on the panel's bottom edge; with more rows it sits inside, under the eyes.

**Not verified:** the plan's last check, on the NUC with `make up` running, needs the real hardware.

## Files changed

- `src/autobot/tui/screens/face.py` (new): the Face screen.
- `src/autobot/tui/screens/main.py`: the Face entry, tenth on the menu, before Quit.
- `tests/tui/test_tui_face_screen.py` (new): the 8 screen tests.
- `tests/tui/test_tui_app.py`: the menu tests now expect Face at `0` and Quit with no number.

## For you to do

- On the NUC, with `make up` running: run `make deploy`, open **Face** and say "autobot". The five echo steps should run on screen and through the speaker, and the ticker should show nothing while autobot speaks.
- Run `make test` and `make lint` outside the sandbox to confirm that the 13 socket tests pass and that lint is clean.
- The plan's "Changes needed elsewhere" are still to do, since they were not part of step 9: the listener spec, `SCHEMA.md`, `ARCH.md`, `GLOSSARY.md`, the agent-sessions spec, `AGENTS.md`, `README.md` and `CHANGELOG.md`.
