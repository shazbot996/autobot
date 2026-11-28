---
type: spec
title: autobot — edge control plane
authority: prescriptive
status: active
describes:
  - src/autobot/listener/**
  - src/autobot/agents/speech.py
  - src/autobot/agents/confirm.py
  - src/autobot/agents/core.py
  - src/autobot/server/client.py
  - src/autobot/tui/router.py
  - src/autobot/**/reflex*
provenance:
  - run: schiele/0149
    at: 2026-11-28T13:41:45.939Z
    mode: create
  - run: schiele/0150
    at: 2026-11-28T13:47:58.817Z
    mode: revise
  - run: schiele/0151
    at: 2026-11-28T13:56:44.860Z
    mode: revise
---

# autobot — edge control plane

The **edge control plane** is the always-on layer on the NUC that keeps listening through every session and gives the owner control that does not depend on any language model. It has five parts:

- **One capture.** A single audio capture process reads the echo-cancelled microphone and sends the same frames to every consumer.
- **The echo canceller.** It takes autobot's own voice out of what the microphone hears.
- **The reflex listener.** Local faster-whisper `tiny.en` runs all the time and hears the reflexes even while autobot speaks (barge-in).
- **The reflexes.** A short, configured list of exact "autobot + phrase" commands, such as cancel, stop talking and check progress. They work with `GAMEON1` off.
- **The session's stream.** After a wake, the session's utterances go to `GAMEON1`'s `/v1/stt`, and the NUC falls back to `tiny.en` when the server is away.

A cancel really stops: the speech, the session and every request still in flight. A reflex can run a side task without interrupting the session. Nothing autobot says can set off a reflex. Rules EC-1 to EC-19 govern this layer. They build on `CN-1`, `CN-2`, WW-4, WW-6, WW-8, WW-11, TS-5, TS-11, FS-4, FS-10, FS-11, FS-12, AG-1, AG-13, AG-14, AG-15, IS-4, IS-5, IS-8, IS-15, IS-16, IS-23 and IS-24. They amend FS-4, FS-11, FS-12, AG-14, WW-8 and IS-24 as "Changes needed elsewhere" lists. The console and moving agents to tools are out of scope.

## Numbered rules, each with its reason

1. **EC-1. The edge control plane runs on the NUC, needs no network, and acts without any language model.**
   - Every rule here keeps working while `GAMEON1` is off, asleep or busy, and while there is no network at all.
   - The only network traffic this layer causes is the session's stream to `/v1/stt` (EC-14) and the closing of requests on a cancel (EC-11).
   - It refuses to run on any machine but the NUC, with the one-line message the other NUC-only paths give (TS-11, WW-9).

   Reason: the owner wants control "outside the language model". A stop that waits on the model or the server is not a stop. IS-4 already says the NUC never depends on the server.

2. **EC-2. One capture process sends the same frames to every consumer.**
   - **The listener is the capture process.** It is the only process that opens the input device.
   - **Its consumers** are:
     - the wake detectors;
     - the reflex listener (EC-5);
     - the session's capture (EC-14);
     - the recent audio of the live monitor (WW-11);
     - the face's microphone meter (FS-4);
     - the echo's recording (FS-11).
   - **Every consumer gets the same frame:** 16 kHz mono int16, `[audio] frame_ms` long.
   - **A slow consumer never delays another one or the capture.** Each consumer has its own queue. One that falls behind drops its oldest frames and counts them.
   - **Other processes never open the microphone.** The interface gets what it needs (levels, transcripts and, when asked, a stretch of audio) through the listener's socket and control commands, in memory and on the NUC only, as the snapshot and hold already work.

   Reason: echo cancellation (EC-3) is worth something only if nothing reads the microphone around it. One open device also stops PipeWire and two readers from fighting over the AIRHUG 21. Today the face opens its own capture for the meter and the echo, and this rule ends that.

3. **EC-3. Echo cancellation: the speaker's output is the reference, and every consumer reads the cancelled stream, never the raw microphone.**
   - **The canceller** is PipeWire's echo-cancel module on the NUC, with the WebRTC backend.
   - **What it provides** is an echo-cancelled source and a sink. Everything autobot plays goes to that sink, so the reference is exactly what the speaker plays. That includes the listener's acknowledgement, the face's speech, confirmations, side-task answers and the echo's playback.
   - **The configuration.** `[audio] input_device` and `output_device` name those two nodes.
   - **No consumer reads the raw source.** The one exception is the measurement in EC-4, which is not a consumer.
   - **It stays local.** The canceller runs on the NUC and sends nothing anywhere (`CN-1`, `CN-2`).
   - **The hardware stays.** The NUC keeps the AIRHUG 21 microphone and its current speaker. No new hardware is bought; the canceller is made to work with what is there.

   Reason: sound comes from at least two processes (the listener's acknowledgement and the interface's speech). A canceller inside the sound server sees all of it as one reference, and one inside a single process could not. PipeWire is already the NUC's audio path ([audio] in `config/autobot.toml`). WebRTC's canceller copes with talk in both directions at once and with clock drift better than speexdsp. Drift matters here because the microphone is USB and the speaker is the onboard HDA output, so they run on different clocks. A microphone with hardware echo cancellation is the alternative, and the owner has chosen not to buy one and to try to make the present hardware work.

4. **EC-4. `make echo-check` measures the canceller on the NUC.**
   - **NUC only.** The target refuses on any other machine, as `make audio-check` does. It appears in the bare `make` summary.
   - **The playback test.** It plays a committed fixture in autobot's piper voice, `en_US-joe-medium`. The fixture contains the wake phrase and every reflex phrase. While it plays, the target records the raw source and the cancelled source at the same time and reports:
     - how many decibels the cancelled recording is below the raw one;
     - whether `tiny.en` hears the wake phrase or any reflex in the cancelled recording.
   - **The double-talk test.** When asked, the target also has the owner say a reflex over the playback and reports whether it was recognised, and how fast (EC-16).
   - **Pass conditions.** It passes when all three hold:
     - the playback is at least 20 dB lower in the cancelled recording;
     - ten playbacks cause no wake and no reflex;
     - the owner's reflex is recognised over the playback in at least nine of ten tries.
   - **Nothing is kept.** Both recordings stay in memory and are dropped when the check ends. The owner's voice from the double-talk test is never written (TS-5).
   - **Unit tests.** The measures are computed by code that does no audio. It is tested on synthetic arrays with no microphone or speaker.

   Reason: the owner asked how to test the canceller. The failure that matters is autobot hearing itself, so the check plays exactly the words that would cause it. `AGENTS.md` asks for hardware checks as `make` targets that an agent can run.

5. **EC-5. The reflex listener is always on.**
   - **What it hears.** It transcribes every stretch of the cancelled stream with the NUC's local faster-whisper `tiny.en`, whatever the wake phrase's kind. It runs while the listener runs, including while autobot speaks (EC-13).
   - **One transcription per stretch.** Under `text_match` the detector's own transcription of a stretch is the reflex listener's as well, so a stretch is not transcribed twice. On the session's fallback (EC-14), the same transcription also serves as the turn.
   - **It stays on the NUC.** It never uses the server and never sends audio or a transcript off the NUC.

   Reason: the owner wants a reflex layer that "keeps listening during every session" and works with `GAMEON1` off. `tiny.en` is already on the NUC and already proven by `text_match`. Under `openwakeword` it adds one small transcription per stretch, which is the price of control that does not depend on the server.

6. **EC-6. Reflexes are a `[[reflexes]]` table of `config/autobot.toml`.** Each entry has four fields:

   | Field | Meaning |
   | --- | --- |
   | `phrase` | The words said after the wake phrase. |
   | `action` | The name of a built-in action on the NUC (EC-8). |
   | `confirm` | Whether the reflex asks before it acts (EC-9). |
   | `confirm_question` | The question it asks. |

   - The action's code is autobot's own. The entry is data, and a new action is new code (AG-1).
   - The loader refuses a phrase that equals a `[[routes]]` command, appears twice, or is empty.

   Reason: the owner asked for "`[[reflexes]]` or similar", and a table of its own matches `[[routes]]` and `[[agents]]`. `CONVENTIONS.md` keeps configuration in that one file. Without the refusals, "autobot weather" could be both a reflex and a command.

7. **EC-7. A reflex matches only on the whole utterance, and an utterance that matches is that reflex and nothing else.**
   - **The match.** A stretch's transcript is a reflex when, normalised as the routing rules are, it is exactly the wake phrase followed by the reflex's phrase, with nothing before or after it. The wake phrase is matched by WW-6's rule with the spaces removed, so "auto bot" counts. "Hey autobot cancel" and "tell autobot to cancel it" are not reflexes.
   - **Checked first.** Reflexes are checked before the global action keywords (AG-14) and before the routing cascade (IS-17).
   - **What a reflex utterance never becomes:**
     - a command;
     - a confirmation's answer;
     - a turn of the core agent;
     - a request to the server.
   - **The listener announces it.** It publishes a `reflex` event on `run/listener.sock` with the stretch's identifier and the action, and the face acts on it.
   - **Outside a session.** A wake on the reflex's own stretch has already been acknowledged by then. The reflex ends the session that wake began, before any command is routed.

   Reason: exact matching on the whole utterance is what keeps conversation from setting off a reflex. Checking reflexes first is what makes them reliable when a route or a model would read the same words differently.

8. **EC-8. The first reflexes.** All five are the owner's:

   | Phrase | Action | Kind | Effect |
   | --- | --- | --- | --- |
   | `cancel` | `cancel` | control | Ends the session and everything in flight (EC-10). |
   | `check progress` | `progress` | side task | Says briefly what the session is doing (EC-12). |
   | `stop talking` | `quiet` | control | Stops autobot's speech at once and drops what was queued. The session goes on listening. |
   | `repeat that` | `repeat` | side task | Says the last spoken summary again. |
   | `server status` | `server` | side task | Says the inference server's state as the watcher holds it (IS-23). |

   - A control reflex acts on the session. A side task only reads it (EC-12).
   - `yes` and `no` are not reflexes. They answer a confirmation through the `[words]` yes and no words (EC-9).

   Reason: the owner named cancel and check progress, asked for suggestions, and accepted `stop talking`, `repeat that` and `server status` as a good starting point. `stop talking` is barge-in without losing the session. `repeat that` and `server status` are local reads that the owner would otherwise ask a model or a screen for. A yes or a no is the answer to a question and not a command of its own, so it has no reflex.

9. **EC-9. Every reflex asks for a confirmation by default, and all confirmations share one process and one timing.**
   - **The convention.** A reflex with `confirm = true` asks its `confirm_question` as AG-13's confirmation does: spoken in the local voice, shown in the summary, and answered by the `[words]` yes and no words.
   - **How the answer is heard.** It is taken from the reflex listener's local transcripts, so it works with `GAMEON1` off.
   - **One process, one timing.** Every reflex confirmation runs through the same code and reads its timing from the same settings in `config/autobot.toml`, not from the reflex's own entry. The timing is how long the question waits for an answer and how long listening stays open after the question has been spoken. Ten seconds (FS-15) is the starting window. A change to the settings changes every confirmation at once.
   - **Effect on the session.** While the question waits, autobot's speech stops after the current sentence. A yes carries out the action. A no, or the time running out, leaves the session where it was, and the speech goes on.
   - **Defaults.** Every reflex ships with `confirm = true`.

   Reason: the owner wants every reflex to confirm by default and the confirmation shared among them, so that the timing of each request and response window can be shaped later in one place. The window has to be quick, but not so quick that it stops listening before the owner has answered and must be tried again, and not so loose that a stray word is taken for an answer. AG-13 is the existing convention, and reusing it keeps one shape for asking.

10. **EC-10. Cancel really aborts.** When the `cancel` reflex is recognised, and confirmed where it asks (EC-9), autobot does all of this within the budget of EC-16:
    - **Speech stops.** The clip playing is cut, the queued sentences are dropped, and renders not yet played are discarded.
    - **The session ends.** It returns to the root, from whatever level it stood at.
    - **Server requests are closed.** Every request of the session still in flight to `/v1/stt`, `/v1/route` or `/v1/chat` has its connection closed (EC-11).
    - **A Gemini request is abandoned.** Its answer is discarded, never spoken and never retried. A request already sent may still be billed.
    - **Nothing joins the conversation.** No part of an abandoned answer is added to it.
    - **The owner sees it.** "Cancelled." is shown in the summary and not spoken, and the eyes play *done*.

    This reflex differs from the spoken keyword `cancel`, which keeps AG-14's meaning: said without the wake phrase, it still goes back one level. Commands an agent started detached, such as the camera agent's, are not stopped (AG-6).

    Reason: the owner wants cancel to "really abort": stop TTS, end the session and cancel anything in flight. Today an answer that is cancelled keeps arriving on its worker thread, and the server keeps generating it. Speaking nothing after a cancel is what the owner asked for: quiet.

11. **EC-11. Closing the connection is the cancel, and the server has no cancel endpoint.**
    - **The NUC's side.** The NUC cancels a request by closing its connection.
    - **A streamed chat.** The server treats a closed connection as cancellation. It stops reading the model's tokens and closes its own request to the runtime, which stops the generation. Its job slot (IS-24) is free within 0.5 s of the close.
    - **Route and transcription.** These do not stream, so the server finishes the call it has begun, within that endpoint's timeout (IS-4), and throws away the answer.

    Reason: IS-5 makes the server stateless. A cancel endpoint would need request identifiers, and so state on the server, while HTTP already carries the signal. Freeing the slot is what matters, because IS-24 gives a busy job one waiting request and then answers `503`. If the slot stayed busy, the owner's next request after a cancel would fall back for no reason.

12. **EC-12. A side task runs during a session without interrupting it.**
    - **What it leaves alone.** A side-task reflex runs its built-in action on the NUC, off the frame. It reads the session's state and changes none of it: not the stage, the conversation, the requests in flight or the agent's experience.
    - **The answer.** It is one short line in the local voice, shown in the summary panel. It is spoken at once if autobot is silent, or in the next gap between two of autobot's sentences.
    - **Timers.** The session's listening time is extended by the length of that line.
    - **Not a turn.** The line is never added to the conversation, and is never a turn of the core agent.
    - **Local only.** A side task calls neither the server nor the cloud.
    - **What `check progress` says:** which agent is running and at what stage (thinking, confirming, tools, handoff, listening), how long it has been there, and what it is waiting on (the home model, Gemini, a tool). With no session running it says "Nothing is running."

    Reason: the owner wants a reflex to run "a preconfigured NUC action during a session without interrupting it", with check progress reading the session state and answering briefly.

13. **EC-13. Barge-in: while autobot speaks, the session's speech-to-text is paused and the reflex listener stays on.**
    - **What counts as speaking.** Any of autobot's playback: the acknowledgement, an agent's speech, a confirmation's question, a side task's answer, the echo's playback.
    - **What stays paused** during that playback, and for `ACK_TAIL_SECONDS` after it:
      - no frame is sent to `/v1/stt`;
      - no frame is transcribed as a turn;
      - no wake is published (FS-12).
    - **What stays on.** The reflex listener keeps hearing the cancelled stream, so a reflex is recognised over autobot's speech.
    - **The spoken keyword.** `cancel` said without the wake phrase is still heard only between utterances (AG-14).

    Reason: the owner wants the GPU speech-to-text paused while autobot speaks, and the reflex listener left on for barge-in. Pausing the stream keeps any echo the canceller leaves from becoming a turn, from being sent across the LAN, and from using the GPU.

14. **EC-14. After a wake, the session's utterances go to `/v1/stt`, with `tiny.en` as the fallback.**
    - **When a stretch is sent.** During a session, a stretch is sent only if three things hold:
      - it is not a reflex;
      - no wake fired in it;
      - `[server] stt_enabled` is `true` and the watcher says the `stt` job is ready (IS-23).
    - **How it is sent.** It goes at the end of the stretch as one IS-8 request, with IS-16's hints. It is not streamed.
    - **Otherwise it stays local.** In every other case, and when the server fails or runs out of time (IS-4), the stretch is the NUC's own `tiny.en` transcript.
    - **The waking stretch is never sent.** The stretch in which the wake fired, including "autobot, echo that" said in one breath, is transcribed on the NUC and never sent (`CN-1` item 2).
    - **Every wake kind.** This holds for every kind of wake phrase (WW-4), so commands keep being heard once a trained `openwakeword` model is installed.
    - **Labelled.** Every transcript says where it came from (IS-15).
    - **Ending.** When the session ends, nothing more is sent.

    Reason: the owner wants the session to stream to `GAMEON1` after the wake and fall back to `tiny.en` when the server is away. IS-8 makes the unit of transfer one whole utterance, cut by the VAD, so the stream is the fan-out of frames on the NUC, not a streaming protocol. A stretch that held the wake phrase is the case `CONSTRAINTS.md` leaves open. Keeping it on the NUC is the reading under which `CN-1` item 2 can be checked.

15. **EC-15. Spoken text is scrubbed of reflex phrases, so autobot never sets itself off.**
    - **The scrub.** Before any text is rendered to speech, wherever the wake phrase (by WW-6's rule) is directly followed by a reflex phrase, the wake phrase is dropped from the spoken text. "Say autobot cancel to stop" is spoken as "say cancel to stop".
    - **When it runs.** It runs on the whole text before the text is cut into sentences, so a pair split across two sentences is still caught.
    - **What is not changed.** The text shown on screen is unchanged.
    - **Where it applies.** It is applied in one place on the speech path, so it covers every agent, confirmation question, side-task answer, the introduce script and the listener's acknowledgement.
    - **Recorded audio.** The echo's playback of the owner's own recording cannot be scrubbed. There the canceller (EC-3) is the only defence, and a reflex it lets through at worst ends a five-second echo.

    Reason: the owner asked for exactly this. Reflexes match only whole utterances (EC-7), so the one way autobot can set one off is to say the wake phrase and a reflex phrase together, and the scrub makes that impossible for anything synthesised.

16. **EC-16. The latency budget of a reflex.** Times are measured from the last voiced frame of the reflex utterance, on the NUC, with `GAMEON1` off. When the reflex asks first (EC-9), the budgets of the action's own rows run from the last voiced frame of the answer:

    | What | Budget |
    | --- | --- |
    | The reflex is recognised and its event published | within 0.9 s |
    | `cancel` or `stop talking`: autobot is silent | within 1.0 s |
    | `cancel`: the session has ended and every request's connection is closed | within 1.0 s |
    | A confirmation's yes or no word is recognised and the confirmation is answered | within 1.0 s |
    | A side task's answer starts speaking, when autobot was silent | within 1.5 s |
    | The server's job slot is free after a closed chat stream (EC-11) | within 0.5 s of the close |

    The face shows the measured time of each reflex in the detail panel. A reflex over its budget is one detail line, not an error. Reason: IS-4 gives a keyword-routed local answer 1.0 s, and a stop must be no slower than an answer. The `text_match` gate's 0.48 s hangover plus a `tiny.en` pass over a short clip on the NUC fits within 0.9 s.

17. **EC-17. How the layer falls back.**
    - **The server is away, or `stt_enabled` is off.** The session runs on `tiny.en` (EC-14, IS-4). Reflexes, cancel and side tasks are unaffected. A request in flight when the server goes away falls back as IS-4 says.
    - **The echo-cancelled source is missing.** The listener does not fall back to the raw microphone. It exits with status 1 and a one-line reason naming the missing node, so systemd restarts it once PipeWire provides the node. The main screen shows *listener down* with that reason.
    - **The reflex listener falls behind.** It drops its oldest frames (EC-2) and never delays the capture. The count of dropped frames is in the listener's log.

    Reason: the owner asked for the fallback to be stated, and chose that the listener stays down when the canceller's node is missing. The stated rule is that no consumer reads the raw microphone, and a listener that is visibly down keeps that rule, where one that quietly runs without cancellation and hears itself would not.

18. **EC-18. Tests open no microphone, speaker or network.**
    - The fan-out, the reflex match, the scrub, cancel, side tasks, barge-in, the shared confirmation timing and the session's choice between server and local are tested with fakes for the capture source, the transcriber, the server client and the clock.
    - A test of cancel checks that the fake connection was closed.
    - The real canceller and the real latency are measured by `make echo-check` on the NUC (EC-4), not by `make test`.

    Reason: `AGENTS.md` keeps the tests free of hardware and network, and puts hardware checks in `make` targets.

19. **EC-19. Everything the control plane does is shown in the game interface.**
    - **The reflexes are on the map.** The face's detail panel, the game screen, shows the configured reflexes: each phrase, its action and whether it asks first.
    - **A reflex heard is a game event.** When a reflex is recognised, the panel shows which one, its measured time (EC-16) and, if it asks, the open confirmation and its remaining window (EC-9).
    - **No hidden behaviour.** A behaviour of this layer that the owner could observe, such as barge-in, a cancel or a side task's answer, has a visible effect on that screen.

    Reason: the owner wants every behaviour designed here to be reflected on the screen as a game option, and the interface is the application (ADR 0006).

## Out of scope

- The console.
- Moving agents to tools.

## Settled without asking

- **Derived.** The prefix `EC-` is free. The prefixes in use are `CN`, `WW`, `TS`, `RR`, `AS`, `FS`, `AG`, `GC` and `IS`, and `docs/remediations/` is empty.
- **Derived (IS-8).** The "stream to `/v1/stt`" is the fan-out of frames on the NUC, sent as one request per whole utterance. A streaming transcription would need IS-8 to be amended.
- **Derived (`CN-1` item 2).** A stretch in which the wake fired is never sent to the server (EC-14). This settles `CONSTRAINTS.md`'s open question as that document recommends.
- **Derived (IS-5, IS-24).** The server gets no cancel endpoint. A closed connection is the cancel, and the slot must be freed (EC-11).
- **Derived (WW-4).** The session's capture works for every wake kind. This answers the open question in `ARCH.md` on where post-wake capture happens.
- **Derived (AG-13).** A reflex's confirmation uses `confirm` and `confirm_question` with the shared `[words]` lists, as an agent's does.
- **Derived (`[server] stt_enabled`).** Server speech-to-text stays off until the owner sets the flag. `CN-1` now allows it.
- **Default.** The table is `[[reflexes]]`, beside `[[routes]]` and `[[agents]]`. For now its entries are edited in the file, and the game interface shows them (EC-19) without editing them.
- **Default.** The canceller is PipeWire's echo-cancel module with the WebRTC backend, because playback comes from several processes and PipeWire is already in the path.
- **Default.** The check is `make echo-check`, with the thresholds of EC-4: 20 dB lower, no reflexes caused by the playback in ten plays, and nine of ten recognised over it.
- **Default.** Matching is strict: no "hey" or "yo" before the wake phrase. This keeps false triggers rare, and the owner can add a phrase.
- **Owner's decision.** The first reflexes are `cancel`, `check progress`, `stop talking`, `repeat that` and `server status`. `yes` and `no` are not reflexes. The owner has no further reflexes to add yet.
- **Owner's decision.** Every reflex defaults to `confirm = true`, and all confirmations share one process and one timing setting (EC-9).
- **Owner's decision.** The NUC keeps its present microphone and speaker (EC-3), and the listener stays down when the echo-cancelled source is missing (EC-17).
- **Default.** A cancel is shown and not spoken, and the spoken keyword `cancel` keeps AG-14's one level back.
- **Default.** A reflex said outside a session ends the session its own wake opened.
- **Default.** The latency figures of EC-16 are taken from IS-4's 1.0 s for a local answer.
- **Not cited.** ADR 0007 is not in `docs/decisions/`. An earlier run drafted it under "Changes needed elsewhere" but did not write it, so this spec does not cite it.

## Changes needed elsewhere

- **docs/context/GLOSSARY.md.** Add:
  - **reflex:** an exact "wake phrase + phrase" command from `[[reflexes]]`, matched on the whole utterance by the local reflex listener, which works with `GAMEON1` off.
  - **control plane:** the always-on layer on the NUC (capture, echo canceller, reflex listener, reflexes and session controls) that acts without a language model. Also called the edge control plane.
  - **barge-in:** a reflex heard and acted on while autobot is speaking.

  The STT entry ("always local"), the faster-whisper entry and the inference server entry ("IS-1 to IS-14") also need updating for `CN-1` as amended.
- **docs/context/ARCH.md.** Add the edge control plane as a part. Record the listener as the one capture process, with the consumers of EC-2 and the echo canceller in PipeWire. Add the `reflex` event to the socket seam. "Control plane" now names this layer, so the rules layer stops being called the control plane. Close the open question on capture after the wake (EC-14).
- **docs/context/SCHEMA.md.** Add the `[[reflexes]]` table (`phrase`, `action`, `confirm`, `confirm_question`) and the `reflex` event on `run/listener.sock` (stretch identifier, action, time). Add the shared confirmation timing settings (EC-9). Record that `[audio] input_device` and `output_device` name the echo-cancel nodes.
- **docs/context/INFRASTRUCTURE.md.** Record the PipeWire echo-cancel configuration on the NUC, with its source and sink node names, and that the AIRHUG 21 stays.
- **docs/specs/active/autobot-gpu-inference-server-SPEC.md.** Add that a closed connection cancels and frees the job slot within 0.5 s (EC-11), as an amendment to IS-24. Drop IS-8's "takes effect once" condition, which now holds. IS-15 gains: a stretch in which a wake fired is never sent.
- **docs/specs/active/autobot-the-face-SPEC.md.** FS-4: the meter reads the listener's levels, not its own capture. FS-11: the echo records from the listener's capture. FS-12: the hold no longer silences the reflex listener (EC-13). Add that the detail panel shows the reflexes and the reflexes heard, with the open confirmation and its window (EC-19).
- **docs/specs/active/autobot-agents-and-the-agent-tree-SPEC.md.** AG-13: a reflex's confirmation uses the same process and the same timing settings (EC-9). AG-14: the reflex `cancel` ends the session, while the spoken keyword still goes back one level. AG-12: post-wake utterances may go to `GAMEON1`.
- **docs/specs/active/wake-word-listener-SPEC.md.** WW-4: capture after the wake for every kind (EC-14). WW-8: the reflex listener still hears during the acknowledgement. Close its open question on the end of the speech after a wake.
- **docs/context/CONSTRAINTS.md.** Its open question on a wake phrase and a command in one utterance is answered by EC-14: that utterance stays on the NUC.
- **AGENTS.md.** Add `make echo-check` to the targets, as NUC-only beside `make audio-check`. Mention the reflexes and `[[reflexes]]`.
- **docs/decisions/.** ADR 0007, "Home models beside Gemini", is still to be written. Record the canceller choice (PipeWire WebRTC rather than speexdsp or a hardware echo-cancelling microphone) as a new ADR.

## Open Questions

- **Cancel and stop talking behind a confirmation.** Every reflex asks first by default (EC-9), which includes `cancel` and `stop talking`, whose point is to be fast. Should those two ship with `confirm = false`, or ask like the rest? The owner said all reflexes default to asking; whether these two are an exception was not said. Recommended: keep them asking only if the shared window proves quick enough, and otherwise set those two to `confirm = false`, because a stop that asks first is slow.
- **The confirmation timing.** What should the starting values of the shared window be (how long the question waits, how long listening stays open after it is spoken), beyond the ten seconds of FS-15? The owner said the timing will be tuned later, and wants it quick but not so short that the owner has to try again. Recommended: start at ten seconds and tune from use.
