---
parent: docs/specs/active/autobot-edge-control-plane-SPEC.md
type: plan
title: Edge control plane
authority: prescriptive
status: active
describes:
  - config/autobot.toml
  - src/autobot/config.py
  - src/autobot/reflexes.py
  - src/autobot/echo_check.py
  - src/autobot/listener/__init__.py
  - src/autobot/listener/audio.py
  - src/autobot/listener/echo_cancel.py
  - src/autobot/listener/fanout.py
  - src/autobot/listener/reflex.py
  - src/autobot/listener/session.py
  - src/autobot/listener/ack.py
  - src/autobot/listener/events.py
  - src/autobot/listener/service.py
  - src/autobot/listener/control.py
  - src/autobot/server/client.py
  - src/autobot/agents/speech.py
  - src/autobot/agents/confirm.py
  - src/autobot/agents/core.py
  - src/autobot/agents/local_model.py
  - src/autobot/tui/router.py
  - src/autobot/tui/face.py
  - src/autobot/tui/face_meters.py
  - src/autobot/tui/echo.py
  - src/autobot/tui/runner.py
  - src/autobot/tui/state.py
  - src/autobot/tui/reflex_actions.py
  - src/autobot/tui/screens/face.py
  - src/autobot/tui/screens/reflexes.py
  - src/autobot/tui/screens/main.py
  - server/src/autobot_server/http.py
  - deploy/pipewire/autobot-echo-cancel.conf
  - Makefile
  - tests/fixtures/audio/synthesise.py
  - tests/fixtures/audio/echo-check.wav
  - tests/test_config_reflexes.py
  - tests/test_reflexes.py
  - tests/test_echo_check.py
  - tests/listener/test_fanout.py
  - tests/listener/test_reflex.py
  - tests/listener/test_session.py
  - tests/listener/test_service.py
  - tests/listener/test_events.py
  - tests/listener/test_control.py
  - tests/server/test_client.py
  - tests/agents/test_agent_core.py
  - tests/agents/test_agent_confirm.py
  - tests/tui/test_tui_reflex_actions.py
  - tests/tui/test_tui_face.py
  - tests/tui/test_tui_echo.py
  - tests/tui/test_tui_face_meters.py
  - tests/tui/test_tui_face_screen.py
  - server/tests/test_http.py
provenance:
  - run: schiele/0169
    at: 2026-11-28T15:08:08.343Z
    mode: create
  - run: schiele/0170
    at: 2026-11-28T15:13:22.574Z
    mode: revise
---

# Edge Control Plane — Implementation Plan

Implements the autobot — edge control plane spec (`docs/specs/active/autobot-edge-control-plane-SPEC.md`).

Nothing in the spec is built yet. No `reflex*` file exists. The listener opens the microphone, and the face opens it a second time for its meter and the echo. While anything plays, the listener drops every frame (`Hold`, and the blocking `_acknowledge`). Post-wake commands come only from `text_match` transcripts (`Router.start` refuses other kinds with `NO_WORDS`). `/v1/stt` is used only by `ServerHearing`, for an agent's own recording, and `[server] stt_enabled` defaults to `false`. When `Core.cancel` runs, the worker thread is dropped but its request is never closed. `LISTEN_SECONDS = 10.0` is a constant in `tui/router.py`, and it is also the confirmation window. On the server side, `http.py` already closes a stream's body when a write fails, and `EventStream.close` gives the job slot back. That close is noticed only at the next write.

## 0. The delta

| Spec Rule | Current Implementation | Planned Change |
|---|---|---|
| EC-1 NUC-only, no network, no model | The listener refuses off the NUC (`require_nuc`) and makes no network calls; no reflex layer exists | Reflex path is network-free and model-free; the only new traffic is `/v1/stt` (step 7) and closes (step 8); `make echo-check` refuses off the NUC (step 15) |
| EC-2 One capture, fan-out | Listener reads `Microphone`; the face opens its own `Capture` (`MicMeter`, echo) | `FanOut` with one bounded drop-oldest queue per consumer (step 4); face meter and echo read from the listener (step 9) |
| EC-3 Echo cancellation | `input_device = output_device = "pipewire"`; no canceller | PipeWire echo-cancel config in `deploy/`, its nodes named in `[audio]`, checked at start (step 3) |
| EC-4 `make echo-check` | Not built | `autobot.echo_check`, a fixture, the Make target, and tested pure measures (step 15) |
| EC-5 Reflex listener always on | Not built | `ReflexListener` consumer; reuses a `text_match` transcript of the same stretch (step 5) |
| EC-6 `[[reflexes]]` table | Not built | Loader with the three refusals (step 1) |
| EC-7 Whole-utterance match, checked first, `reflex` event | Not built | `match_reflex` (step 2), `reflex` event (step 5), face checks reflexes before keywords and the cascade (step 11) |
| EC-8 The five reflexes | Not built | Defaults in config (step 1); actions in `tui/reflex_actions.py` (steps 11, 12) |
| EC-9 Shared confirmation, timing settings, defaults | One `Confirmation` class, but the window is `router.LISTEN_SECONDS` (10 s); no settings | `[session] confirm_seconds`, `idle_seconds` (5 s); reflex confirmations go through `Confirmation`; new settings screen (steps 1, 10) |
| EC-10 Cancel really aborts | The `cancel` keyword drops the experience; worker requests run on; renders not discarded | Reflex `cancel`: stop playback, clear the queue, discard renders, end the session, close every request, discard Gemini, "Cancelled." shown only (step 11) |
| EC-11 Closing is the cancel; slot free in 0.5 s | Server closes the body on a failed write; the NUC has no way to close a request in flight | NUC `InFlight` registry closes sockets (step 8); server watches for a peer close between tokens (step 8) |
| EC-12 Side tasks | Not built | `progress`, `repeat`, `server` actions; asides spoken in gaps; session clock extended (step 12) |
| EC-13 Barge-in | Hold and ack drop all frames | A non-blocking ack and a `Speaking` gate pause wakes, turns and `/v1/stt`; the reflex listener stays fed (step 6) |
| EC-14 Session stream to `/v1/stt`, `tiny.en` fallback | Not built; only `text_match` gives words | `listener/session.py` sends whole non-reflex, non-waking stretches; labelled `turn` events; every wake kind (step 7) |
| EC-15 Scrub | Not built | `scrub` in `reflexes.py`, applied at render in `Voice` and in the ack; sentence cut holds back a trailing wake phrase (step 13) |
| EC-16 Latency budget | Not built | Listener stamps `latency_ms`; face measures action rows and shows over-budget lines (steps 5, 11, 14) |
| EC-17 Fallbacks | Audio faults exit 1; no node check | Missing echo-cancel node: exit 1 with the node named; *listener down* shows the reason; dropped-frame counts logged (steps 3, 4) |
| EC-18 Tests without hardware | Fakes exist for the transcriber, server transport and clock | New tests per step on fakes; a cancel test asserts a fake connection closed |
| EC-19 Shown in the game interface | Game screen shows tree and running agent | Reflex list, heard reflex, its time and open confirmation window on the detail panel (step 14) |

## 1. Configuration: `[[reflexes]]` and the shared timings

**Files:** modified `src/autobot/config.py`, `config/autobot.toml`; new `tests/test_config_reflexes.py`.

- Add `ReflexConfig(phrase, action, confirm=True, confirm_question)`. Add `REFLEX_ACTIONS = ("cancel", "progress", "quiet", "repeat", "server")` and `Config.reflexes`, defaulting to the five reflexes of EC-8, all with `confirm = true`. Each default question is plain, for example "Cancel everything?".
- The loader refuses: an empty phrase, a phrase given twice (normalised as the routing rules normalise), a phrase equal to a `[[routes]]` command, an unknown `action`, and an unknown key. Each refusal is one line.
- Add `SessionConfig(confirm_seconds=5.0, idle_seconds=5.0)` as a `[session]` table. Both must be more than 0 and at most 60.
- Add `[audio] raw_input_device`, which only `make echo-check` reads (step 15).
- Write the five `[[reflexes]]` entries and `[session]`, each with comments, into `config/autobot.toml`.

**Done when:** `load_config()` on the committed file yields five reflexes, all with `confirm=True`, and `confirm_seconds == idle_seconds == 5.0`. A file whose reflex phrase is `weather` fails with one line naming the route.

## 2. Reflex matching and the scrub, as pure code

**Files:** new `src/autobot/reflexes.py`, `tests/test_reflexes.py`.

- `match_reflex(transcript, wake_phrases, reflexes) -> ReflexConfig | None` matches only when the normalised transcript is exactly a wake phrase (WW-6's rule, spaces removed, so "auto bot" counts) followed by the reflex phrase, with nothing before or after.
- `scrub(text, wake_phrases, reflexes) -> str` drops a wake phrase that is directly followed by a reflex phrase. It works across punctuation and sentence ends, and the rest of the text is left as it was.
- `ends_with_wake(sentence, wake_phrases) -> bool` is for the sentence cut in step 13.

**Done when:** the tests show that "Autobot, cancel." and "auto bot cancel" match. "hey autobot cancel" and "tell autobot to cancel it" do not. `scrub("Say autobot cancel to stop")` is "Say cancel to stop", and `scrub("Say autobot. Cancel now.")` drops the wake phrase.

## 3. The echo canceller and the listener's refusal without it

**Files:** new `deploy/pipewire/autobot-echo-cancel.conf`, `src/autobot/listener/echo_cancel.py`; modified `src/autobot/listener/audio.py`, `src/autobot/listener/service.py`, `src/autobot/tui/state.py`, `config/autobot.toml`, `tests/listener/test_service.py`.

- The deploy file is a `libpipewire-module-echo-cancel` drop-in for `~/.config/pipewire/pipewire.conf.d/`. It uses `library.name = aec/libspa-aec-webrtc` and names a source node `autobot_ec_source` and a sink node `autobot_ec_sink`, taking capture from the AIRHUG 21 and sending playback to the onboard HDA output. Set `[audio] input_device = "autobot_ec_source"` and `output_device = "autobot_ec_sink"`.
- `echo_cancel.require_nodes(config, run)` asks `wpctl status` (it reuses `tui/pipewire.parse_status`) whether both configured nodes exist. It raises `EchoCancelMissing(AudioError)` with one line naming the missing node and the deploy file. When both exist, the stream is opened on the node through the PipeWire ALSA device with the node as its target (choice 2).
- `service.main` calls `require_nodes` before it opens the `Microphone`. The listener exits 1 on a missing node and never falls back to the raw source.
- `tui/state.py`: *listener down* carries the last line of `run/listener.log` as its reason.

**Done when:** with a fake `wpctl` that lists no `autobot_ec_source`, `main()` returns 1. The log's last line names `autobot_ec_source`, and the main screen's state reads *listener down* with that line.

## 4. One capture, fanned out

**Files:** new `src/autobot/listener/fanout.py`, `tests/listener/test_fanout.py`; modified `src/autobot/listener/service.py`, `src/autobot/listener/events.py`, `src/autobot/listener/__init__.py`.

- `FanOut` takes each frame from `Microphone` and offers it to every registered consumer's own `deque(maxlen=…)`. When a queue is full, its oldest frame is dropped and counted for that consumer. The capture never waits on a consumer.
- The consumers are the wake pipeline, the reflex listener (step 5), the session capture (step 7), `RecentAudio`, the level publisher and the record request (step 9). The wake pipeline and `RecentAudio` are drained on the main loop. The reflex listener and the session capture each drain on a thread of their own, so a transcription never delays the capture.
- The level publisher publishes a new `level` event on the socket: the dBFS of each frame and its held peak, at most every 80 ms.
- At stop, and every 10 minutes, the log gets one line per consumer with its count of dropped frames.

**Done when:** a test with a fake source and one consumer that never drains shows the other consumers receiving every frame and the slow one's dropped count rising. A `level` line is published for each frame.

## 5. The reflex listener and the `reflex` event

**Files:** new `src/autobot/listener/reflex.py`, `tests/listener/test_reflex.py`; modified `src/autobot/listener/events.py`, `src/autobot/listener/service.py`, `tests/listener/test_events.py`.

- `ReflexListener` gates its queue through the speech gate's default margins, driven by the same VAD score. At each end of a stretch it transcribes the stretch with the shared `tiny.en`, unless a `text_match` detector already transcribed the same stretch, in which case it takes that transcript (EC-5). It runs whatever the wake kind.
- Every transcript it makes is published as a `transcript` event with kind `reflex`, so `FaceModel._waking_words` works for every wake kind.
- On a match it publishes `{"event": "reflex", "stretch", "phrase", "action", "latency_ms", "at"}`. `latency_ms` runs from the stretch's last voiced frame to the publish.
- It imports nothing from `autobot.server` and makes no network call.

**Done when:** with a fake transcriber that returns "autobot stop talking", one `reflex` event with `action: "quiet"` and a `latency_ms` is published. Under a fake `text_match` detector the transcriber is called once for that stretch, not twice.

## 6. Barge-in: playback pauses wakes and turns, never the reflexes

**Files:** modified `src/autobot/listener/ack.py`, `src/autobot/listener/service.py`, `tests/listener/test_service.py`.

- `Acknowledgement.play` becomes non-blocking (`sd.play(..., blocking=False)`), with an `ends_at`.
- `Hold` becomes a `Speaking` gate. The gate is closed while the ack plays, while an outside hold runs (the face's speech, asides, the echo's playback), and for `ACK_TAIL_SECONDS` after either. While it is closed, frames still reach the reflex listener and `RecentAudio`. No wake is published, no turn is transcribed, and nothing goes to `/v1/stt`. When it reopens, the wake pipeline is reset, as today.
- The `control hold` command and its output stay as they are.

**Done when:** a test plays a fake ack and feeds "autobot cancel" frames during it. A `reflex` event is published, and no `wake` or `turn` event is.

## 7. Listener requests and the session's stream

**Files:** new `src/autobot/listener/session.py`, `tests/listener/test_session.py`; modified `src/autobot/listener/service.py`, `src/autobot/listener/control.py`, `src/autobot/listener/events.py`, `src/autobot/server/client.py`, `src/autobot/tui/face.py`, `src/autobot/tui/router.py`, `src/autobot/tui/runner.py`, `tests/listener/test_control.py`.

- Requests other than the snapshot move to a request directory, `run/listener.requests/`. Each request is one file written through a temporary name, and `SIGUSR2` means "a request waits". `hold` becomes one such request, with its command line unchanged. The new requests are `session keep --stt ready|off` and `session close`.
- The listener opens a session on its own wake. It keeps the session open while `session keep` renews a lease of 30 s, and closes it on `session close` or when the lease lapses.
- `SessionCapture` handles each stretch that ends while a session is open. It skips a stretch that matched a reflex, a stretch in which a wake fired, and any stretch while `Speaking` is closed. When the stt is `ready` and `[server] stt_enabled` is true, it sends the stretch as one IS-8 request with IS-16's hints, on a worker thread. Otherwise, or when the server fails, it takes the stretch's local `tiny.en` transcript.
- Each turn is published as `{"event": "turn", "stretch", "text", "source", "at"}`, with `source` `server:<model>` or `local:tiny.en` (IS-15).
- `session close` closes any `/v1/stt` request in flight (step 8).
- Face side: the router sends `session keep` on its first tick and every 10 s, and whenever the watcher's `stt` readiness changes. It sends `session close` when the session ends. `FaceContext.heard` takes `turn` events and no longer takes detector transcripts. The router's `NO_WORDS` refusal is removed (WW-4).

**Done when:** with a fake server client, a stretch after a wake goes to `stt` and a turn with `source: "server:<model>"` is published. The waking stretch never goes there. With `stt` set to `off`, a turn with `source: "local:tiny.en"` is published. After `session close` nothing more is sent. Under a fake `openwakeword` detector, "autobot, echo that" still routes to the echo.

## 8. Closing requests in flight

**Files:** modified `src/autobot/server/client.py`, `src/autobot/agents/local_model.py`, `src/autobot/agents/core.py`, `server/src/autobot_server/http.py`, `tests/server/test_client.py`, `tests/agents/test_agent_core.py`, `server/tests/test_http.py`.

- `ServerClient` takes an `InFlight` registry. Each `stt`, `route` and `stream_chat` call registers its open connection. `InFlight.close_all()` shuts every one down (`socket.shutdown(SHUT_RDWR)`) from any thread, and the worker's call then ends in `ServerUnavailable("cancelled")`, which nothing retries.
- The transport moves from `urllib` to `http.client`, so that the socket can be reached before the answer arrives. There is still no proxy and no retry.
- `FaceContext` owns one `InFlight` and passes it to the seams. `Core.cancel` and every worker check a per-turn generation number, so a token, an answer or a Gemini reply that arrives after a cancel is dropped. It is never spoken, never added to `messages` and never retried.
- Server side: while a streamed answer waits for its next token, `_send` watches the request socket for EOF and closes the body at once when it sees one. The Ollama reply is then closed and the job slot freed.

**Done when:** a client test shows `close_all()` closing the fake connection, and `stream_chat` raising "cancelled". A core test shows a cancel mid-stream adding nothing to `messages` and queuing no speech. A server test shows a client that hangs up before the first token leaving the chat job free to admit a new request within 0.5 s.

## 9. The face reads the listener's capture

**Files:** modified `src/autobot/tui/face_meters.py`, `src/autobot/tui/face.py`, `src/autobot/tui/echo.py`, `src/autobot/listener/control.py`, `src/autobot/listener/service.py`, `src/autobot/tui/runner.py`, `tests/tui/test_tui_face_meters.py`, `tests/tui/test_tui_echo.py`.

- `MicMeter` is fed by `level` events, and reads *off* when the stream is down. `FaceModel.open()` no longer opens an input stream.
- The echo records through a new `control record --seconds S` request. The listener collects S seconds from its fan-out and writes `run/record.wav` (mode 0600, written through a temporary name). The face reads it into memory and removes it in the same tick (choice 4). The listener removes `run/record.*` when it starts and when it stops.
- The echo's playback goes to `output_device` (the echo-cancel sink) under a hold, as today.

**Done when:** a face test with a fake listener feed moves the meter from `level` lines alone, with no `AudioBackend.open_input` call. An echo test records from a fake `record` answer, and the WAV is gone after the tick that read it.

## 10. One confirmation, one timing, and its settings screen

**Files:** modified `src/autobot/agents/confirm.py`, `src/autobot/tui/router.py`, `src/autobot/tui/face.py`, `src/autobot/agents/core.py`, `src/autobot/tui/screens/main.py`; new `src/autobot/tui/screens/reflexes.py`; modified `tests/agents/test_agent_confirm.py`.

- `Confirmation` reads its window from `config.session.confirm_seconds`, counted from when the question has been spoken. Router, agent and reflex confirmations all use it.
- The router's wait for a command uses `config.session.idle_seconds` in place of `LISTEN_SECONDS`.
- `FaceContext.session_clock()` is the clock less the time spent speaking asides (step 12). The router, `Confirmation` and `Core` measure their windows with it.
- New settings screen **Reflexes and timing**, on the settings menu:
  - It edits `confirm_seconds` and `idle_seconds` through `config_edit`, keeping the file's comments.
  - It lists each reflex's phrase, action and `confirm` read-only.

**Done when:** a confirmation test with a fake clock times out at 5.0 s after the question and not before. Changing `confirm_seconds` to 3 in a test config changes the router's, an agent's and a reflex's windows together. The screen writes `[session] confirm_seconds = 3.0` to a scratch config.

## 11. Reflexes on the face: the control reflexes and cancel

**Files:** new `src/autobot/tui/reflex_actions.py`, `tests/tui/test_tui_reflex_actions.py`; modified `src/autobot/tui/face.py`, `src/autobot/tui/router.py`, `src/autobot/agents/speech.py`, `tests/tui/test_tui_face.py`.

- `FaceModel.feed` handles a `reflex` event before anything else, and drops the turn of the same stretch. With `confirm` on, the reflex opens a `Confirmation` through the shared code. While that confirmation is open, the current `Speaker` finishes its sentence and the next ones wait. A yes acts. A no, or a timeout, resumes the session where it was.
- The confirmation's answer comes from the `transcript` events of kind `reflex` (step 5). It works with `GAMEON1` off.
- `cancel` does the following, in this order:
  1. `face.stop()`.
  2. Clear `face.speech`.
  3. `Voice.discard_pending()`.
  4. `InFlight.close_all()`.
  5. Call the experience's `cancel()`.
  6. `session close`.
  7. Show "Cancelled." in the summary without speaking it, and play the eyes' *done*.
  8. Set the router's position to the root.

  The spoken keyword `cancel` keeps AG-14's one level back.
- `quiet` stops playback, clears the queued speech and discards pending renders. The session goes on listening.
- A reflex while no experience runs ends the session its own wake opened, before any command is routed.
- The face measures each action row of EC-16 from the event's stretch end. It does so by adding the event's `latency_ms` to its own clock.

**Done when:** a face test with fakes feeds a `reflex` `cancel` and then a yes word during a core turn. Playback stops, the fake connection is closed, the router is at the root, the summary's last line is "Cancelled.", and no speech is queued. A `quiet` test leaves the core agent listening.

## 12. Side tasks

**Files:** modified `src/autobot/tui/reflex_actions.py`, `src/autobot/tui/face.py`, `src/autobot/agents/speech.py`, `src/autobot/tui/router.py`, `src/autobot/agents/core.py`, `tests/tui/test_tui_reflex_actions.py`.

- `progress` reads a `Progress` snapshot from the router: the agent, its stage (from `Core.stage` or the router's phase), how long it has been there, and what it waits on (the home model, Gemini or a tool). With no session it says "Nothing is running."
- `repeat` says the last spoken summary again.
- `server` says the watcher's state (IS-23).
- Each answer is one line in the summary panel and is queued as an aside. A `Speaker` that is about to play waits while an aside is pending, so the aside plays in the next gap, or at once when the face is silent.
- The aside's length is added to the session clock. It is never appended to the conversation, and it makes no server or cloud call.

**Done when:** a test asks `check progress` while a fake core agent streams. The answer is spoken between two sentences, `Core.messages` is unchanged, and the core agent's listen deadline moves by the aside's length.

## 13. The scrub on the speech path

**Files:** modified `src/autobot/tui/face.py` (`Voice`), `src/autobot/agents/core.py` (`SummaryStream`), `src/autobot/listener/ack.py`, `tests/agents/test_agent_core.py`, `tests/tui/test_tui_face.py`.

- `Voice.prepare` renders `scrub(text)` and keys the render by the shown text. The shown text is unchanged. Every `Speaker`, confirmation, aside, the introduce script and the echo's line pass through it.
- `ack.render` scrubs `[ack].text`.
- `SummaryStream._cut` does not give out a sentence that `ends_with_wake` until the next sentence is cut. It then gives the two out as one, so the scrub sees the pair whole.

**Done when:** a test streams "Say autobot." and then "Cancel to stop." The render is asked for "Say. Cancel to stop.", with the wake phrase dropped. The summary still shows "Say autobot. Cancel to stop."

## 14. The game screen shows the control plane

**Files:** modified `src/autobot/tui/screens/face.py`, `src/autobot/tui/face.py`, `tests/tui/test_tui_face_screen.py`.

- The detail panel's game screen lists the configured reflexes: phrase, action, and whether it asks first.
- A reflex heard shows which reflex it was, its recognition time, and the open confirmation with its remaining window as a countdown. A row over its EC-16 budget is one detail line, for example "cancel: silent after 1.3 s (budget 1.0 s)".
- Barge-in, a cancel and an aside each leave a visible mark: a marker on the protagonist and the summary line.

**Done when:** a screen test renders the face to a fake canvas and finds the five reflex phrases. After a fed `reflex` event it finds that reflex's line and a countdown, and after a fake over-budget timing it finds the budget line.

## 15. `make echo-check`

**Files:** new `src/autobot/echo_check.py`, `tests/test_echo_check.py`, `tests/fixtures/audio/echo-check.wav`; modified `tests/fixtures/audio/synthesise.py`, `Makefile`.

- The pure measures:
  - `attenuation_db(raw, cancelled)`;
  - `heard(transcript)`, which reports a wake or a reflex through `match_reflex` and `contains_phrase`;
  - `verdict(results)`, which passes on ≥ 20 dB, no wake and no reflex in ten plays, and ≥ 9 of 10 double-talk recognitions.
- The run:
  - It refuses off the NUC with the TS-11 line, and it refuses while the listener runs.
  - It plays the fixture to `output_device` ten times, recording `raw_input_device` and `input_device` at once, in memory only, and runs `tiny.en` on the cancelled recordings.
  - With `TALK=1` it prompts the owner for ten double-talk tries and reports each recognition time.
  - Nothing is written, and both recordings are dropped at exit.
- `synthesise.py` makes the fixture in `en_US-joe-medium`, containing the wake phrase followed by each reflex phrase.
- The Makefile entry is `echo-check: venv-check ## Measure the echo canceller on the NUC: attenuation, self-triggers, double-talk (TALK=1; NUC only)`.

**Done when:** `make` lists `echo-check`. Off the NUC it exits 1 with one line. `tests/test_echo_check.py` passes on synthetic arrays: a 1 kHz tone at −30 dB relative gives 30 dB, and a fake set of 8 of 10 recognitions fails.

## 16. Choices the spec leaves open

1. **Requests to the listener use one directory and `SIGUSR2`.** The snapshot keeps `SIGUSR1`. The event socket stays write-only, as SCHEMA describes it.
2. **The echo-cancel nodes are reached by name, through PipeWire's ALSA device with the node as target.** The listener checks that the nodes exist with `wpctl` before opening them. Whether the target holds on the NUC is confirmed by `make echo-check`.
3. **The listener opens the session on its own wake, and the face keeps it alive with a 30-second lease.** This way the first stretch after the wake is never missed. A face that is gone cannot leave stretches going to the server.
4. **The echo's recording comes back as `run/record.wav`.** It is mode 0600 and removed in the tick that reads it, as the snapshot works, which the spec cites as the precedent.
5. **One transcription per stretch.** The reflex listener's transcript is the waking words, the confirmation's answer and the local fallback turn.
6. **`make echo-check` refuses while the listener runs,** so the listener's reflexes do not act on the check's playback.

## 17. Out of scope

- The console.
- Moving agents to tools.
- Editing `[[reflexes]]` entries from the interface: they are shown, not edited.
- Stopping commands an agent started detached (AG-6).
- Buying audio hardware.
- A cancel endpoint on the server.

## Settled without asking

- **Derived (EC-14, IS-8).** One whole-stretch request per utterance; no streaming protocol.
- **Derived (EC-9, AG-13).** Reflex confirmations reuse `Confirmation` and the `[words]` lists.
- **Derived (EC-17).** A missing echo-cancel node is exit status 1, so systemd restarts the listener, matching `service.py`'s existing transient status.
- **Derived (AGENTS.md).** The new target carries a `## ` comment, so it shows in the bare `make` summary.
- **Default.** The timings live in a new `[session]` table: they belong to neither `[words]` nor `[server]`.
- **Default.** `[server] core_listen_seconds` (IS-19) is left at 10 s. `idle_seconds` replaces the router's FS-15 wait only.
- **Default.** The raw microphone for `make echo-check` is `[audio] raw_input_device`, which no consumer reads.

## Changes needed elsewhere

- **docs/context/SCHEMA.md.** Add the new socket events: `level`, `turn` (with `source`) and `reflex` (with `latency_ms`). Record that `transcript` gains kind `reflex`. Add the `[session]` table, `[audio] raw_input_device`, the `run/listener.requests/` hand-off and `run/record.wav`, beyond what the spec lists.
- **docs/specs/active/autobot-the-face-SPEC.md.** FS-11: the echo's recording passes through `run/record.wav` for one tick (choice 4).
- **AGENTS.md.** List `make echo-check` and the `deploy/pipewire/` drop-in, and say the listener now makes `/v1/stt` calls during a session.
- **docs/context/INFRASTRUCTURE.md.** The node names `autobot_ec_source` and `autobot_ec_sink`, and where the drop-in is installed.

## Open Questions

- **Studio recording and the microphone.** The Recording session and the Audio screen's test clip still open the input device themselves, and EC-2 does not list them as consumers. Should they go through the listener, or keep their own capture? Recommended: they keep their own capture, but open only the echo-cancelled source and only while the listener is down, because recordings longer than any listener buffer stream straight to the dataset (TS-5), and training on cancelled audio matches what the listener hears.
