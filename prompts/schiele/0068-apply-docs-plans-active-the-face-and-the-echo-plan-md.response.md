---
run: schiele/0068
at: 2026-11-27T05:48:57.578Z
mode: apply
agent: apply
model: claude:claude-opus-5-5
target: docs/plans/active/the-face-and-the-echo-PLAN.md
template: apply
prompt-sha: 97eeee4eb98e
template-sha: bf7cd0cb8062
commit: 37aae60+
attachments: []
read:
  - AGENTS.md
  - docs/plans/active/the-face-and-the-echo-PLAN.md
  - src/autobot/tui/face.py
  - tests/tui/test_tui_face.py
  - src/autobot/tui/screens/main.py
  - src/autobot/tui/audio.py
  - src/autobot/tui/face_meters.py
  - tests/tui/tui_fakes.py
  - src/autobot/tui/runner.py
  - tests/tui/test_tui_echo.py
outcome: success
usage:
  input: 1075203
  output: 20649
  peak: 103069
  window: 1000000
cost: 0
harness:
  provider: claude
  kind: sdk
  workspace: self
  access: build
  turns: 21
  sandbox: true
  changed:
    - src/autobot/tui/echo.py
    - tests/tui/test_tui_echo.py
---

## Result

I applied step 8 of 9, the echo. Steps 1 to 7 were already in place from earlier runs, and step 9 was outside this run's scope. The 12 new tests pass, and so do `make typecheck`, `ruff check` and `ruff format --check` over `src` and `tests`.

`make test` still exits with an error: 876 passed and 14 failed. These are the same 14 failures as before this step. Thirteen need a Unix socket, which the sandbox refuses. The fourteenth is `test_the_committed_config_is_a_fresh_install`, which fails because the committed config sets `theme = "sun"`.

## Steps

### 1. The listener publishes transcripts: done in run schiele/0054, not touched here
### 2. The listener can be told to hold its ears: done in run schiele/0056, not touched here
### 3. The interface's plumbing: argv, the wake colour, the voice seam: done in run schiele/0058, not touched here
### 4. The ticker model: done in run schiele/0060, not touched here
### 5. Eyes and reactions as data: done in run schiele/0062, not touched here
### 6. The meters: done in run schiele/0064, not touched here
### 7. The panels and the experience frame: done in run schiele/0066, not touched here

### 8. The echo: done, verified
`src/autobot/tui/echo.py` is new and does no drawing. It holds the `Echo` state machine, which goes woken, speaking, recording, playing back, done. Every step runs in `tick`, which never blocks.

- **Starting up:** `prepare(voice)` starts rendering `Hey, I'm going to record your voice. Go ahead.` in the background. Step 9's screen will call it when the face opens on the NUC.
- **Woken:** at the wake the summary says `I heard you.`. The first frame then checks the NUC, the microphone stream, the output device and the voice, in that order.
- **Speaking:** `face.say` shows the line and queues it. The echo waits for the render, then:
  - takes the line off the speech queue;
  - asks for a hold sized to the clip, which mutes the ticker;
  - plays the line once the hold has answered, with the eyes on *speaking*.
- **Recording:** five seconds through the face's own `Capture`, with the eyes on *listening*. The ticker is unmuted. The summary counts down `Recording: N s left`, and the detail line updates to `recorded 2.4 s of 5.0 s, level … dBFS`.
- **Playing back:** the summary says `Playing it back.`, a second hold is sized to the recording, and the clip is played from memory.
- **Done:** the eyes play *done*, the summary says `Echo finished. Say the wake phrase to go again.`, the ticker is unmuted, the clip is dropped and `done` is set.
- **Failures:**
  - Any failed step ends the echo with its one-line reason as a trouble line in the summary and the *trouble* reaction.
  - A hold that fails with `the listener is not running` is ignored.
  - Any other hold failure becomes a `hold: …` detail line, and the echo goes on.

**Checked by:** `tests/tui/test_tui_echo.py` (new), 12 tests. They drive `FaceModel` with fake events, the fake audio backend fed from `yo_autobot.wav`, the fake voice, the fake runner and a fake clock. They confirm all of the "Done when":
- The states run in order.
- Two `control hold` commands are started, sized to the speech and to 5.0 s.
- `audio.play` gets the spoken clip and then a 5.0 s clip, exactly the microphone audio that arrived while recording.
- The mouth moves during both clips and is flat after.
- No file appears anywhere under the scratch directory.
- With the voice raising the missing-model error, the summary shows that one line under *trouble* and the model is idle again.

They also cover the countdown, waiting for a render that is still running, the off-NUC line, a microphone that never opened, a missing output device, both kinds of hold failure, a microphone that delivers nothing, and closing the face mid-recording.

**Choices the plan didn't settle:**
- **The eyes during playback:** they play *speaking*, because the spec says the playback comes out of autobot's mouth.
- **A microphone that goes quiet:** a recording still running 2 s past its five seconds ends in trouble with `the microphone stopped delivering audio`. Without this the echo could wait forever.
- **The off-NUC check** names `the echo`: `the echo runs on the NUC only; this host is …`.
- **Closing the face:** `echo.py` has no `leave` of its own. When the face closes, step 7's `FaceModel.close` stops playback and recording and drops the experience, which drops its clip with it.

### 9. The Face screen: not started (out of scope)

## Files changed

- `src/autobot/tui/echo.py` (new): `prepare` and the `Echo` state machine, the face's first experience.
- `tests/tui/test_tui_echo.py` (new): 12 tests of the echo on fakes and a fake clock.

## For you to do

- Run `make test` outside the sandbox to confirm that the 13 socket tests pass.
