---
run: schiele/0044
at: 2026-11-27T04:38:02.099Z
mode: direct
agent: direct:ask
model: claude:claude-fable-5-1
target: null
template: direct/ask
prompt-sha: 989ca41a4451
template-sha: 44af0dfef32e
commit: ac0d0b6
attachments: []
read:
  - AGENTS.md
  - docs/context/ARCH.md
  - config/autobot.toml
  - docs/specs/active/rules-first-routing-SPEC.md
  - docs/specs/active/agent-sessions-SPEC.md
  - src/autobot/listener/events.py
  - src/autobot/listener/service.py
  - src/autobot/listener/ack.py
  - src/autobot/config.py
  - docs/specs/active/wake-word-listener-SPEC.md
  - src/autobot/listener/engines/text_match.py
  - src/autobot/listener/engines/sherpa_kws.py
  - src/autobot/tui/menu.py
  - docs/decisions/0006-full-screen-tui-and-training-on-the-owners-voice-ADR.md
  - docs/context/SCHEMA.md
  - Makefile
  - src/autobot/tui/monitor.py
  - docs/context/GLOSSARY.md
  - docs/context/CONVENTIONS.md
  - tests/listener/conftest.py
  - tests/listener/test_openwakeword.py
outcome: success
usage:
  input: 341241
  output: 7744
  peak: 94188
  window: 1000000
cost: 0
harness:
  provider: claude
  kind: sdk
  workspace: self
  access: read
  turns: 25
  sandbox: false
---

Nothing architectural is stopping you. The project already assumes this order of work; it just hasn't written down that the training is being set aside. Here is what is actually in your way, in three groups: what you'd be living with, what has to be built for the echo test, and what the documents need to say so the next build run heads the right way.

## What you're running today, and what it costs you to leave it

`config/autobot.toml` has `kind = "text_match"`. That is what is responding to you: faster-whisper `tiny.en` transcribes every stretch of speech in the room and fires when the transcript contains `autobot` (`src/autobot/listener/engines/text_match.py`). It needs no trained model, and ADR 0005/0006 kept it as "the bootstrap and fallback kind". The documented reasons it was meant to be replaced:

- **CPU, not accuracy.** It is "the expensive kind: every stretch of speech in the room costs a transcription" (two whisper threads, `WHISPER_CPU_THREADS`). On one NUC for one household that is a cost, not a blocker. `sherpa_kws` is the cheaper no-training alternative (one thread, ~2 ms a frame), but on the synthetic held-out voices it heard 87 of 140, so if `text_match` is reliably hearing you, stay on it.
- **Tests.** `tests/listener/conftest.py` (`wakeword_threshold`) fails the `openwakeword` tests if `models/wakeword/autobot.onnx` is missing; this is AGENTS.md's open question. On the NUC the synthetic-only leftover model is at that path (ADR 0006 calls it the "leftover model"), so `make test` passes there. On a fresh checkout it won't, regardless of your decision. Nothing new.
- **The TUI will say *untrained* forever** (`src/autobot/tui/state.py`), because that state is defined as "no `openwakeword` model installed". Cosmetic until the main screen is redesigned, which you want to do anyway.

So: leaving the wake-word layer exactly as built is a legitimate choice. It is a *decision* though, because ADR 0006 says the trained model is "the engine for 24x7 use" and `text_match` is the fallback. Record it as a new ADR 0007 ("the untrained kind is the production kind for now; the studio stays built and is deferred"), not by editing 0005 or 0006.

## What the echo test needs that does not exist yet

Trace your scenario through the code. "Hey autobot" → `WakeEvent{target:"ack"}` → `_acknowledge` in `src/autobot/listener/service.py` plays "Yes?", then `microphone.discard()` and `pipeline.reset()`. **That is the end of the built path.** Everything after it is listed as "not built" in `ARCH.md` ("The full exchange… continues from the wake event, and is not built"). Specifically:

1. **Capture after the wake (WW-4).** The listener drops the audio during the ack and starts a fresh stretch afterwards; nothing records the query. `ARCH.md` leaves open *where* the capture happens: in the listener (which holds the mic and the VAD, and already knows when speech ends) or in whatever reads the socket. This is the first design decision to make. My read: the listener should do it, because it already has the end-of-speech detection and it is the only process that knows when to mute itself.
2. **A target other than `ack`.** `target` is a plain string, `ack` is the only value that does anything (`TARGET_ACK` in `service.py`; `SCHEMA.md` says so). The wake phrase's `target` is meant to select "an agent or rule set" (WW-4, `WakePhraseConfig.target`).
3. **STT as a part of its own.** Whisper exists only inside the `text_match` detector (`WhisperTranscriber`, reusable as is) and in the training. Note `make fetch-models` fetches only `tiny.en`, good enough to spot a word, marginal for transcribing a sentence like "give me an echo test". A `base.en`/`small.en` for the query path is a `fetch_models.py` addition.
4. **TTS for arbitrary text.** `src/autobot/listener/ack.py` renders one fixed string once at startup. Saying "okay, let's do an echo test… three, two, one" is a new, general `speak(text)` on the same piper voice. `ARCH.md` seam 5, "Spoken response", has no spec at all.
5. **Not hearing itself.** The listener mutes only during its own ack. If a separate process speaks the countdown and plays the echo back, the listener will hear it and may wake again (and `text_match` will happily transcribe it). You need either a mute/hold control (a new `control` subcommand, `src/autobot/listener/control.py` already has the SIGUSR1 pattern for `snapshot`) or the listener does the speaking.
6. **The hand-off channel is one-way and carries no audio.** `run/listener.sock` broadcasts JSON lines, no backlog, nothing a reader sends is read (`src/autobot/listener/events.py`). The TUI's `EventStream` in `src/autobot/tui/monitor.py` is a finished, tested client you can reuse for a session process. Getting *audio* out is only the snapshot hand-off (last 5 stretches / 10 s), designed for the live monitor, not for a query path.
7. **The rules layer itself.** `docs/specs/active/rules-first-routing-SPEC.md` has four rules and five open questions (form of a rule, storage, no-match behaviour). The echo test is exactly RR-2, a rule that answers locally with no AI, so it is the right first case; the spec just needs to be written for real rather than left a stub.

The pattern you describe ("okay, give me a minute… I'll check back") is AS-5 in `agent-sessions-SPEC.md`, whose open question "how does the listener know a follow-up utterance belongs to the open session rather than being a new wake?" is the same mute/hold question as item 5, seen from the other side. Design it once.

## How I'd shape it

Your phrase "voice middleware for agent tasks" is the missing part named: a **session runner** process between the listener and the agents. It reads wake events from the socket, owns the turn (speak → capture → transcribe → rule → task → speak → repeat), runs a task as a child it can time out and recover from, and is where the resilience lives. The listener stays the dumb, always-on data plane; the runner is the control plane `ARCH.md` already sketches, and the TUI becomes its screen. That keeps `CN-1`/`CN-2` trivially satisfied (all audio stays in the listener and runner; only text leaves, and for the echo test nothing leaves).

Concretely, the sequence of CR2 work is documents first, then a plan, then code:

1. **ADR 0007**: training deferred, `text_match` is the running kind; the studio is kept, not removed.
2. **Revise `wake-word-listener-SPEC.md`**: settle its three open questions (how the query capture ends, what a non-`ack` target does, whether the ack is still spoken before a capture) and add a hold/mute control.
3. **Fill `rules-first-routing-SPEC.md`** for the first rule type (a phrase match with a local-response outcome) and **write the missing "spoken response" spec**, or fold it into a new **session runner spec** that covers capture → STT → rule → task → TTS, the task protocol (long-lived, reports back, fails safely), and the echo test as its acceptance case.
4. **A plan from those specs** whose first milestone is the echo test end to end, and whose second is a "task" that sleeps and reports back, to exercise the long-lived turn before any Gemini call. The agent host (`agent-sessions-SPEC.md`, ADR 0002) comes after.
5. **The TUI redesign** goes in the same plan or its own: ADR 0006 already says "later jobs of the application appear as further screens of the same interface", and `ARCH.md` lists "where do the rules layer and the agents appear in the TUI?" as open, so the main screen can become the session/agent view with the studio moved under a menu entry.

One thing to keep from the studio even if you never train: the live monitor's marks still turn real use into labelled recordings in `data/voice/`, so the option to train later stays open at no cost.
