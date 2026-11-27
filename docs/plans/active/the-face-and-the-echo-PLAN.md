---
parent: docs/specs/active/autobot-the-face-SPEC.md
type: plan
title: The face and the echo
authority: prescriptive
status: active
steps:
  total: 9
  completed: [1, 2, 3, 4, 5, 6, 7, 8, 9]
  current: 10
  status:
    1: { state: "completed", run: "schiele/0054", at: "2026-11-27T05:18:00.129Z" }
    2: { state: "completed", run: "schiele/0056", at: "2026-11-27T05:21:46.211Z" }
    3: { state: "completed", run: "schiele/0058", at: "2026-11-27T05:25:07.556Z" }
    4: { state: "completed", run: "schiele/0060", at: "2026-11-27T05:28:18.721Z" }
    5: { state: "completed", run: "schiele/0062", at: "2026-11-27T05:32:28.271Z" }
    6: { state: "completed", run: "schiele/0064", at: "2026-11-27T05:38:55.698Z" }
    7: { state: "completed", run: "schiele/0066", at: "2026-11-27T05:43:32.195Z" }
    8: { state: "completed", run: "schiele/0068", at: "2026-11-27T05:48:56.791Z" }
    9: { state: "completed", run: "schiele/0070", at: "2026-11-27T05:53:12.423Z" }
describes:
  - src/autobot/listener/events.py
  - src/autobot/listener/service.py
  - src/autobot/listener/control.py
  - src/autobot/tui/runner.py
  - src/autobot/tui/ui.py
  - src/autobot/tui/themes.py
  - src/autobot/tui/app.py
  - src/autobot/tui/face_ticker.py
  - src/autobot/tui/face_reactions.py
  - src/autobot/tui/face_meters.py
  - src/autobot/tui/face_voice.py
  - src/autobot/tui/face.py
  - src/autobot/tui/echo.py
  - src/autobot/tui/screens/face.py
  - src/autobot/tui/screens/main.py
  - tests/listener/test_events.py
  - tests/listener/test_service.py
  - tests/listener/test_control.py
  - tests/tui/tui_fakes.py
  - tests/tui/test_tui_themes.py
  - tests/tui/test_tui_app.py
  - tests/tui/test_tui_face_ticker.py
  - tests/tui/test_tui_face_reactions.py
  - tests/tui/test_tui_face_meters.py
  - tests/tui/test_tui_face.py
  - tests/tui/test_tui_echo.py
  - tests/tui/test_tui_face_screen.py
provenance:
  - run: schiele/0051
    at: 2026-11-27T05:13:34.884Z
    mode: create
  - run: schiele/0052
    at: 2026-11-27T05:17:28.008Z
    mode: revise
---

# The Face and the Echo — Implementation Plan

Implements the autobot — The Face spec (`docs/specs/active/autobot-the-face-SPEC.md`).

This is an initial plan. No face or echo code exists under `src/autobot/tui/` or `tests/tui/`. Two things the face depends on are also missing from the listener. First, the listener publishes no transcripts: `autobot.listener.events` has only `wake` and `score`. Second, it has no way for another process to make it stop listening for a while: `control.py` has `snapshot` but nothing like `hold`, and only `_acknowledge` drops audio. So the work starts with those two listener seams. Then come the models that do no drawing, and the screen comes last.

## 0. The delta

| Spec Rule | Current Implementation | Planned Change |
|---|---|---|
| FS-1 Face is a TUI screen | The main menu has ten entries (`screens/main.py`) and no Face | A new `screens/face.py` view, reached as **Face** from the main menu. It is drawn in the current theme, and `T` works on it. |
| FS-2 Layout | Nothing | The screen draws a header row, then summary and character side by side in the upper band, then the detail panel, then the ticker row. The app's own message line and key bar stay below it. |
| FS-3 Ticker | The socket carries `wake` and `score` only. `EventStream` exists. | The listener publishes a `transcript` event. A new ticker model (`face_ticker.py`) scrolls transcripts, colours the wake phrase in a new `wake` theme role, shows a stretch marker for kinds that don't transcribe, and shows one sentence when the listener is down. |
| FS-4 Microphone meter | The Audio screen has `Capture`, `meter_cells`, `meter_zones` and `SILENCE_WARNING` | A compact meter in the header, fed by a `Capture` on `[audio] input_device`, with the same three bands and the same silence warning |
| FS-5 Eyes | Nothing | Pose and animation model (`face_reactions.py`), with an ASCII drawing and a Unicode drawing |
| FS-6 Mouth | Nothing | `face_meters.mouth_level`: the level of whichever clip is playing, at the point playback has reached |
| FS-7 Reactions as data | Nothing | A built-in set defined as a data mapping, loaded by `reaction_set`. Each experience can supply its own set, with the built-in reaction used for any name it leaves out. |
| FS-8 Summary panel | Nothing | `SummaryPanel` model. `Experience.say` shows a line and speaks the same text. A prompt is shown before the first wake. Messages are cleared at the next wake. |
| FS-9 Detail panel | Nothing | `DetailPanel` model: lines the experience appends or updates, scrolled with the Live monitor's keys |
| FS-10 Experience | Nothing | `face.py`: an `Experience` protocol, one experience running at a time, a wake reaction on every wake, and the echo started on any wake while idle |
| FS-11 Echo | Nothing | `echo.py` state machine: wake, speak, record 5 s, play back, done. The recording is held in memory only. Any failure ends the echo with *trouble* and a one-line reason. |
| FS-12 No self-hearing | `_acknowledge` drops audio only for the listener's own "Yes?" | New `control hold --seconds S`. The listener drops audio and resets its detectors for that long plus `ACK_TAIL_SECONDS`. The face asks for a hold before each thing it plays, and the ticker is muted meanwhile. |
| FS-13 Local, autobot's voice | piper rendering exists only inside the listener (`ack.render`) | `face_voice.synthesise` renders text with local piper in `[ack].voice`, under the offline guard. The face opens no sockets except `run/listener.sock`. |
| FS-14 Front, testable, NUC only | `App.require_nuc`, fake backends and `Services` exist | All face logic sits in modules that do no drawing and are tested with fakes. Off the NUC, the meter, voice and echo refuse with the usual line while the screen still opens. |

## 1. The listener publishes transcripts

**Files:** modified `src/autobot/listener/events.py`, `src/autobot/listener/service.py`, `tests/listener/test_events.py`, `tests/listener/test_service.py`.

- Add a `TranscriptEvent` (`phrase`, `kind`, `text`, `stretch`, `at`) to `events.py`. `to_json_line` gives it as `{"event": "transcript", "phrase": …, "kind": "text_match", "text": …, "stretch": N, "at": …}`. `EventPublisher.publish` sends it to socket readers only and never to the log, the same as `ScoreEvent`. Update the module docstring: add the third event, and change the order rule to "transcripts, then scores, then wakes".
- After every `feed` and `end_of_speech` call, `Pipeline.process` checks each detector's `last_transcript`. If the object is new since the last check, it records `(phrase, kind, text)` in a new `pipeline.transcripts` attribute for that frame. Empty texts are skipped.
- `RecentAudio` gains `open_identifier()`. It returns the identifier the open stretch has, or will be given when it is scored.
- `step` publishes the frame's transcripts first, stamped with `open_identifier()`. The stretch's scores and wakes follow, as before.
- The debug log of transcripts (`AUTOBOT_LISTENER_DEBUG`) is unchanged. Transcripts never reach the log any other way.

**Done when:** a test drives `step` with a fake transcriber that returns "hey autobot", and the published lines are in this order: one `transcript` with the text and the stretch's identifier, then the `score` line with the same identifier, then the `wake`. A second test confirms the log stream holds only the `wake` line.

## 2. The listener can be told to hold its ears

**Files:** modified `src/autobot/listener/service.py`, `src/autobot/listener/control.py`, `tests/listener/test_service.py`, new `tests/listener/test_control.py`.

- Add `python -m autobot.listener.control hold --seconds S`, where S is greater than 0 and at most `MAX_HOLD_SECONDS = 60`. It works the same way as `snapshot`:
  - It writes `run/hold.request` with S, sends the listener `SIGUSR2`, and waits up to five seconds for the listener to remove the request file.
  - On success it prints `listener holding for S s` and exits 0.
  - Otherwise it exits 1 with one line: `the listener is not running`, or `the listener did not take the hold within 5 s`.
- In `service.py`:
  - `SIGUSR2` only sets a flag. Before the next frame, the main loop calls `answer_hold_request`, which reads S, sets a `Hold` deadline of now + S + `ACK_TAIL_SECONDS`, logs `hold: S s`, and removes the request file.
  - While the hold lasts, frames are read and dropped and nothing is scored or published.
  - When the hold ends, it does what `_acknowledge` does: `microphone.discard()`, `pipeline.reset()` and `recent.reset()`.
  - A new request extends the deadline and never shortens it.
  - `run/hold.request` is cleared when the listener starts and when it stops, like the snapshot files.
- The listener still plays its own "Yes?" before the main loop sees the next request. So a hold the face asks for right after a wake is taken only once the acknowledgement has finished, and the face's speech follows it (WW-8).

**Done when:**
- Tests of `Hold` with a fake clock show it active for S + 0.25 s and inactive after.
- A test of `answer_hold_request` shows that a bad request leaves no hold and removes the file.
- A test of `cmd_hold` with no listener running exits 1 with `the listener is not running`.
- `python -m autobot.listener.control hold --help` lists the option.

## 3. The interface's plumbing: argv, the wake colour, the voice seam

**Files:** modified `src/autobot/tui/runner.py`, `src/autobot/tui/ui.py`, `src/autobot/tui/themes.py`, `src/autobot/tui/app.py`, `tests/tui/tui_fakes.py`, `tests/tui/test_tui_themes.py`; new `src/autobot/tui/face_voice.py`.

- Add `listener_hold_argv(seconds)` to `runner.py`, which gives `control hold --seconds S` with S to two decimals.
- Add a `wake` field to `Theme`. Its ink is bold red on each palette's page: `Ink(RED, WHITE, True)` on light, `Ink(RED, BLUE, True)` on dark, `Ink(RED, BLACK, True)` on sun. In mono it is reverse plus bold. These colour pairs already exist in each palette, so `PAIRS_NEEDED` does not change.
- `face_voice.synthesise(text, voice, models_root) -> Clip` renders `text` with piper into memory:
  - The voice file is `models/piper/<voice>.onnx` and its `.onnx.json`, looked for under `models_root`.
  - `enforce_offline()` is called before `piper` is imported, and `piper` is imported inside the function.
  - A short silent tail is appended, as `ack.render` does.
  - A missing file raises a one-line error that names the file and `make fetch-models`.
- `Services` gains `voice: Callable[[str, str, Path], Clip] = synthesise`. Tests supply a fake that returns a tone from the committed fixtures and loads no model.

**Done when:**
- The theme test confirms every palette colours `wake` and that `PAIRS_NEEDED` is unchanged.
- A test calls `synthesise` with a scratch `models_root` and gets the one-line missing-file error naming `en_US-joe-medium.onnx`.
- `make typecheck` passes with the new `Services` field.

## 4. The ticker model

**Files:** new `src/autobot/tui/face_ticker.py`, `tests/tui/test_tui_face_ticker.py`.

- `Ticker.feed(events)` takes what `EventSource.poll` returned:
  - A `transcript` event becomes an item holding its text, phrase and stretch.
  - A `score` event whose stretch has no transcript, from a phrase of kind `openwakeword` or `sherpa_kws`, becomes a stretch marker item (the glyph bullet).
  - A `wake` pops the newest item for that phrase in the current stretch. On a transcript, it marks the span the phrase covers, found by `phrase_span(text, phrase)` using the same normalisation as containment: lower case, apostrophes dropped, and a match only at a word start (WW-6). If no span is found, the whole item is popped.
- `Ticker.advance()` is called once a frame. Items are joined with a separator into one strip that enters at the right and moves one column per frame. When the waiting text is longer than the width, it moves faster, so it is never more than one width behind.
- `Ticker.view(width)` returns the visible characters, each tagged plain or wake.
- Text that has scrolled off the left is dropped. Nothing is written anywhere.
- `muted` is set while the face holds the listener. Events that arrive while muted are dropped.
- `Ticker.down` is set while the stream is disconnected. The view is then `The listener is down: nothing is being heard.` and does not scroll.

**Done when:** tests feeding recorded event lists show all of these:
- "hey autobot" scrolls in from the right, and its `autobot` cells are tagged wake.
- A `sherpa_kws` stretch shows one marker, popped on its wake.
- Nothing is added while muted.
- The down sentence appears when disconnected.
- The model holds no more than one width of backlog after a long feed.

## 5. Eyes and reactions as data

**Files:** new `src/autobot/tui/face_reactions.py`, `tests/tui/test_tui_face_reactions.py`.

- A `Pose` is a pupil position (`left`, `center`, `right`, each one of `up`, `middle` or `down`) and a lid state (`open`, `wide`, `half`, `closed`). It is held for a time in milliseconds: a fixed value, or a `[min, max]` range.
- A `Reaction` is a list of poses plus `loop` and `random` flags. `reaction_set(mapping)` builds a set from plain data and rejects an unknown pose word with a one-line error.
- `BUILTIN_REACTIONS` is a data mapping inside the module, not drawing code. It defines:
  - *idle*: random and looping; glances in four directions and back to centre, a blink about once in six poses, and holds of 0.8 to 3 s.
  - *wake*: eyes wide and centred.
  - *listening*: half-closed with slow side glances.
  - *speaking*.
  - *working*: looking up-left and up-right.
  - *trouble*: half-closed, looking down.
  - *done*: a blink, then centre.
- The `Eyes` animator takes a clock and a `random.Random`. `play(name, reactions)` takes the experience's set and falls back to the built-in reaction for any name the set doesn't define. A reaction that doesn't loop holds its last pose for a moment and then returns to *idle*. `pose()` gives the current pose.
- `draw_eyes(pose, glyphs) -> list[str]` draws two eyes with three interior columns and two interior rows, so a pupil can look up or down as well as sideways. It uses rounded line-drawing characters when `glyphs.block_art` is set and plain ASCII (`.-.`, `( o )`, `'-'`) otherwise. There are no eyebrows and nothing else.

**Done when:**
- With a seeded random and a fake clock, *idle* goes through at least three different pupil positions and a closed-lid pose within 30 s.
- `play("wake")` changes the pose on the very next call.
- A set that defines only *wake* still plays the built-in *done*.
- The ASCII drawing contains only printable ASCII.

## 6. The meters

**Files:** new `src/autobot/tui/face_meters.py`, `tests/tui/test_tui_face_meters.py`.

- `MicMeter` wraps a `Capture` and the input stream it reads. It has these states, each with a header text:
  - `live`: the level and the hold, banded with `meter_zones`.
  - `silent`: `NO SIGNAL`, using `Capture.delivering_silence`.
  - `off`: shown off the NUC.
  - `failed`: shows the device error.
- `mouth_level(clip, elapsed)` is the RMS of the block of the clip being played at `elapsed` seconds. It is 0 before the start and after the end. `Mouth` follows whichever clip is playing, the speech line or the recording, and is flat otherwise.

**Done when:** a test plays a committed fixture clip through `Mouth` with a fake clock. The level is above the meter floor during the speech, 0 after its end, and 0 while nothing plays. A `Capture` fed zeros for 2 s puts `MicMeter` in `silent`.

## 7. The panels and the experience frame

**Files:** new `src/autobot/tui/face.py`, `tests/tui/test_tui_face.py`.

- `SummaryPanel` keeps lines with the newest at the bottom.
  - Before the first wake it holds `Say "<first wake phrase>" to wake me.`
  - `clear()` runs at the start of each session.
  - `trouble(text)` marks a line for the warning role.
- `DetailPanel` keeps lines. `append(line)` adds one, `update(line)` replaces the last line, `clear()` empties it, and a scroll offset with `page` works like the Live monitor's.
- The `Experience` protocol has `reactions` (a set, or None), `start(face)`, `tick(face)` and `done`. `FaceContext` is what an experience drives:
  - the two panels;
  - `react(name)`;
  - `say(text)`, which puts the line in the summary and queues the same text to be spoken, so the spoken summary and the shown summary are one text (FS-8);
  - the capture, the voice, the hold and playback;
  - the clock.
- `FaceModel` owns the ticker, the eyes, the meters, the panels and the running experience. `feed(events)` does three things:
  - It passes every event to the ticker.
  - On every `wake` it plays *wake* at once.
  - If no experience is running, it clears the panels and starts a new echo. A wake during an experience reaches only the ticker and the eyes.

**Done when:**
- Feeding two wakes 1 s apart starts one echo, and the second wake pops on the ticker without restarting the echo.
- The summary shows the prompt before the first wake and is cleared at the second session's wake.

## 8. The echo

**Files:** new `src/autobot/tui/echo.py`, `tests/tui/test_tui_echo.py`.

The `Echo` state machine never blocks. Each step runs in `tick`:

1. **Woken.** Summary `I heard you.`; the *wake* reaction is already playing. Checks NUC, input, output and voice. Any one that fails gives *trouble* with its line, then the end.
2. **Speaking.** The line is `Hey, I'm going to record your voice. Go ahead.` It is rendered once per face visit, in the background (`Services.spawn`), when the screen opens on the NUC. If the render isn't ready yet, the echo waits for it.
   - The echo starts `listener_hold_argv(clip.seconds)` through `Services.runner`, mutes the ticker, and waits for the command.
   - A hold that fails because the listener is not running is not trouble: there is nothing to hold. Any other failure is shown as one detail line, and the echo goes on.
   - Then it calls `audio.play` on the resolved output device. The eyes play *speaking* and the mouth follows the clip.
3. **Recording.** `Capture.start_recording(5.0)` and the *listening* reaction. The summary counts down `Recording: N s left`, and the detail line is updated with `recorded 2.4 s of 5.0 s, level -23 dBFS`.
4. **Playing back.** Summary `Playing it back.` The echo holds the listener for the recording's length, then plays the in-memory `Clip` on the output device. The mouth follows the recording.
5. **Done.** The *done* reaction, summary `Echo finished. Say the wake phrase to go again.`, and the ticker is unmuted. The clip reference is dropped, and `done` is set.

The recording is never passed to `write_wav`, the voice dataset or any path. When the face closes, `leave` calls `stop_playback` and `stop_recording` and drops the clip.

**Done when:** a test drives `FaceModel` with fake events, the fake audio backend fed from a committed fixture WAV, the fake voice, a fake runner and a fake clock, and checks all of these:

- The states run in order.
- Two `control hold` commands are started, sized to the speech and to the 5.0 s recording.
- `audio.play` gets the spoken clip and then a 5.0 s clip.
- The mouth moves during both and is flat after.
- No file is created in the scratch repository or voice directory.
- With the voice fake raising the missing-model error, the summary shows that one line under *trouble* and the model is idle again.

## 9. The Face screen

**Files:** new `src/autobot/tui/screens/face.py`, `tests/tui/test_tui_face_screen.py`; modified `src/autobot/tui/screens/main.py`, `tests/tui/test_tui_app.py`.

- `FaceScreen(View)`, titled `Face`.
- **`enter`.** On the NUC, it opens the input stream into the `MicMeter`'s capture and starts rendering the speech line. Off the NUC, it calls `app.require_nuc("the face's microphone and voice")`, which shows the usual line, and leaves the meter and mouth idle.
- **`tick`.** It polls `services.events` and feeds the model, advances the ticker and the eyes, and runs the echo.
- **`leave`.** It closes the stream, stops playback, and closes the event stream.
- **Drawing,** inside the frame:
  - Row 0 is the header: `MIC` and the compact meter at the left, `FACE` in the middle, and `listener: connected` or `listener: down` at the right.
  - The upper band is `max(6, body // 3)` rows. It holds the **Summary** panel (about 60 % of the width) and the **autobot** panel, with the eyes centred and the mouth meter under them.
  - The **Detail** panel takes the rest of the body.
  - The last row inside the frame is the ticker, with wake cells in `theme.wake`.
  - The app's message line and key bar stay below.
  - Every part fits the 72×22 minimum and grows with the terminal.
- **Keys.** Up, Down, PgUp, PgDn and End scroll the detail panel. Help lines for F1 cover the same keys. `T` works through the app.
- Add **Face** to the main menu as the tenth entry (key `0`), before Quit, with the hint `autobot's face: wake it and it echoes your voice back`.

**Done when:**
- The screen test drives `App` with the fake screen at 72×22 and at 120×40, then opens Face from the main menu with `0`. The frame title shows `Face`, the header shows `MIC`, and the summary shows the say-the-wake-phrase prompt.
- After a fake `transcript` and `wake`, the ticker row holds `autobot` drawn with the `wake` attribute and the eye rows have changed.
- With the fake events disconnected, the ticker reads the listener-down sentence.
- Off the NUC, the screen opens and the message line reads `the face's microphone and voice runs on the NUC only; this host is …`.
- `make test`, `make lint` and `make typecheck` pass.
- On the NUC, with `make up` running, `make deploy`, then **Face**, then saying "autobot" runs the five echo steps on screen and through the speaker, and the ticker shows nothing while autobot speaks.

## 10. Choices the spec leaves open

1. **The spoken line** is `Hey, I'm going to record your voice. Go ahead.` (FS-11 leaves the words to the plan.)
2. **Hold sizing.** The face asks for the clip's own length. The listener adds `ACK_TAIL_SECONDS` itself, so "a moment after" is defined in one place.
3. **Hold transport.** The request file and a signal (`SIGUSR2`), like `snapshot`. The listener's socket only writes (WW-10's semantics), so a request can't go that way.
4. **Hold cap.** A hold is capped at 60 s, so a faulty request can't leave the listener deaf for long.
5. **A hold when the listener is down** is not trouble. Nothing can wake on the playback, so the echo goes on.
6. **Rendering the line.** It is rendered once per visit to the screen, when it opens. That keeps the first wake's reply quick, as the listener does by pre-rendering "Yes?".
7. **The face renders speech itself** with `face_voice`, which imports only `enforce_offline` from `autobot.listener.models`. That module is the shared offline guard, not the listener process. Reusing it keeps one list of offline variables for `CN-2` instead of a copy that could drift. The face never imports the listener's service, events or control modules.
8. **Wake spans** are found by the face's own `phrase_span`, using the containment rule's normalisation. If whisper's text doesn't contain the phrase, the whole item is popped.
9. **Markers instead of words** are shown only for a stretch that has no transcript. With both kinds configured, words take precedence.
10. **Ticker speed** is one column per frame, about ten a second, and faster when text is waiting.
11. **Face is menu entry ten**, so the existing entries keep their numbers. Quit becomes the eleventh entry and keeps `q` and F10.
12. **Empty transcripts** are not published.

## 11. Out of scope

- Parsing an agent's response into a TTS summary block and a detail part. No agent exists yet; `Experience.say` is the single-text path the echo uses.
- Choosing an experience by a wake's target.
- Agent-carried reaction sets stored beside agent definitions.
- Eyebrows or other features.
- Recording until the owner stops talking.
- Capture after a wake in the listener (WW-4).
- Holding the listener while the echo records.

## Settled without asking

- **Derived (FS-3, WW-10).** Transcripts are a socket-only event, like scores, and come before the stretch's scores and wakes.
- **Derived (FS-12, WW-8).** The hold drops audio and resets detectors exactly as the acknowledgement does, through the same steps.
- **Derived (TS-10).** Holds go through `python -m autobot.listener.control hold` as a subprocess, and events come from `run/listener.sock`.
- **Derived (TS-11, WW-9).** Off the NUC, the face's audio refuses with `App.require_nuc`'s wording, and the screen still opens.
- **Derived (TS-13).** The wake pop is a new `wake` theme role, red in all three palettes, and needs no new colour pairs.
- **Derived (TS-4, TS-5).** The echo's clip goes to no file and is dropped at the end of the echo or when the screen closes. A test confirms no file appears.
- **Default.** The detail panel scrolls with the Live monitor's keys, as FS-9 asks ("the keys the other scrolling screens use").
- **Default.** The listener-down sentence is `The listener is down: nothing is being heard.`
- **Default.** The listener's new tests go in `tests/listener/test_service.py` and a new `tests/listener/test_control.py`, beside the existing listener tests.

## Changes needed elsewhere

- **docs/specs/active/wake-word-listener-SPEC.md.** Add two rules, as the face spec asks: the socket-only `transcript` event, published before the stretch's scores; and the `control hold --seconds` command, which drops audio and resets detectors for the stated time plus the acknowledgement's tail, capped at 60 s.
- **docs/context/SCHEMA.md.** Add the `transcript` event's fields beside `WakeEvent` and `ScoreEvent`, and the `run/hold.request` hand-off beside the snapshot files.
- **docs/context/ARCH.md.** Add the Face screen to the interface's screens and the echo as the first speaker on the Spoken response seam. Under the capture open question, note that the echo records through the interface's own `Capture`.
- **docs/context/GLOSSARY.md.** Add "face", "experience", "reaction", "ticker", "echo" and "TTS summary".
- **docs/specs/active/agent-sessions-SPEC.md.** In AS-4, replace "shown on the wall UI" with the face: the summary panel and the detail panel.
- **AGENTS.md.** Add the Face screen to the interface's screen list, and `control hold` to what the interface uses.
- **README.md.** Under "Running it", add opening **Face** and saying the wake phrase to hear the echo.
- **CHANGELOG.md.** Under Unreleased, record the face, the echo, the `transcript` event and `control hold`.

## Open Questions

None.
