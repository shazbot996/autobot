---
run: schiele/0054
at: 2026-11-27T05:18:01.109Z
mode: apply
agent: apply
model: claude:claude-opus-5-5
target: docs/plans/active/the-face-and-the-echo-PLAN.md
template: apply
prompt-sha: 2b1997ad5168
template-sha: bf7cd0cb8062
commit: 1b06bf1+
attachments: []
read:
  - AGENTS.md
  - docs/plans/active/the-face-and-the-echo-PLAN.md
  - src/autobot/listener/events.py
  - src/autobot/listener/service.py
  - tests/listener/test_events.py
  - tests/listener/test_service.py
  - src/autobot/listener/control.py
  - tests/listener/conftest.py
  - src/autobot/listener/detector.py
outcome: success
usage:
  input: 1404928
  output: 15885
  peak: 96167
  window: 1000000
cost: 0
harness:
  provider: claude
  kind: sdk
  workspace: self
  access: build
  turns: 19
  sandbox: true
  changed:
    - src/autobot/listener/events.py
    - src/autobot/listener/service.py
    - tests/listener/test_events.py
    - tests/listener/test_service.py
---

## Result

Applied step 1 of 9. Steps 2 to 9 were not started because the target scope limited this run to step 1. The code for step 1 is in place, but its two socket tests could not run here: the sandbox does not allow Unix sockets, and the existing socket tests fail the same way.

## Steps

### 1. The listener publishes transcripts: done, partly verified

- **`events.py`:** a new `TranscriptEvent` (`phrase`, `kind`, `text`, `stretch`, `at`) and an `Event` type covering all three events.
  - `to_json_line` writes `{"event": "transcript", "phrase", "kind", "text", "stretch", "at"}` in that field order.
  - `publish` sends a transcript to socket readers only and never to the log.
  - The module docstring now describes three events, with the order "transcripts, then scores, then wakes".
- **`Pipeline`:**
  - After every `feed` and `end_of_speech` call, it checks the detector's `last_transcript`.
  - When that object is new since the last check, it records `(phrase, kind, text)` in `pipeline.transcripts`. This list is cleared at the start of each frame and by `reset()`.
  - Transcripts whose text is empty or only whitespace are skipped.
- **`RecentAudio`:** gains `open_identifier()`. It also gains `transcript_events(pipeline)`, which the plan did not name: `step` has no clock of its own, so this method stamps the transcripts with `open_identifier()` and `RecentAudio`'s clock.
- **`step`:** publishes transcripts first, then scores, then wakes.
- **Unchanged:** the debug log, and the log stream, which still holds only wakes.

How it was checked:
- **Order:** I ran `step` with a scripted transcriber returning "hey autobot" over two stretches, publishing through an `EventPublisher` that was never opened. Each stretch gave `transcript` (stretch N), then `score` (stretch N), then `wake`, and the log held only the two `wake` lines.
- **Tests that ran and passed:**
  - the transcript's JSON line and field order;
  - an empty transcript is not published;
  - a transcript made after a stretch was scored early keeps that stretch's identifier.
- **Tests that could not run here:** the two socket tests that check the "Done when" directly, `test_a_transcript_is_published_before_its_stretchs_score_and_wake` and `test_a_transcript_event_goes_to_the_socket_and_not_to_the_log`. They fail with `PermissionError` when creating the socket, as all 10 existing socket tests in `test_events.py` and both socket tests in `test_tui_monitor.py` do.
- **Checks:**
  - `make typecheck` passes.
  - `ruff check` passes and every Python file is formatted. `make lint` still exits with an error only because the sandbox will not let `ruff format --check` read `.claude/loop.md`.
  - `make test`: 749 passed, 14 failed. Thirteen failures are the socket refusal. The other is `tests/test_config.py::test_the_committed_config_is_a_fresh_install`: the committed `config/autobot.toml` has `theme = "sun"` but the test expects `light`. That file has no changes in the working tree, so the failure was there before this run.
- **Readers:** the Live monitor's reader skips event kinds it doesn't know, so the new `transcript` line does not disturb it.

### 2. The listener can be told to hold its ears: not started
### 3. The interface's plumbing: argv, the wake colour, the voice seam: not started
### 4. The ticker model: not started
### 5. Eyes and reactions as data: not started
### 6. The meters: not started
### 7. The panels and the experience frame: not started
### 8. The echo: not started
### 9. The Face screen: not started

## Files changed

- `src/autobot/listener/events.py`: adds `TranscriptEvent` and the `Event` type, its JSON line, and the socket-only rule; the docstring covers three events and the new order.
- `src/autobot/listener/service.py`: adds `Pipeline.transcripts`, `RecentAudio.open_identifier()` and `transcript_events()`; `step` publishes transcripts first; the docstrings mention transcripts.
- `tests/listener/test_events.py`: tests for the transcript's JSON line and for transcripts going to the socket and not the log.
- `tests/listener/test_service.py`: tests for the transcript → score → wake order and the log through `step`, the skipped empty transcript, and the identifier after an early score.

## For you to do

- Outside the sandbox, run `make test` and `make lint` to confirm the socket tests pass, especially `test_a_transcript_is_published_before_its_stretchs_score_and_wake`.
- Decide what to do about `test_the_committed_config_is_a_fresh_install`: either set the committed `theme` back to `light` or update the test. It is not part of this step.
