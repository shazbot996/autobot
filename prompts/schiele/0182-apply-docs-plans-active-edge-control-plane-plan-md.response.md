---
run: schiele/0182
at: 2026-11-28T16:03:35.614Z
mode: apply
agent: apply
model: claude:claude-opus-5-5
target: docs/plans/active/edge-control-plane-PLAN.md
template: apply
prompt-sha: 15650a1d826b
template-sha: bf7cd0cb8062
commit: f30f357+
attachments: []
read:
  - AGENTS.md
  - docs/plans/active/edge-control-plane-PLAN.md
  - src/autobot/listener/events.py
  - src/autobot/listener/service.py
  - tests/listener/test_events.py
  - src/autobot/listener/fanout.py
  - tests/listener/test_fanout.py
  - src/autobot/listener/__init__.py
  - src/autobot/listener/ack.py
  - tests/listener/test_service.py
  - src/autobot/reflexes.py
  - src/autobot/listener/detector.py
  - src/autobot/listener/vad.py
  - docs/specs/active/autobot-edge-control-plane-SPEC.md
  - src/autobot/listener/engines/text_match.py
  - src/autobot/tui/face.py
  - src/autobot/tui/face_ticker.py
outcome: success
usage:
  input: 5419724
  output: 52084
  peak: 163736
  window: 1000000
cost: 0
harness:
  provider: claude
  kind: sdk
  workspace: self
  access: build
  turns: 69
  sandbox: true
  changed:
    - src/autobot/listener/__init__.py
    - src/autobot/listener/events.py
    - src/autobot/listener/reflex.py
    - src/autobot/listener/service.py
    - tests/listener/test_events.py
    - tests/listener/test_reflex.py
---

## Result

I applied step 5 only, as scoped. The listener now has a reflex listener and publishes `reflex` events. All 15 new reflex tests pass, the type check passes, and lint and formatting pass. Steps 6 to 15 were not started.

Two things don't behave the way the plan's text says; both are under step 5 below. I couldn't run the listener's main loop itself, because it needs the microphone and the model files.

## Steps

### 1. to 4.: not part of this run
Steps 1, 2 and 4 were done in earlier runs. Step 3 is still not built: run schiele/0178 stopped without changing anything, even though the plan's frontmatter marks it completed.

### 5. The reflex listener and the `reflex` event: done, verified (except the main loop)
- **New `listener/reflex.py`:** `ReflexListener` cuts stretches with the speech gate's default margins, using the VAD score the wake pipeline already computes for each frame. At the end of each stretch:
  - If a `text_match` detector transcribed the stretch whole, in one go, it reuses that transcript and calls the transcriber no more.
  - Otherwise the stretch goes into its own queue and is transcribed with the shared `tiny.en` on its own thread. The queue holds 20 s of audio. When it is full, the oldest stretches are dropped and their frames counted, and the count appears in the log as "fan-out: reflex dropped N frames".
  - Each stretch's transcript is published as a `transcript` event with kind `reflex`. A match then publishes `{"event": "reflex", "stretch", "phrase", "action", "latency_ms", "at"}`.
  - `latency_ms` runs from when the stretch's last voiced frame was captured to the publish. The capture time is estimated from how many frames are still queued behind the frame.
  - It imports nothing from `autobot.server`, and a test checks that.
- **`events.py`:** the new `ReflexEvent` with its JSON line. Like scores and transcripts, it goes to the socket and never to the log. The docstring now gives the order of the reflex lines.
- **`service.py`:**
  - The pipeline now exposes each frame's VAD score, the transcript of a stretch transcribed whole, and a fresh gate with the default margins.
  - `step` gives each frame to the reflex listener. A reused transcript is published among the frame's transcripts, and whatever the reflex listener has ready is published after the wakes.
  - `run` builds the listener, starts its thread alongside the capture, publishes what it has ready each time round the loop, and resets it wherever the pipeline is reset.
- **Checks:**
  - `pytest tests/listener/test_reflex.py`: 15 passed. They show:
    - a fake transcript "Autobot, stop talking." gives one `reflex` event with action `quiet` and `latency_ms` 360 under a fake clock;
    - under a real `TextMatchDetector` with a fake transcriber, the transcriber is called once for the stretch, and the reflex listener calls it zero times;
    - "hey autobot cancel" and "tell autobot to cancel it" are transcripts only, not reflexes.
  - `make typecheck`: passed. `ruff check` and `ruff format --check` over `src` and `tests`: passed.
  - `make test`: 16 failed, 1883 passed. That is the same 15 failures as before plus my new socket test, which fails the same way: the sandbox refuses Unix sockets (`PermissionError`).

**Where I departed from the plan:**
1. **The reflex listener isn't a fan-out queue of its own.** Step 4 says it gets its own fan-out queue drained on its own thread. That can't be squared with step 5's "driven by the same VAD score" and reusing the `text_match` transcript of the same stretch, because both of those live on the main loop. So the stretches are cut on the main loop, and only the transcription runs on its own thread, with its own bounded queue and dropped count.
2. **Under `text_match`, every stretch's transcript is published twice.** The reused transcript goes out again with kind `reflex`, so that the kind-`reflex` lines cover every stretch, which step 11's confirmations rely on. The face and the ticker already drop a repeated line with the same stretch and text.

**What this does not do yet:**
- **The face still gets no waking words under `openwakeword`.** The plan says this step makes `FaceModel._waking_words` work for every wake kind. It doesn't: under `openwakeword` the reflex listener's transcript arrives after the `wake` event, so the face has nothing to read when it wakes. Fixing that needs face changes, which belong with step 7.
- **Reflexes aren't heard while autobot speaks.** During a hold or the acknowledgement, the reflex listener hears nothing, as the wake pipeline does today. Keeping it fed is step 6.
- **An `openwakeword`-only setup now needs the `tiny.en` model.** If it's missing, the listener exits with status 2.
- **Two transcriptions can run at once.** The shared `tiny.en` can now be called from the reflex thread and the main loop together. I believe faster-whisper handles this by running the calls one after another, but I haven't checked it on the NUC.

### 6. to 15.: not started
Outside the scope of this run.

## Files changed

- `src/autobot/listener/reflex.py` (new): the reflex listener, its queue and its thread.
- `src/autobot/listener/events.py`: the `reflex` event and its JSON line, the `reflex` transcript kind, and the order of events in the docstring.
- `src/autobot/listener/service.py`: the pipeline's VAD score, whole-stretch transcript and `default_gate`; the reflex listener in `step` and `run`; the reflex line in the dropped-frame report; docstring.
- `src/autobot/listener/__init__.py`: not named in step 5; lists `reflex.py` and the new event in the module overview.
- `tests/listener/test_reflex.py` (new): the 15 tests above.
- `tests/listener/test_events.py`: the reflex event's JSON line, and that it reaches the socket but not the log.

## For you to do

- Run `make test` outside the sandbox to confirm the socket tests pass, including the new `test_a_reflex_event_goes_to_the_socket_and_not_to_the_log`.
- On the NUC, run `make up`, say "autobot, stop talking", and check that a `reflex` line with a `latency_ms` appears on `run/listener.sock`. Then run `make down` and check that the log has a "fan-out: reflex dropped N frames" line.
- Correct the plan's frontmatter, which still marks step 3 completed.
- When the docs are next updated, SCHEMA.md needs the `reflex` event and the `reflex` transcript kind (the plan already lists this), and step 4's wording about the reflex listener's queue should match what was built.
