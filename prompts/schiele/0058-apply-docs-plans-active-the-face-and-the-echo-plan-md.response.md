---
run: schiele/0058
at: 2026-11-27T05:25:08.390Z
mode: apply
agent: apply
model: claude:claude-opus-5-5
target: docs/plans/active/the-face-and-the-echo-PLAN.md
template: apply
prompt-sha: f8ba1a8310e0
template-sha: bf7cd0cb8062
commit: c92a2ac+
attachments: []
read:
  - AGENTS.md
  - docs/plans/active/the-face-and-the-echo-PLAN.md
  - src/autobot/tui/runner.py
  - src/autobot/tui/ui.py
  - src/autobot/tui/themes.py
  - src/autobot/tui/app.py
  - tests/tui/tui_fakes.py
  - tests/tui/test_tui_themes.py
  - tests/listener/test_control.py
  - src/autobot/tui/audio.py
outcome: success
usage:
  input: 1692046
  output: 13799
  peak: 90819
  window: 1000000
cost: 0
harness:
  provider: claude
  kind: sdk
  workspace: self
  access: build
  turns: 38
  sandbox: true
  changed:
    - src/autobot/tui/app.py
    - src/autobot/tui/face_voice.py
    - src/autobot/tui/runner.py
    - src/autobot/tui/themes.py
    - src/autobot/tui/ui.py
    - tests/tui/test_tui_face_voice.py
    - tests/tui/test_tui_runner.py
    - tests/tui/test_tui_themes.py
    - tests/tui/tui_fakes.py
---

## Result

I applied step 3 of 9. Steps 1 and 2 were already in place from earlier runs, and steps 4 to 9 were outside this run's scope. Every new test passes, and so do `make typecheck`, `ruff check` and the format check over `src` and `tests`. `make test` and `make lint` still exit with errors, but these failures were there before this step and none comes from it.

## Steps

### 1. The listener publishes transcripts: done in run schiele/0054, not touched here

### 2. The listener can be told to hold its ears: done in run schiele/0056, not touched here

### 3. The interface's plumbing: argv, the wake colour, the voice seam: done, verified

- **`runner.py`:** new `listener_hold_argv(seconds)`, which gives `control hold --seconds S` with S to two decimals.
- **Wake colour:** `Theme` has a new `wake` role.
  - In `themes.py` it is `Ink(RED, WHITE, True)` on light, `Ink(RED, BLUE, True)` on dark and `Ink(RED, BLACK, True)` on sun.
  - In mono it is reverse plus bold.
- **`face_voice.py` (new):** `synthesise(text, voice, models_root) -> Clip`.
  - It looks for `models/piper/<voice>.onnx` and its `.onnx.json` under `models_root`.
  - It calls `enforce_offline()` and only then imports `piper`, inside the function.
  - It appends a 0.15 s silent tail.
  - A missing file raises a one-line `VoiceError` naming the file and `make fetch-models`.
  - The only thing it takes from the listener's code is `enforce_offline` (plan choice 7).
- **`Services`:** gains `voice: Callable[[str, str, Path], Clip] = synthesise`.
- **`tui_fakes.py`:** a `FakeVoice` that returns the committed `hey_autobot.wav` and loads no model. It records its calls and can be set to raise. `make_harness` passes it to `Services` and makes it available as `Harness.voice`.

**What confirmed it:**
- **The `wake` colour:** a new theme test checks that every palette draws `wake` bold red on its page with a colour pair it already had, so `PAIRS_NEEDED` stays at 14 (the value I measured before the change). The existing test that every palette colours every role also passes with the new role.
- **The missing-file error:** a new test calls `synthesise` with a scratch `models_root` and gets one line naming `…/models/piper/en_US-joe-medium.onnx` and `make fetch-models`. Further tests cover a missing `.onnx.json`, check that `piper` is not imported when a file is missing, and exercise the fake voice.
- **`make typecheck`:** no issues in 82 source files.
- **Real render:** I ran `synthesise` once against the local `en_US-joe-medium` voice. It rendered the echo line to a 3.82 s int16 clip at 22050 Hz.

**Checks:**
- **`make test`:** 773 passed, 14 failed. These are the same 14 failures as before this step:
  - 13 tests need a Unix socket, which the sandbox refuses.
  - `test_the_committed_config_is_a_fresh_install` fails because the committed config has `theme = "sun"`.
- **`make lint`:** `ruff check` passes and all 103 files it could read are formatted. The target still exits with an error because the sandbox won't let ruff read `.claude/loop.md`.

**Beyond the files the plan named:**
- **`tests/tui/test_tui_face_voice.py` (new):** the plan asks for a `synthesise` test but names no file for it.
- **`tests/tui/test_tui_runner.py`:** one test for `listener_hold_argv`, next to the snapshot argv test.
- **Additions in `face_voice.py`:** `VoiceError` and a `voice_files` helper. The listener's `piper_voice_path` can't be reused because it looks under the repository root, not `models_root`.

The real voice returned a peak of 32767, which is full scale. The listener's `ack.render` produces its clip the same way, so I left it as it is.

### 4. The ticker model: not started (out of scope)
### 5. Eyes and reactions as data: not started (out of scope)
### 6. The meters: not started (out of scope)
### 7. The panels and the experience frame: not started (out of scope)
### 8. The echo: not started (out of scope)
### 9. The Face screen: not started (out of scope)

## Files changed

- `src/autobot/tui/runner.py`: adds `listener_hold_argv(seconds)`.
- `src/autobot/tui/ui.py`: adds the `wake` role to `Theme`, reverse plus bold in mono.
- `src/autobot/tui/themes.py`: colours `wake` bold red on each palette's page.
- `src/autobot/tui/face_voice.py` (new): `synthesise`, `voice_files` and `VoiceError`, for local piper rendering under the offline guard.
- `src/autobot/tui/app.py`: `Services.voice`, which defaults to `synthesise`.
- `tests/tui/tui_fakes.py`: adds `FakeVoice`, `fixture_clip` and `FIXTURES`, and passes the fake voice to `Services` in the harness, available as `Harness.voice`.
- `tests/tui/test_tui_themes.py`: a test of the `wake` colour and the unchanged pair count.
- `tests/tui/test_tui_runner.py`: a test of `listener_hold_argv`.
- `tests/tui/test_tui_face_voice.py` (new): tests of the missing-file error, that `piper` isn't loaded when a file is missing, and the fake voice.

## For you to do

- Outside the sandbox, run `make test` and `make lint` to confirm the socket tests and the format check of `.claude/loop.md` pass.
