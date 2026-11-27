---
run: schiele/0050
at: 2026-11-27T05:12:10.118Z
mode: answer
agent: answer
model: claude:claude-sonnet-5-5
target: docs/specs/active/autobot-the-face-SPEC.md
template: answer
prompt-sha: 1bcfd6651095
template-sha: 90976540f104
commit: 30fbe7a+
attachments: []
read:
  - AGENTS.md
  - docs/specs/active/autobot-the-face-SPEC.md
outcome: success
usage:
  input: 24883
  output: 7517
  peak: 24883
  window: 1000000
cost: 0
---

---
type: spec
title: autobot — The Face
authority: prescriptive
status: active
describes:
  - src/autobot/tui/**/face*
  - src/autobot/tui/**/echo*
  - tests/tui/**/*face*
  - tests/tui/**/*echo*
provenance:
  - run: schiele/0047
    at: 2026-11-27T05:06:07.761Z
    mode: create
  - run: schiele/0048
    at: 2026-11-27T05:11:13.528Z
    mode: revise
---

# autobot — The Face

The face is a new screen of the terminal user interface: the one the owner looks at while autobot is in use, as against the studio screens he uses to train it. It shows one session as it happens, in the same mid-90s terminal style as the rest of the application: a ticker of what the listener hears scrolling along the bottom, with a detected wake phrase popped in colour; a character made of ASCII-drawn eyes that look around and react, with a bounce meter of autobot's own speech for a mouth; a microphone meter so that the owner can see the microphone is live; a summary panel for what the agent says about its progress; and a detail panel the agent fills with whatever it is running. autobot here is an interface, not a cartoon: no robot, no transformer. The first thing the face does is the **echo**: autobot is woken, says it is going to record the owner's voice, records it, plays it back, and ends. Rules FS-1 to FS-14 govern it. It builds on the studio spec (TS-1, TS-10, TS-11, TS-13, TS-15), the listener spec (WW-4, WW-8, WW-10) and the agent sessions stub (AS-4), and withdraws nothing from them.

## Numbered rules, each with its reason

1. **FS-1. The face is a screen of the terminal user interface.** The face is one more screen of the application under `src/autobot/tui/`, reached from the main menu as **Face**, drawn with `curses` in the manner of a mid-90s bulletin board like every other screen, in the current theme; `T` switches themes on it as elsewhere (TS-13). It fills whatever terminal it is given. It is not the opening screen: `make deploy` still opens on the studio's main screen, and the face is a menu item. It is autobot's face in the sense of an interface screen: the character is a pair of eyes and nothing more, and no robot, vehicle or transformer figure is drawn. Reason: ADR 0006 and TS-1 make later jobs of the application further screens of the same interface, and the owner wants this one to look like the rest; the owner asked that autobot not be thought of as the cartoon transformer. The face is not the opening screen because the studio is still the owner's daily job until a model is trained.

2. **FS-2. The layout is a header, an upper band split in two, a detail panel, and a footer with the ticker.** From top to bottom: a header; an upper band, about a third of the body, split into the **summary** panel on the left and the **character** panel on the right; the **detail** panel, taking the rest of the body; and a footer whose lowest rows hold the **ticker** of FS-3 and the key hints the other screens show. The header holds the microphone meter of FS-4 at its left, the screen's name, and whether the face is connected to the listener. The arrangement is the owner's sketch:

   ```
   |||||
   | summary        | (o) (o) |
   |||||
   |     detail              |
   |                         |
   |||||
   | ticker ...              |
   ```

   Reason: the owner drew this layout; the summary is what he reads, the eyes are what he glances at, the detail is the agent's, and the ticker is the ephemeral stream that belongs at the edge.

3. **FS-3. The ticker scrolls what the listener hears, as it is transcribed, and pops a detected wake phrase in colour.** The footer's ticker is a single line of text that scrolls from right to left with each transcript the listener makes of the speech in the room, as soon as the listener publishes it. It is ephemeral: text scrolls off and is kept nowhere, not in a log and not in a file. When a transcript woke the listener, the words of the wake phrase, where they stand in that transcript, are drawn in the theme's wake colour so the owner sees that the word was caught. Transcripts come from the listener, over the event stream on `run/listener.sock`, and only from it: the face runs no speech-to-text of its own. A wake phrase whose kind does not transcribe speech (`openwakeword`, `sherpa_kws`) yields no words; for such a phrase the ticker shows a marker for each stretch and pops the marker on a wake. When the socket is absent the ticker says that the listener is down, in one short sentence, and nothing scrolls. Reason: the owner wants to watch what the system hears in real time and to know when it saw the word; the listener already transcribes every stretch under the `text_match` kind (WW-7) and already publishes per-stretch events to the socket (WW-10), so the stream is the one place the face can get the words without a second transcription (`CN-1`, TS-10). Nothing is kept because a transcript of the room is not something to store (the listener itself logs transcripts only under its debug switch, for that reason).

4. **FS-4. The microphone meter shows that the microphone is live.** The header's left end holds a bounce meter of the microphone's level, moving with the sound in the room, in the three bands (quiet, warm, hot) the Audio screen's meter already uses. It opens the configured input device (`[audio] input_device`) and reads it for as long as the face is open. When the input delivers digital silence the meter says so, as the Audio screen does. Reason: the owner asked for active feedback that the microphone is working; without it a quiet ticker could mean a quiet room or a dead microphone. The header's upper left is where the owner suggested it.

5. **FS-5. The character is a pair of ASCII-drawn eyes that look around and react.** The character panel draws two eyes in text characters: cute, simple, without eyebrows or any other feature for now. While nothing is happening they are alive: the pupils glance left, right, up, down and back to centre at irregular intervals, and the eyes blink now and then. When the listener publishes a wake the eyes react visibly and at once (the wake reaction of FS-7), before any speech is heard. The drawing is made of the characters every theme can show; where the terminal has no line-drawing or block characters, plain ASCII is used, as the banner does. Reason: the owner wants the face to have character, and eyes that move and react are the smallest thing that reads as a face. Eyebrows and further features are left for later so that the first animations can be made good on their own.

6. **FS-6. The mouth is a bounce meter of autobot's own speech.** Under the eyes, a small meter moves with the level of the text-to-speech audio while autobot speaks, so that the piper voice and the mouth move together; it is flat when autobot is silent. The same meter moves during the echo's playback of the owner's recording (FS-11), since that too comes out of autobot's mouth. Reason: the owner asked for a meter of the text-to-speech playback as the mouth; a meter driven by the samples being played is what keeps the two in step.

7. **FS-7. Reactions are named animations defined as data, and an agent can bring its own.** A reaction is a short, named animation of the eyes (a sequence of poses with their timing) played when something happens: at least *idle* (FS-5's looking around), *wake* (the phrase was heard), *listening* (autobot is recording the owner), *speaking*, *working* (the agent is processing), *trouble* (the agent reports difficulty or an error) and *done*. The face ships with one built-in set, which lives inside the application. The set is defined as data, not as drawing code, so that an experience (FS-10) can carry a set of its own and the face plays that set while the experience runs, falling back to the built-in reaction for any name the set does not define. An agent's own set is kept beside its agent definition once that shape is fixed; no separate file for reactions is made before then. Reason: the owner wants reactions to be fun, to grow into rules-based responses, and to be portable: attached to each agent experience as it is made. Data that the face plays, rather than code in the face, is what makes them portable and keeps one experience's reactions from touching another's. Keeping an agent's set beside its definition avoids two stores for one agent.

8. **FS-8. The summary panel shows what the agent says about the session.** The summary panel, left of the character, carries the agent-level messages of the session, newest at the bottom: the acknowledgement ("I've got you"), progress ("processing", "one moment"), trouble, errors, and at the end of a run the agent's summary of what it did. When an agent's response carries a spoken-summary block, the **TTS summary**, that block is what the summary panel shows and what autobot reads aloud, the two being one text, and the rest of the response goes to the detail panel. Before the first wake the panel says what to do: say the wake phrase. Messages stay until the next session begins. Reason: the owner distinguishes what the agent reports about its work, which he wants to read in one place and hear read back, from the work itself; a single block of summary text, produced by the agent for that purpose and read aloud as written, is the arrangement he has used before (AS-4 says the response is spoken and shown).

9. **FS-9. The detail panel belongs to the agent.** The detail panel shows whatever the running experience puts there, as lines of text the experience appends. The experience owns its content; the face draws it in the panel and does not interpret it. Content longer than the panel scrolls, and the owner can scroll back through it with the keys the other scrolling screens use. Reason: each agent experience is an application of its own that drives both a screen and a language-model agent; the face is the frame it renders into, and the owner wants the agent, not the face, to decide what the detail is. Lines of text are all the echo needs; the first agent will show what a richer model must hold.

10. **FS-10. An experience is what the face runs after a wake.** An experience is one spoken exchange the face can run from a wake to an end: it drives the summary panel, the detail panel, the reactions, autobot's speech and the microphone, and it ends in a *done* state after which the face is idle again. The face reacts to every wake the listener publishes (FS-3's pop and FS-7's wake reaction) whether or not an experience runs. Only one experience runs at a time; a wake during one is shown on the ticker and ignored by the experience. The echo of FS-11 is the first experience. Experiences are meant to be bound to agents reached through configured wake phrases (WW-4, AS-3), but for this version a wake's target does not select an experience: the face runs the echo on every wake while it is open, and binding by target waits until the agent sessions spec fixes what a target names. Reason: the owner describes the face as feeding agent experiences, each with its own reactions and its own detail, served from a wake; the face needs a name for that unit and a rule for how it begins and ends. `ack` is the only target built, and changing it would silence a wake made while the face is closed. The listener's acknowledgement (WW-8) stays as it is: it is the spoken "yes?", and the face's experience speaks after it.

11. **FS-11. The echo is the first experience.** The echo runs as follows, and each step is visible on the face:
    1. The listener publishes a wake. The ticker pops the phrase; the eyes play *wake*; the summary says the wake was heard.
    2. autobot says, in its voice, that it is going to record the owner's voice ("hey, I'm going to record your voice", or words to that effect fixed by the plan); the mouth meter moves with the speech; the summary shows the line.
    3. autobot records from the microphone for a fixed five seconds. The eyes play *listening*; the summary says it is recording and counts down how long remains; the detail panel shows the recording's running length and level.
    4. autobot plays the recording back on the configured output device (`[audio] output_device`); the mouth meter moves with the playback; the summary says it is playing back.
    5. The run ends: the eyes play *done*, the summary says the echo is finished, and the face is idle again.

    The recording is held in memory for the playback and discarded when the echo ends. It is written to no file, is not added to the voice dataset, and leaves the NUC in no form. A step that fails (no input device, no output device, no voice model) ends the echo with its one-line reason in the summary panel and the *trouble* reaction, and the face stays open. Reason: this is the exchange the owner described as the first interactive version, and it exercises every part of the face (wake, speech, recording, playback, the panels, the reactions) with no agent and no network. The voice dataset's clips are labelled by the prompt or mark that made them (TS-4) and an echo clip has no label, so it does not belong there; and an unlabelled recording of the owner kept nowhere is the simplest way to honour TS-5. The recording has a fixed length because the face has no voice-activity detector of its own and the first version is a test of the plumbing; recording until the owner stops speaking can follow once the listener's capture after a wake (WW-4) exists.

12. **FS-12. autobot does not hear itself.** While the face speaks or plays back audio, the listener does not wake: the audio of that time, and a moment after it, is not scored, as the listener already does for its own acknowledgement (WW-8). The face asks the listener for this through the listener's control interface for the length of the speech or playback, and the ticker shows nothing for that time. Reason: the echo plays the owner's own voice, which may contain the wake phrase, back into the microphone; an assistant that wakes on its own playback loops. The listener already knows how to hold its ears for the acknowledgement; this extends it to speech the face makes.

13. **FS-13. Everything the face hears and says is local, in autobot's voice.** The words on the ticker come from the listener's local speech-to-text (`CN-1`); autobot's speech is synthesised locally with piper-tts in the configured voice, `[ack].voice`, piper's Joe Medium (`CN-2`, TS-15); the microphone and the recording are read and played on the NUC's own audio devices. The face opens no network connection. Reason: `CN-1` and `CN-2` forbid any cloud speech path, and the owner wants the mouth to move with "the piper-tts voice", which is the voice the glossary names as autobot's.

14. **FS-14. The face is a front, its logic is testable without a terminal, and its audio runs on the NUC only.** The face holds no configuration of its own and reaches the listener only through `python -m autobot.listener.control` as a subprocess and the event stream on `run/listener.sock` (TS-10). The ticker's model, the eye animations and reactions, the meters, the panels' models and the echo's state machine live in modules that do no drawing and are tested with the committed fixtures and fakes for the audio backend, with no microphone, speaker or network. The microphone meter, autobot's speech and the echo's recording and playback refuse to run on a machine whose hostname differs from `host.nuc_hostname`, with the one-line message the other audio paths give (WW-9, TS-11); the screen still opens there, with the meter and mouth idle and the ticker reading from the socket if one exists. Reason: `CONVENTIONS.md` "Control layers" and "Testing" require all three of the studio's screens, and the face is another screen of the same interface.

## Settled without asking

- **Derived (TS-1, ADR 0006).** The face is a screen of the terminal user interface, not a new interface; no web, PWA or wall UI is built for it.
- **Derived (TS-13).** The face is drawn in the current theme and `T` switches themes on it. The "red" pop of the wake phrase is a theme role, so that each theme can draw it; in the three shipped themes the role is red.
- **Derived (TS-10, CONVENTIONS "Control layers").** The face gets transcripts and wakes from `run/listener.sock` and asks the listener for things only through `python -m autobot.listener.control`; it never imports the listener and runs no speech-to-text of its own.
- **Derived (CN-1, CN-2, TS-15).** Speech-to-text stays in the listener; autobot speaks with piper-tts in `[ack].voice`, Joe Medium; the mouth meter is driven by the samples being played.
- **Derived (WW-9, TS-11, CONVENTIONS "Control layers").** The microphone meter, the speech and the echo's recording and playback run on the NUC only and refuse elsewhere with the usual message.
- **Derived (TS-4, TS-5).** The echo's recording is not written to the voice dataset: dataset clips are labelled by the prompt or mark that made them, and the recording leaves the NUC in no form. It is held in memory and discarded.
- **Derived (WW-8).** The listener's "Yes?" acknowledgement is unchanged; the echo's spoken line follows it.
- **Derived (WW-7).** Only the `text_match` kind transcribes speech, so the ticker carries words for that kind and stretch markers for the others.
- **Default.** The upper band is about a third of the body and the detail panel the rest: the owner's sketch shows the detail panel the larger, and "upper third and bottom third" is read that way.
- **Default.** The microphone meter sits in the header's upper left, where the owner said it might go, in the Audio screen's three bands.
- **Default.** The eyes blink as well as glance in idle. The owner asked only that they look around; blinking is what makes drawn eyes read as alive, and it is one more pose in the data of FS-7.
- **Default.** The reaction names are *idle*, *wake*, *listening*, *speaking*, *working*, *trouble*, *done*: one per state the summary panel of FS-8 and the echo of FS-11 pass through. A set that defines fewer falls back to the built-in reaction for the rest.
- **Default.** The echo records through the face's own capture, the Audio screen's machinery, rather than through the listener; the listener holds no capture-after-wake today (WW-4 is not built) and the face already needs the microphone for its meter. The ARCH open question on where capture happens is not decided by this: the echo is a plumbing test.
- **Default.** The summary panel keeps its messages until the next wake, so a finished run can be read.
- **Default.** The face runs the echo on every wake while it is open, whatever the wake's target, until targets select experiences (FS-10).
- **Default.** The ticker shows text only as fast as the listener transcribes it: at the end of each stretch, or every two seconds of continuous speech under `text_match`, so "real time" is a second or two behind the voice.

## Changes needed elsewhere

- **docs/specs/active/wake-word-listener-SPEC.md.** Add as listener behaviour: a `transcript` event on `run/listener.sock` (socket only, never the log) carrying the text of each transcription a `text_match` detector makes, with its stretch identifier, published before the stretch's score lines; and a control command by which a reader asks the listener to hold its ears for a stated time, discarding audio and resetting the detectors as it does during the acknowledgement (FS-12).
- **docs/context/SCHEMA.md.** Add the `transcript` event's shape beside `WakeEvent` and `ScoreEvent`, and the hold-your-ears request to the control hand-off.
- **docs/context/ARCH.md.** Add the face to the terminal user interface's screens; note that the **Spoken response** seam now has a first spec for its display and the echo as its first speaker; record the capture default of FS-11 under the open question on where capture happens.
- **docs/context/GLOSSARY.md.** Add "face", "experience", "reaction", "ticker", "echo" and "TTS summary".
- **docs/specs/active/agent-sessions-SPEC.md.** AS-4 says the response is "shown on the wall UI"; it is shown on the face: the TTS summary in the summary panel, the rest in the detail panel (FS-8, FS-9).
- **AGENTS.md.** The interface's screens gain the face, and the main screen's list of what the interface does gains the echo.
- **README.md.** "Running it" gains the face and the echo as the first thing to try once the listener runs.
- **CHANGELOG.md.** Record the face and the echo under Unreleased.

## Open Questions

None.
