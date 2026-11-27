---
run: schiele/0064
at: 2026-11-27T05:38:56.506Z
mode: apply
agent: apply
model: claude:claude-opus-5-5
target: docs/plans/active/the-face-and-the-echo-PLAN.md
template: apply
prompt-sha: 22980b0a6e59
template-sha: bf7cd0cb8062
commit: ccb45ff+
attachments: []
read:
  - AGENTS.md
  - docs/plans/active/the-face-and-the-echo-PLAN.md
  - src/autobot/tui/face_reactions.py
  - tests/tui/test_tui_face_reactions.py
  - src/autobot/tui/audio.py
  - src/autobot/tui/screens/audio.py
  - tests/tui/tui_fakes.py
  - src/autobot/tui/face_ticker.py
  - tests/tui/test_tui_face_meters.py
outcome: success
usage:
  input: 1139895
  output: 13569
  peak: 80592
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
    - src/autobot/tui/face_meters.py
    - tests/tui/test_tui_face_meters.py
---

## Result

I applied step 6 of 9. Steps 1 to 5 were already in place from earlier runs, and steps 7 to 9 were outside this run's scope. All 13 new tests pass, and so do `make typecheck`, `ruff check` and `ruff format --check` over `src` and `tests`.

`make test` and `make lint` still exit with errors, but none of the failures comes from this step:
- **`make test`:** 845 passed and 14 failed, the same 14 as before. Thirteen need a Unix socket, which the sandbox refuses. The fourteenth is `test_the_committed_config_is_a_fresh_install`, which fails because the committed config sets `theme = "sun"`.
- **`make lint`:** its only finding is in `.claude/loop.md`, an untracked file this run did not touch.

## Steps

### 1. The listener publishes transcripts: done in run schiele/0054, not touched here
### 2. The listener can be told to hold its ears: done in run schiele/0056, not touched here
### 3. The interface's plumbing: argv, the wake colour, the voice seam: done in run schiele/0058, not touched here
### 4. The ticker model: done in run schiele/0060, not touched here
### 5. Eyes and reactions as data: done in run schiele/0062, not touched here

### 6. The meters: done, verified
`src/autobot/tui/face_meters.py` is new and does no drawing. It uses the Audio screen's scale and three bands (`meter_cells`, `meter_zones`).

- **`MicMeter(capture)`:** wraps the `Capture` and the input stream that feeds it.
  - `open(audio, device, frame_samples)` finds the device and opens it into the capture. If the device can't be found or won't open, the meter goes to `failed` and keeps the one-line error.
  - `close()` stops any recording, closes the stream and resets the level.
  - The four states and their header text:
    - `live`: the peak in dBFS. `bar(width)` gives the filled cells, the hold marker and the band edges.
    - `silent`: `NO SIGNAL`, based on `Capture.delivering_silence`. `warning` gives `SILENCE_WARNING`.
    - `off`: `OFF`, when no stream is open.
    - `failed`: the device error.
- **`mouth_level(clip, elapsed)`:** the RMS of the 80 ms block of the clip being played at `elapsed`. It is 0 before the start and from the end on.
- **`Mouth(clock)`:** `play(clip)` follows a clip from now, `stop()` flattens it, and `level()` and `bar(width)` read it by the clock. A clip that has ended is let go, so the mouth doesn't keep the recording after its playback.

**Checked by:** `tests/tui/test_tui_face_meters.py` (new), 13 tests, all passing. They cover the "Done when":
- The committed fixture `hey_autobot.wav` plays through `Mouth` with a fake clock. Every reading during the clip is above `METER_FLOOR_DB` and the bar is never empty. After the end the level is 0, and it is also 0 before anything plays.
- A `Capture` fed 24 blocks of zeros (1.92 s) is still `live`. One more block, making 2.0 s, puts the meter in `silent` with the warning. A signal brings it back to `live`.

The other tests cover opening the right device, the hold marker, both failure paths, closing, and following a second clip.

**One choice the plan didn't settle:** before it is opened and after it is closed, the meter shows `off`, the same as off the NUC. The screen's message line (`require_nuc`, step 9) is what tells the two apart.

### 7. The panels and the experience frame: not started (out of scope)
### 8. The echo: not started (out of scope)
### 9. The Face screen: not started (out of scope)

## Files changed

- `src/autobot/tui/face_meters.py` (new): `MicMeter` and its four states, `Bar` and `bar`, `mouth_level` and `Mouth`.
- `tests/tui/test_tui_face_meters.py` (new): 13 tests of the microphone meter on the fake audio backend, and of the mouth on a committed fixture with a fake clock.

## For you to do

- Run `make test` outside the sandbox to confirm that the 13 socket tests pass.
- Decide whether `.claude/loop.md` should be excluded from `make lint`. It is the target's only finding.
