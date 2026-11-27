---
run: schiele/0128
at: 2026-11-27T19:54:46.148Z
mode: direct
agent: direct:general
model: claude:claude-opus-5-5
target: null
template: direct/general
prompt-sha: ec586a927f9c
template-sha: f7093a112dd1
commit: 90b03d1+
attachments: []
read:
  - AGENTS.md
  - src/autobot/agents/question.py
outcome: success
usage:
  input: 5704815
  output: 49364
  peak: 153183
  window: 1000000
cost: 0
harness:
  provider: claude
  kind: sdk
  workspace: self
  access: build
  turns: 59
  sandbox: true
  changed:
    - CHANGELOG.md
    - config/autobot.toml
    - src/autobot/agents/gemini.py
    - src/autobot/agents/hearing.py
    - src/autobot/agents/question.py
    - src/autobot/config.py
    - src/autobot/gcp/checks.py
    - src/autobot/tui/app.py
    - src/autobot/tui/face.py
    - src/autobot/tui/screens/face.py
    - tests/agents/agent_fakes.py
    - tests/agents/test_agent_gemini.py
    - tests/agents/test_agent_hearing.py
    - tests/agents/test_agent_question.py
    - tests/gcp/test_gcp_checks.py
    - tests/tui/test_tui_face_screen.py
---

The question agent now has the flow you described, and the Gemini 404 should be fixed, but that fix isn't proven. I couldn't call Gemini from here, and neither the new flow nor the call has run on the NUC with a real microphone and voice. All of it was tested with fakes.

## Why it failed

**The 404.** autobot asked for `gemini-3.5-flash-lite` in `us-east5`. As far as I know, the newer Gemini models on Vertex AI are only served from the `global` location, not from a region. There is a new setting, `[gcp] gemini_location`, set to `"global"`. `region` is still used for quota checks. If the model still isn't found, the error now names the model, the location and the setting to change. If that happens, pick another model on the Gemini step of the Google Cloud screen.

**The pacing.** The question agent never listened after it started. It only used words that came in the same sentence as "question", so "I have a question" ended at once with "I didn't hear a question". The listener also transcribes in 2-second pieces, so a spoken question would have reached the agent cut up. That's why only "question how big is the sun", said fast, ever worked.

## The new flow

After the wake word and "I have a question":
1. autobot says "Okay, cool, I can look that up for you. Tell me what your question is, and I'll see if I can find an answer for you. Ask me in", then counts "Three. Two. One." one second apart and beeps. The listener is paused for the count and the beep so it doesn't hear autobot.
2. Recording starts when the beep ends. It stops 1.5 s after you stop talking, after 5 s if you say nothing, or after 10 s at most.
3. The recording is transcribed on the NUC with the same faster-whisper model the listener uses, kept in memory only, then dropped. Saying "cancel" as the question sends nothing, and silence gets "I didn't hear a question", spoken.
4. Then the same steps as before: the confirmation if you turned it on, Gemini, and the spoken answer with its detail.

If you say the question in the same breath ("question, how big is the sun"), it is still asked at once. The spec (AG-5) still says the question comes from the listener's transcript, so it should be updated to match.

**What you see on screen:**
- A progress track across the top that brackets the current step: `3 > 2 > 1 > BEEP > ASK > THINK > ANSWER`.
- Big block digits for 3, 2, 1, then GO.
- While recording: a blinking REC light, a bar for the time left, a voice-level meter, and "waiting for you to speak" or "heard you: go on".
- A spinner while it transcribes and while Gemini thinks.

The spoken lines are rendered when the face opens, so the countdown isn't held up waiting for them.

## Checks

- **Lint and type check:** `ruff check`, `ruff format --check` and `make typecheck` pass.
- **`make test`:** 1377 passed and 15 failed, none of them caused by this change:
  - 13 socket tests the sandbox blocks.
  - `test_the_committed_config_is_a_fresh_install` fails because the theme in `config/autobot.toml` is `sun`.
  - `test_the_committed_definitions_seed_what_the_agents_need` fails because the camera agent has `confirm_entry = true`.

  Both values are yours, so I left them.

## For you to try

- On the NUC: wake it, say "I have a question", wait for the beep, and ask. If the countdown feels rushed or recording stops too early, the timings are constants at the top of `src/autobot/agents/question.py`. If it misses quiet speech, lower the -35 dBFS speech threshold there.
- The Rate limits step still checks quotas in `us-east5`. With calls going to `global`, the quota that actually applies may be a different one.

Files changed:
- `src/autobot/agents/question.py`: the invitation, countdown, beep, recording, local transcription and the on-screen view.
- `src/autobot/agents/hearing.py` (new): local faster-whisper transcription of a recording.
- `src/autobot/agents/gemini.py`: asks for the model at `gemini_location`, and a 404 names the setting to change.
- `src/autobot/config.py`: the new `[gcp] gemini_location` key (default `global`) and its validation.
- `config/autobot.toml`: `gemini_location = "global"`, with a comment.
- `src/autobot/gcp/checks.py`: the Gemini step names the location it calls.
- `src/autobot/tui/face.py`: the new `hearing` seam the agents reach the transcriber through.
- `src/autobot/tui/app.py`: the application uses the real local transcriber.
- `src/autobot/tui/screens/face.py`: renders the question agent's spoken lines when the face opens.
- `tests/agents/agent_fakes.py`: a fake transcriber, plus a fake open microphone the test feeds with sound.
- `tests/agents/test_agent_question.py`: tests of the new flow, its failure cases and the beep.
- `tests/agents/test_agent_hearing.py` (new): tests of the audio conversion and the missing-model message.
- `tests/agents/test_agent_gemini.py`: tests of the location setting and the 404 message.
- `tests/gcp/test_gcp_checks.py`, `tests/tui/test_tui_face_screen.py`: updated for the new location text and the extra spoken lines.
- `CHANGELOG.md`: entries for the new flow and the 404 fix.
