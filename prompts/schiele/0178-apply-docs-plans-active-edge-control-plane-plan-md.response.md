---
run: schiele/0178
at: 2026-11-28T15:39:11.309Z
mode: apply
agent: apply
model: claude:claude-opus-5-5
target: docs/plans/active/edge-control-plane-PLAN.md
template: apply
prompt-sha: 3becb8884ca0
template-sha: bf7cd0cb8062
commit: 81ebfb6+
attachments: []
read:
  - AGENTS.md
  - docs/plans/active/edge-control-plane-PLAN.md
  - src/autobot/listener/audio.py
  - src/autobot/listener/service.py
  - src/autobot/tui/state.py
  - config/autobot.toml
  - tests/listener/test_service.py
  - src/autobot/reflexes.py
  - tests/test_reflexes.py
  - src/autobot/listener/events.py
  - src/autobot/listener/__init__.py
  - src/autobot/tui/pipewire.py
  - docs/specs/active/autobot-edge-control-plane-SPEC.md
outcome: success
usage:
  input: 530249
  output: 6487
  peak: 68239
  window: 1000000
cost: 0
harness:
  provider: claude
  kind: sdk
  workspace: self
  access: build
  turns: 12
  sandbox: true
---

## Result

I made no changes. I stopped at step 3 because doing it as written would break playback across autobot, and the plan doesn't mention that. Steps 1 and 2 were done in earlier runs, and I didn't start steps 4 to 15 (outside this run's scope).

## Steps

### 1. Configuration: `[[reflexes]]` and the shared timings: done in an earlier run (schiele/0174)

### 2. Reflex matching and the scrub, as pure code: done in an earlier run (schiele/0176)

### 3. The echo canceller and the listener's refusal without it: stopped

**What the plan assumes.** Setting `[audio] input_device = "autobot_ec_source"` and `output_device = "autobot_ec_sink"` in `config/autobot.toml`, and changing only `listener/audio.py`, is enough to open streams on the two echo-cancel nodes.

**What the code has.** Both settings are read as names in PortAudio's device list. `audio_check.resolve_device` matches them against that list, and its docstring calls it "the one place a configured device ... is turned into a `sounddevice` index". PortAudio never lists PipeWire nodes by name: it sees them only through the single ALSA device `pipewire`. So once the config names the nodes, every place that resolves these settings fails with "no output device matches 'autobot_ec_sink'" (or the input version). None of these files is in step 3:

- `src/autobot/listener/ack.py`: the listener's acknowledgement.
- `src/autobot/tui/face.py`: `output_device()`, so all the face's speech, confirmations and the echo's playback; `question.py` and `echo.py` call it too.
- `src/autobot/tui/screens/session.py` and `src/autobot/tui/screens/dataset.py`: the studio's recording and playback.
- `src/autobot/audio_check.py`: `make audio-check`.
- `src/autobot/tui/audio.py` and `src/autobot/tui/screens/audio.py`: the Audio screen writes PortAudio device names back into these same two settings, so it would overwrite the node names.

I also couldn't do the rest of step 3 on its own. With the config still on `"pipewire"`, the new `require_nodes` would look for a node called `pipewire` in `wpctl status`, find none, and the listener would refuse to start.

**What I found that would make it work.** This machine's PipeWire ALSA plugin reads a `PIPEWIRE_NODE` environment variable and sets `target.object` from it. Its `pcm.pipewire` definition also takes a `NODE` argument (`/usr/share/alsa/alsa.conf.d/50-pipewire.conf`). The WebRTC canceller is installed (`spa-0.2/aec/libspa-aec-webrtc.so`). So reaching a node is possible, but every place that opens an output or input stream has to do it, not just `Microphone`. `PIPEWIRE_NODE` is a single variable for the whole process, while the listener captures from the source and plays to the sink. It would have to be set right before each stream opens. There's also a smaller point for the drop-in: `wpctl status`, as `parse_status` reads it, shows node descriptions, not node names. The drop-in needs to set `node.description` to the same names, or `require_nodes` has to look elsewhere.

**What I propose.** Revise step 3 so device handling covers both kinds. Either:

- leave `input_device` and `output_device` as PortAudio devices (`"pipewire"`) and add `[audio] input_node` and `output_node` for the echo-cancel nodes, or
- teach `resolve_device` and the stream openers that a name is a PipeWire node, opened through `pipewire` with that node as target.

Either way, step 3 should list `ack.py`, `face.py`, `audio_check.py`, `tui/audio.py` and the Audio, session and dataset screens among its files, and add a "Done when" saying the ack and the face's speech still open after the switch.

### 4. to 15.: not started
Outside the scope of this run.

## For you to do

- Choose one of the two approaches above and revise step 3 of `docs/plans/active/edge-control-plane-PLAN.md`, then run apply for step 3 again.
