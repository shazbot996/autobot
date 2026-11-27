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
  - run: schiele/0049
    at: 2026-11-27T05:12:58.115Z
    mode: revise
  - run: schiele/0073
    at: 2026-11-27T06:22:14.633Z
    mode: revise
---

# autobot — The Face

The face is a new screen of the terminal user interface. The owner looks at it while autobot is in use, and uses the studio screens to train autobot. It shows one session as it happens, in the same mid-90s terminal style as the rest of the application. A ticker of what the listener hears scrolls along the bottom, and a detected wake phrase pops out of it in colour. A character made of ASCII-drawn eyes looks around and reacts, with a bounce meter of autobot's own speech for a mouth. A microphone meter shows the owner that the microphone is live. A summary panel holds what the agent says about its progress, and a detail panel holds whatever the agent is running. autobot here is an interface, not a cartoon: no robot, no transformer. The wake phrase only wakes autobot. The face then listens for a command, and the routing rules hand that command to an agent. The first agent is the **echo**. The owner wakes autobot and says "echo". autobot then says it is going to record his voice, records it, plays it back, and ends. Rules FS-1 to FS-15 govern the face. It builds on the studio spec (TS-1, TS-10, TS-11, TS-13, TS-15), the listener spec (WW-4, WW-6, WW-8, WW-10), the rules-first routing stub (RR-1, RR-3, RR-4) and the agent sessions stub (AS-4), and withdraws nothing from them.

Revised 2026-11-27: a wake no longer runs the echo. The face listens for a command after the wake and routes it by the rules (FS-10, FS-11, FS-15).

## Numbered rules, each with its reason

1. **FS-1. The face is a screen of the terminal user interface.** The face is one more screen of the application under `src/autobot/tui/`. It is reached from the main menu as **Face** and drawn with `curses` in the manner of a mid-90s bulletin board, like every other screen, in the current theme. `T` switches themes on it as elsewhere (TS-13). It fills whatever terminal it is given. It is not the opening screen: `make launch` still opens on the studio's main screen, and the face is a menu item. It is autobot's face in the sense of an interface screen. The character is a pair of eyes and nothing more, and no robot, vehicle or transformer figure is drawn. Reason: ADR 0006 and TS-1 make later jobs of the application further screens of the same interface, and the owner wants this one to look like the rest. The owner also asked that autobot not be thought of as the cartoon transformer. The face is not the opening screen because the studio is still the owner's daily job until a model is trained.

2. **FS-2. The layout is a header, an upper band split in two, a detail panel, and a footer with the ticker.** From top to bottom:
   - a header;
   - an upper band, about a third of the body, split into the **summary** panel on the left and the **character** panel on the right;
   - the **detail** panel, taking the rest of the body;
   - a footer whose lowest rows hold the **ticker** of FS-3 and the key hints the other screens show.

   The header holds three things: the microphone meter of FS-4 at its left, the screen's name, and whether the face is connected to the listener. The arrangement is the owner's sketch:

   ```
   |||||
   | summary        | (o) (o) |
   |||||
   |     detail              |
   |                         |
   |||||
   | ticker ...              |
   ```

   Reason: the owner drew this layout. The summary is what he reads, the eyes are what he glances at, the detail is the agent's, and the ticker is the ephemeral stream that belongs at the edge.

3. **FS-3. The ticker scrolls what the listener hears as it is transcribed, and pops a detected wake phrase in colour.** The footer's ticker is a single line of text that scrolls from right to left. It moves on with each transcript the listener makes of the speech in the room, as soon as the listener publishes it. It is ephemeral: text scrolls off and is kept nowhere, not in a log and not in a file. When a transcript woke the listener, the words of the wake phrase are drawn in the theme's wake colour, where they stand in that transcript, so the owner sees that the word was caught. Transcripts come only from the listener, over the event stream on `run/listener.sock`; the face runs no speech-to-text of its own. A wake phrase whose kind does not transcribe speech (`openwakeword`, `sherpa_kws`) yields no words. For such a phrase the ticker shows a marker for each stretch, and pops the marker on a wake. When the socket is absent, the ticker says in one short sentence that the listener is down, and nothing scrolls. Reason: the owner wants to watch what the system hears in real time and to know when it saw the word. Under the `text_match` kind the listener already transcribes every stretch (WW-7) and publishes per-stretch events to the socket (WW-10). The stream is therefore the one place the face can get the words without a second transcription (`CN-1`, TS-10). Nothing is kept because a transcript of the room is not something to store; for the same reason, the listener logs transcripts only under its debug switch.

4. **FS-4. The microphone meter shows that the microphone is live.** The header's left end holds a bounce meter of the microphone's level. It moves with the sound in the room, in the three bands (quiet, warm, hot) that the Audio screen's meter already uses. It opens the configured input device (`[audio] input_device`) and reads it for as long as the face is open. When the input delivers digital silence, the meter says so, as the Audio screen does. Reason: the owner asked for active feedback that the microphone is working. Without it, a quiet ticker could mean a quiet room or a dead microphone. The header's upper left is where the owner suggested it.

5. **FS-5. The character is a pair of ASCII-drawn eyes that look around and react.** The character panel draws two eyes in text characters: cute and simple, with no eyebrows or any other feature for now. While nothing is happening the eyes are alive. The pupils glance left, right, up, down and back to centre at irregular intervals, and the eyes blink now and then. When the listener publishes a wake, the eyes react visibly and at once, before any speech is heard (the wake reaction of FS-7). The drawing uses only characters every theme can show. Where the terminal has no line-drawing or block characters, plain ASCII is used, as the banner does. Reason: the owner wants the face to have character, and eyes that move and react are the smallest thing that reads as a face. Eyebrows and further features are left for later, so that the first animations can be made good on their own.

6. **FS-6. The mouth is a bounce meter of autobot's own speech.** Under the eyes, a small meter moves with the level of the text-to-speech audio while autobot speaks, so that the piper voice and the mouth move together. It is flat when autobot is silent. The same meter moves during the echo's playback of the owner's recording (FS-11), since that too comes out of autobot's mouth. Reason: the owner asked for a meter of the text-to-speech playback as the mouth. A meter driven by the samples being played is what keeps the two in step.

7. **FS-7. Reactions are named animations defined as data, and an agent can bring its own.** A reaction is a short, named animation of the eyes, a sequence of poses with their timing, played when something happens. The face has at least these reactions:
   - *idle*: FS-5's looking around;
   - *wake*: the phrase was heard;
   - *listening*: autobot is recording the owner;
   - *speaking*;
   - *working*: the agent is processing;
   - *trouble*: the agent reports difficulty or an error;
   - *done*.

   The face ships with one built-in set, which lives inside the application. The set is defined as data, not as drawing code, so that an experience (FS-10) can carry a set of its own. The face plays that set while the experience runs, and falls back to the built-in reaction for any name the set does not define. An agent's own set is kept beside its agent definition once that shape is fixed; no separate file for reactions is made before then. Reason: the owner wants reactions to be fun, to grow into rules-based responses, and to be portable, attached to each agent experience as it is made. Data that the face plays, rather than code in the face, makes reactions portable and keeps one experience's reactions from touching another's. Keeping an agent's set beside its definition avoids two stores for one agent.

8. **FS-8. The summary panel shows what the agent says about the session.** The summary panel, left of the character, carries the agent-level messages of the session, newest at the bottom: the acknowledgement ("I've got you"), progress ("processing", "one moment"), trouble, errors, and at the end of a run the agent's summary of what it did. When an agent's response carries a spoken-summary block, the **TTS summary**, the summary panel shows that block and autobot reads it aloud; the two are one text. The rest of the response goes to the detail panel. Before the first wake, the panel says what to do: say the wake phrase. Messages stay until the next session begins. Reason: the owner distinguishes what the agent reports about its work from the work itself. He wants to read the report in one place and hear it read back. He has used this arrangement before: a single block of summary text, produced by the agent for that purpose and read aloud as written. AS-4 says the response is spoken and shown.

9. **FS-9. The detail panel belongs to the agent.** The detail panel shows whatever the running experience puts there, as lines of text that the experience appends. The experience owns its content; the face draws it in the panel and does not interpret it. Content longer than the panel scrolls, and the owner can scroll back through it with the keys the other scrolling screens use. Reason: each agent experience is an application of its own that drives both a screen and a language-model agent. The face is the frame it renders into, and the owner wants the agent, not the face, to decide what the detail is. Lines of text are all the echo needs; the first language-model agent will show what a richer model must hold.

10. **FS-10. An experience is what an agent runs on the face after its command.** An experience is one spoken exchange that an agent runs on the face, from the command that routed to it to an end. It drives the summary panel, the detail panel, the reactions, autobot's speech and the microphone. It ends in a *done* state, after which the face is idle again. The face reacts to every wake the listener publishes, with FS-3's pop and FS-7's wake reaction.
    - **A wake begins a session.** A wake while the face is idle begins a session: the face listens for a command, then runs the experience of the agent the command routes to (FS-15). The wake alone runs no experience.
    - **One session at a time.** A wake during a session is shown on the ticker and the eyes, and the session ignores it.
    - **The echo is the first.** The echo of FS-11 is the first agent's experience.
    - **The command chooses the agent.** A wake's `target` does not select an agent. Every wake phrase only wakes autobot, and the command said after it chooses the agent.
    - **The acknowledgement stays.** The listener's acknowledgement (WW-8) stays as it is: it is the spoken "yes?" that answers the wake, and the face speaks after it.

    Reason: the owner wants to ask autobot for an agent, not have one run on every wake. He wants one command per agent as agents are added, in the way Dialogflow routes calls. RR-1 lets no query reach an agent without a rule routing it there. The face needs a name for the unit an agent runs, and a rule for how a session begins and ends. `ack` is the only target built, and changing it would silence a wake made while the face is closed.

11. **FS-11. The echo is the first agent's experience.** The echo runs as follows, and each step is visible on the face:
    1. The listener publishes a wake. The ticker pops the phrase, the eyes play *wake*, and the face listens for a command (FS-15).
    2. The owner says a sentence with `echo` in it: "echo" alone, "please echo my voice", or "autobot, echo that" in one breath. The detail panel names the rule that matched and what was heard. The summary says the command is routed to the echo agent, then that it heard him.
    3. autobot says, in its voice, "Hey, I'm going to record your voice. Go ahead." The mouth meter moves with the speech, and the summary shows the line.
    4. autobot records from the microphone for a fixed five seconds. The eyes play *listening*. The summary says it is recording and counts down how long remains. The detail panel shows the recording's running length and level.
    5. autobot plays the recording back on the configured output device (`[audio] output_device`). The mouth meter moves with the playback, and the summary says it is playing back.
    6. The run ends. The eyes play *done*, and the summary says the echo is finished and that saying the wake phrase and "echo" runs it again. The face is idle again.

    The recording is held in memory for the playback and discarded when the echo ends. It is written to no file, is not added to the voice dataset, and leaves the NUC in no form. A step can fail: no input device, no output device, or no voice model. A failed step ends the echo with its one-line reason in the summary panel and the *trouble* reaction, and the face stays open. Reason: this is the exchange the owner described as the first interactive version, now asked for by its command. It exercises every part of the face (wake, command, speech, recording, playback, the panels, the reactions) with no language-model agent and no network. The echo clip is kept out of the voice dataset because the dataset's clips are labelled by the prompt or mark that made them (TS-4), and an echo clip has no label. Keeping an unlabelled recording of the owner nowhere is the simplest way to honour TS-5. The recording has a fixed length for two reasons: the face has no voice-activity detector of its own, and the first version is a test of the plumbing. Recording until the owner stops speaking can follow once the listener captures speech after a wake (WW-4).

12. **FS-12. autobot does not hear itself.** While the face speaks or plays back audio, the listener does not wake. The audio of that time, and of a moment after it, is not scored, as the listener already does for its own acknowledgement (WW-8). The face asks the listener for this through the listener's control interface, for the length of the speech or playback, and the ticker shows nothing for that time. Reason: the echo plays the owner's own voice, which may contain the wake phrase, back into the microphone, and an assistant that wakes on its own playback loops. The listener already knows how to hold its ears for the acknowledgement; this rule extends that to speech the face makes.

13. **FS-13. Everything the face hears and says is local, in autobot's voice.**
    - **Hearing.** The words on the ticker come from the listener's local speech-to-text (`CN-1`).
    - **Speaking.** autobot's speech is synthesised locally with piper-tts in the configured voice, `[ack].voice`, piper's Joe Medium (`CN-2`, TS-15).
    - **Devices.** The microphone and the recording are read and played on the NUC's own audio devices.
    - **Network.** The face opens no network connection.

    Reason: `CN-1` and `CN-2` forbid any cloud speech path. The owner wants the mouth to move with "the piper-tts voice", which is the voice the glossary names as autobot's.

14. **FS-14. The face is a front, its logic is testable without a terminal, and its audio runs on the NUC only.**
    - **A front.** The face holds no configuration of its own. It reaches the listener only through `python -m autobot.listener.control` as a subprocess and the event stream on `run/listener.sock` (TS-10).
    - **Testable logic.** These parts live in modules that do no drawing: the ticker's model, the eye animations and reactions, the meters, the panels' models, the router of FS-15 and the echo's state machine. They are tested with the committed fixtures and fakes for the audio backend, with no microphone, speaker or network.
    - **NUC only.** The microphone meter, autobot's speech, and the echo's recording and playback refuse to run on a machine whose hostname differs from `host.nuc_hostname`. They give the same one-line message as the other audio paths (WW-9, TS-11). The screen still opens on such a machine, with the meter and mouth idle and the ticker reading from the socket if one exists.

    Reason: `CONVENTIONS.md` "Control layers" and "Testing" require all three of these of the studio's screens, and the face is another screen of the same interface.

15. **FS-15. After a wake the face listens for a command and routes it by the rules.**
    - **Listening.** When a wake begins a session, the summary says which commands autobot knows (`Listening for a command: say "echo".`), the eyes play *listening*, and the face listens for up to ten seconds.
    - **What counts.** The words said after the wake phrase in the transcript that woke the listener count, so "autobot, echo that" routes at once. Words said before the wake phrase do not count. After that, each transcript the listener publishes is tried in turn.
    - **Matching.** Each transcript is tried against the routing rules, the `[[routes]]` entries of `config/autobot.toml`, in the order the file lists them. A rule matches when its command stands among the transcript's words, by the listener's containment rule (WW-6). The first rule that matches wins.
    - **No match.** A transcript that no rule matches becomes one line in the detail panel, and the face keeps listening.
    - **A match.** The detail panel names the rule and what was heard, and the summary says which agent has the command. That agent's experience then starts, with its own reactions (FS-7).
    - **No command.** When ten seconds pass without a command, the summary says `No command heard. Say the wake phrase to try again.`, the eyes play *done*, and the session ends.
    - **Nothing to route.** The session ends at once, with its one-line reason in the summary and the *trouble* reaction, in three cases: no rule is configured, the matching rule names an agent that is not built, or no wake phrase is of the `text_match` kind.
    - **Silence.** The face makes no sound of its own while it listens: the listener's acknowledgement is the reply to the wake.
    - **Sources.** The commands come only from the listener's transcripts (FS-3, `CN-1`).
    - **The Agent routing screen.** The rules are listed, edited and tried on the **Agent routing** screen, the main-menu entry after Face.

    Reason: the owner wants the wake phrase only to wake autobot and the word after it to choose the agent. This is the first step of a basic rules engine that will grow into routing (RR-1, RR-3, RR-4). A command often comes inside a longer sentence, so containment is the match. Only `text_match` publishes words today, so without it there is nothing to route.

## Settled without asking

- **Derived (RR-1, RR-3).** A wake alone runs no experience. Only a command that a rule routes reaches an agent, and the rule names that agent.
- **Derived (WW-6).** A command is matched by the listener's containment rule, so it is found inside a longer sentence, as the owner said it would be.
- **Derived (WW-8).** The face says nothing while it listens for a command: the listener's "Yes?" is the reply to the wake.
- **Derived (FS-3, WW-7).** Only `text_match` publishes words. Without a wake phrase of that kind, a session ends at once and says that no command can be heard.
- **Default.** The face listens for a command for ten seconds after the wake. That is long enough to finish a short sentence, and short enough that a missed command ends cleanly.
- **Default.** Words said before the wake phrase in the waking transcript are not a command. Words after it are, so "autobot, echo that" works in one breath.
- **Default.** A transcript that matches no rule is one line in the detail panel, and the face keeps listening until the ten seconds are up. A sentence without a command is not an error.
- **Resolved.** The echo's closing line now tells the owner to say the wake phrase and "echo" to run it again, since a wake alone no longer runs it (FS-10, FS-11).

## Changes needed elsewhere

- **docs/specs/active/wake-word-listener-SPEC.md.** Add two listener behaviours:
  - a `transcript` event on `run/listener.sock` (socket only, never the log). It carries the text of each transcription a `text_match` detector makes, with its stretch identifier, and is published before the stretch's score lines;
  - a control command by which a reader asks the listener to hold its ears for a stated time. The listener discards audio and resets the detectors, as it does during the acknowledgement (FS-12).
- **docs/context/SCHEMA.md.** Add the `transcript` event's shape beside `WakeEvent` and `ScoreEvent`, and add the hold-your-ears request to the control hand-off.
- **docs/context/ARCH.md.** Make these changes:
  - add the face and the Agent routing screen to the terminal user interface's screens;
  - note that the **Spoken response** seam now has a first spec for its display, with the echo as its first speaker;
  - note that the **Rules-first routing** seam has its first form, the face's command routing (FS-15);
  - record the capture default of FS-11 under the open question on where capture happens.
- **docs/context/GLOSSARY.md.** Add "face", "experience", "reaction", "ticker", "echo", "TTS summary", "command" and "Agent routing". The **wake phrase** entry says hearing one selects which agent the capture is for, but on the face the command after it does (FS-10).
- **docs/specs/active/rules-first-routing-SPEC.md.** Record the first form of a rule from FS-15. It is a `[[routes]]` entry in `config/autobot.toml` with a `command` and an `agent`. Rules are tried in file order, the first match wins, and a command matches by containment. When no rule matches, autobot keeps listening until its ten seconds are up, then says no command was heard. Also record the Agent routing screen. This answers that spec's questions on the form of a rule, where rules are stored, and what happens when none matches.
- **docs/specs/active/agent-sessions-SPEC.md.** AS-4 says the response is "shown on the wall UI". It is shown on the face instead: the TTS summary in the summary panel, the rest in the detail panel (FS-8, FS-9).
- **AGENTS.md.** The interface's screens gain the face and Agent routing. The main screen's list of what the interface does gains the echo, reached by its command.
- **README.md.** "Running it" gains the face and the echo as the first thing to try once the listener runs: open Face, say "autobot", wait for "Yes?", then say "echo".
- **CHANGELOG.md.** Record the face, the echo, command routing and the Agent routing screen under Unreleased.

## Open Questions

- **Commands under a trained model.** The face hears a command only while a wake phrase is of the `text_match` kind. Once a trained `openwakeword` model is installed, the listener publishes no words, and no command can be routed. Should the listener transcribe the speech after a wake whatever the phrase's kind? Recommended default: yes, as the capture after a wake that WW-4 already asks for. ADR 0006 is heading for an installed trained model, and routing would stop working the day it lands.
