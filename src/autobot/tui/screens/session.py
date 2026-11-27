"""The recording session screen: one prompt at a time, recorded on the NUC.

It draws `autobot.tui.session.Session` and dispatches keys to it; what a
session is, and how it resumes, is decided there. One input stream feeds both
the level meter and the recorder, opened as the Audio screen opens its own.

A take is stored in the voice dataset the moment its recording ends, so
leaving the screen, quitting, or a terminal too small to draw can never lose
one:

    Enter  start a take; stop it (a spoken prompt)
    P      play the latest take of the prompt shown
    R      record the latest take again (it is replaced)
    D      discard the latest take
    N, B   the next prompt, the one before
    Esc    stop a recording; otherwise leave (the session resumes later)

A muted microphone is caught before a take is made. While the input has been
digital silence for a couple of seconds the screen shows a NO SIGNAL line
where the RMS meter is and the message line says so; Enter and R then ask
before they record, because nothing said would be on the take. (A take that
is digital silence is flagged as well, as before.)

A hands-off prompt (the room, nobody speaking) counts down and stops itself;
Esc cancels it and nothing of it is kept, because a part of a minute is not
the minute the script asked for. The long optional prompt is streamed to a
temporary file under the dataset directory as it records and moved into place
when it ends; Enter stops it only once its minimum has passed.
"""

from __future__ import annotations

import contextlib
import curses
import textwrap
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from autobot.audio_check import AudioCheckError, dbfs, resolve_device
from autobot.tui.app import KEY_ESC, KEYS_ENTER, App, View
from autobot.tui.audio import (
    SILENCE_WARNING,
    AudioError,
    Capture,
    Clip,
    InputStream,
    StreamedClip,
    clip_peak,
    sanitise_clip_name,
)
from autobot.tui.screens.audio import (
    draw_meter,
    draw_silence_warning,
    input_device_name,
)
from autobot.tui.session import (
    Session,
    remove_stale_streams,
    stream_path,
    take_warning,
)
from autobot.tui.ui import Canvas, Rect, fit
from autobot.wakeword.data import phrase_table
from autobot.wakeword.dataset import ClipRecord, DatasetError
from autobot.wakeword.script import Prompt

# A take shorter than this holds no speech: the recording was stopped at once.
MIN_TAKE_SECONDS = 0.2
# A prompt that runs longer than this is streamed to disk, not held in memory.
STREAM_OVER_SECONDS = 120.0
# A take longer than this is not loaded to play it or to measure its peak,
# and discarding it asks first.
LONG_TAKE_SECONDS = 120.0
# D asks before it deletes a take longer than this.
CONFIRM_DISCARD_SECONDS = 30.0

SILENCE_QUESTION = "The microphone is delivering silence"


def clock_text(seconds: float) -> str:
    """Seconds as `m:ss`."""
    whole = max(0, int(seconds))
    return f"{whole // 60}:{whole % 60:02d}"


def minutes_words(seconds: float) -> str:
    """A length for a sentence: `60 minutes`, `1 minute`, `24 seconds`."""
    if seconds >= 60 and seconds % 60 == 0:
        minutes = int(seconds // 60)
        return f"{minutes} minute{'' if minutes == 1 else 's'}"
    return f"{seconds:g} seconds"


def large_type(text: str, width: int, rows: int) -> list[str]:
    """`text` as the prompt box shows it: spaced capitals when they fit.

    A short prompt becomes `A U T O B O T`; one too long for that is shown as
    written, folded over at most `rows` lines.
    """
    spaced = "   ".join(" ".join(word) for word in text.upper().split())
    if len(spaced) <= width:
        return [spaced]
    lines = textwrap.wrap(text, max(8, width)) or [""]
    if len(lines) > rows:
        lines = [*lines[: rows - 1], fit(" ".join(lines[rows - 1 :]), width)]
    return lines


@dataclass(frozen=True)
class TakeInfo:
    seconds: float
    # None when the take is too long to measure without loading it.
    peak: int | None


def open_session(app: App) -> None:
    """Ask who is speaking and open their session; the main menu's entry.

    Refuses with the one-line message on any machine but the NUC (TS-11).
    """
    if not app.require_nuc("the recording session"):
        return
    services = app.services
    try:
        initial = services.dataset.last_speaker()
    except DatasetError as error:
        app.error(str(error))
        return

    def complaint(text: str) -> str | None:
        try:
            sanitise_clip_name(text)
        except ValueError:
            return "the name needs at least one letter or digit"
        return None

    typed = app.prompt(
        "Recording session",
        "Who is speaking? The speaker's name:",
        initial,
        validate=complaint,
    )
    if typed is None:
        return
    phrase = app.config.wake_phrases[0].phrase
    script = services.build_script(phrase, phrase_table(phrase))
    try:
        session = Session(services.dataset, phrase, typed, script)
    except DatasetError as error:
        app.error(str(error))
        return
    if not session.script:
        app.error("The recording script is empty.")
        return
    app.push(SessionScreen(app, session))


class SessionScreen(View):
    title = "Recording session"
    help_lines = (
        "Enter          record a take; Enter again stops it",
        "P              play the latest take of this prompt",
        "R              record it again (the latest take is replaced)",
        "D              discard the latest take",
        "N, B           the next prompt, the one before",
        "Esc            while recording: stop. A room prompt: cancel it.",
        "",
        "Leaving loses nothing: the session resumes where it stopped.",
    )

    def __init__(self, app: App, session: Session) -> None:
        super().__init__(app)
        self.session = session
        self.index = 0
        self.devices: list[Mapping[str, Any]] = []
        self.capture = Capture(app.config.audio.sample_rate)
        self.stream: InputStream | None = None
        # The prompt whose recording is running, or has ended and is not stored yet.
        self.recording: Prompt | None = None
        self.takes: dict[str, TakeInfo] = {}
        # Whether the message line has said that the input is digital silence.
        self.silence_told = False

    # --- opening and closing --------------------------------------------------

    def enter(self) -> None:
        app = self.app
        session = self.session
        app.session_open = True
        # A long recording that was interrupted left a partial file: no manifest
        # line names it, so it is not a clip, and it goes.
        remove_stale_streams(session.dataset.root)
        try:
            self.devices = app.services.audio.query_devices()
        except AudioError as error:
            self.devices = []
            app.error(str(error))
        opened = self.start_meter()
        progress = session.progress()
        position = session.next_index()
        self.index = len(session.script) - 1 if position is None else position
        if not opened:
            return
        if position is None:
            app.say("Every prompt has a take. B goes back through them.", "ok")
        elif progress.complete:
            app.say(
                "The session is complete. This last prompt is optional: N skips it.",
                "ok",
            )
        elif progress.done:
            app.say(
                f"Welcome back, {session.speaker}: resuming at prompt "
                f"{self.index + 1} of {len(session.script)}.",
                "ok",
            )

    def leave(self) -> None:
        if self.recording is not None:
            self.end_recording(keep=self.keep_on_leaving())
        self.stop_meter()
        self.app.services.audio.stop_playback()
        self.app.session_open = False

    def tick(self) -> None:
        # A recording that reached its limit ended on the audio thread.
        if self.recording is not None and not self.capture.recording:
            self.collect(keep=True)
        self.watch_for_silence()

    def muted(self) -> bool:
        """Whether the open input has been digital silence for a couple of seconds."""
        return self.stream is not None and self.capture.delivering_silence

    def watch_for_silence(self) -> None:
        """Say so once when the input turns to digital silence, and when it ends."""
        silent = self.muted()
        if silent and not self.silence_told:
            self.app.error(f"No signal: {SILENCE_WARNING}.")
        elif self.silence_told and not silent and self.stream is not None:
            self.app.say("The microphone is delivering a signal again.", "ok")
        self.silence_told = silent

    def record_despite_silence(self) -> bool:
        """With a silent input, ask before recording; True when there is a signal."""
        if not self.muted():
            return True
        silent = clock_text(self.capture.silent_seconds)
        return self.app.confirm(
            SILENCE_QUESTION,
            [
                f"The input ({fit(self.device_name(), 40)}) has been",
                f"digital silence for {silent}: nothing said now",
                "would be on the take.",
                "",
                "Check the microphone's own mute button, then the",
                "source and its volume on the Audio screen.",
                "",
                "Record anyway?",
            ],
        )

    # --- the input stream -----------------------------------------------------

    def start_meter(self) -> bool:
        if self.stream is not None:
            return True
        audio = self.app.config.audio
        try:
            index = resolve_device(audio.input_device, "input", self.devices)
            self.stream = self.app.services.audio.open_input(
                index, audio.sample_rate, audio.frame_samples, self.capture.feed
            )
        except (AudioCheckError, AudioError) as error:
            self.app.error(str(error))
            return False
        return True

    def stop_meter(self) -> None:
        self.capture.stop_recording()
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        self.capture.reset_level()

    def device_name(self) -> str:
        return input_device_name(self.app.config.audio.input_device, self.devices)

    # --- the prompt shown -----------------------------------------------------

    @property
    def prompt(self) -> Prompt:
        return self.session.script[self.index]

    def info(self, record: ClipRecord) -> TakeInfo:
        """A take's length and peak, measured once."""
        known = self.takes.get(record.file)
        if known is not None:
            return known
        dataset = self.session.dataset
        try:
            seconds = dataset.seconds(record)
            peak = (
                clip_peak(dataset.load(record).samples)
                if seconds <= LONG_TAKE_SECONDS
                else None
            )
        except DatasetError:
            seconds, peak = 0.0, None
        self.takes[record.file] = TakeInfo(seconds, peak)
        return self.takes[record.file]

    def go(self, delta: int) -> None:
        session = self.session
        last = len(session.script) - 1
        if delta > 0 and self.index == last:
            if session.complete():
                self.app.say(
                    "That was the last prompt; the session is complete. "
                    "Esc, then Train.",
                    "ok",
                )
            else:
                position = session.next_index()
                if position is not None:
                    self.index = position
                    self.app.say(
                        f"Back to prompt {position + 1}: it has no take yet.", "warning"
                    )
            return
        self.index = max(0, min(last, self.index + delta))
        self.app.services.audio.stop_playback()

    # --- recording ------------------------------------------------------------

    def streams(self, prompt: Prompt) -> bool:
        """Whether a prompt's recording goes to disk as it is made."""
        return prompt.min_seconds > 0 or prompt.seconds > STREAM_OVER_SECONDS

    def start_recording(self) -> None:
        app = self.app
        prompt = self.prompt
        if not self.start_meter():
            return
        app.services.audio.stop_playback()
        try:
            if self.streams(prompt):
                self.capture.start_recording(
                    prompt.seconds, stream_path(self.session.dataset.root)
                )
            else:
                self.capture.start_recording(prompt.seconds)
        except AudioError as error:
            app.error(str(error))
            return
        self.recording = prompt
        if prompt.min_seconds > 0:
            app.say(
                f"Recording the room: up to {minutes_words(prompt.seconds)}; "
                f"Enter stops it after {minutes_words(prompt.min_seconds)}.",
                "warning",
            )
        elif prompt.hands_off:
            app.say(
                f"Recording the room for {minutes_words(prompt.seconds)}: "
                "say nothing. It stops by itself.",
                "warning",
            )
        else:
            app.say("Recording: speak now. Enter stops it.", "warning")

    def can_stop(self) -> bool:
        """Whether the running recording has passed its prompt's minimum."""
        prompt = self.recording
        return (
            prompt is not None and self.capture.recorded_seconds >= prompt.min_seconds
        )

    def keep_on_leaving(self) -> bool:
        """Whether a recording interrupted by leaving the screen is a take."""
        prompt = self.recording
        if prompt is None:
            return False
        if prompt.min_seconds > 0:
            return self.can_stop()
        return not prompt.hands_off

    def end_recording(self, *, keep: bool) -> None:
        self.capture.stop_recording()
        self.collect(keep=keep)

    def collect(self, *, keep: bool) -> None:
        """Take the recording that has ended and store it, or drop it."""
        prompt = self.recording
        self.recording = None
        clip = self.capture.take_clip()
        streamed = self.capture.take_streamed()
        if prompt is None:
            if streamed is not None:
                self.drop(streamed)
            return
        if streamed is not None:
            self.store_streamed(prompt, streamed, keep)
        elif clip is not None and keep:
            self.store(prompt, clip)
        else:
            self.app.say("Cancelled: nothing of that recording was kept.", "warning")

    def drop(self, streamed: StreamedClip) -> None:
        with contextlib.suppress(OSError):
            streamed.path.unlink()

    def store(self, prompt: Prompt, clip: Clip) -> None:
        app = self.app
        if clip.seconds < MIN_TAKE_SECONDS:
            app.say(
                f"Nothing was recorded ({clip.seconds:.1f} s). "
                "Enter starts a take, Enter stops it.",
                "warning",
            )
            return
        try:
            record = self.session.accept(prompt, clip, self.device_name())
        except DatasetError as error:
            app.error(f"Not saved: {error}")
            return
        peak = clip_peak(clip.samples)
        self.takes[record.file] = TakeInfo(clip.seconds, peak)
        self.announce(prompt, clip.seconds, peak)

    def store_streamed(
        self, prompt: Prompt, streamed: StreamedClip, keep: bool
    ) -> None:
        app = self.app
        if streamed.error:
            # The disk filled, say. What was written is a take when it is long enough.
            keep = keep and streamed.seconds >= max(
                prompt.min_seconds, MIN_TAKE_SECONDS
            )
        if not keep or streamed.seconds < MIN_TAKE_SECONDS:
            self.drop(streamed)
            if streamed.error:
                app.error(f"The recording stopped: {streamed.error}. Nothing was kept.")
            else:
                app.say("Cancelled: nothing of that recording was kept.", "warning")
            return
        try:
            record = self.session.accept_file(prompt, streamed.path, self.device_name())
        except DatasetError as error:
            self.drop(streamed)
            app.error(f"Not saved: {error}")
            return
        self.takes[record.file] = TakeInfo(streamed.seconds, streamed.peak)
        if streamed.error:
            app.error(
                f"The recording stopped early ({streamed.error}); "
                f"{clock_text(streamed.seconds)} of it was kept."
            )
        else:
            self.announce(prompt, streamed.seconds, streamed.peak)

    def announce(self, prompt: Prompt, seconds: float, peak: int) -> None:
        """Say that a take was stored, with a warning when its level looks wrong."""
        app = self.app
        length = clock_text(seconds) if seconds >= 60 else f"{seconds:.1f} s"
        warning = take_warning(peak, prompt.label)
        if warning:
            stop = "" if warning.endswith("?") else "."
            app.say(f"Saved, but {warning}{stop} R redoes it.", "warning")
        elif not self.session.complete() or prompt.optional:
            app.say(
                f"Take saved ({length}, peak {dbfs(peak)}). N next, P play, R again.",
                "ok",
            )
        elif self.session.next_index() is not None:
            app.say(
                "The session is complete. N: the optional long prompt; Esc: leave.",
                "ok",
            )
        else:
            app.say("Everything is recorded. Esc, then Train the model.", "ok")

    # --- the keys of an idle prompt ---------------------------------------------

    def latest(self) -> ClipRecord | None:
        takes = self.session.clips_for(self.prompt)
        return takes[-1] if takes else None

    def play(self) -> None:
        app = self.app
        record = self.latest()
        if record is None:
            app.say("No take to play yet: Enter records one.")
            return
        info = self.info(record)
        if info.seconds > LONG_TAKE_SECONDS:
            app.say(
                f"That take is {clock_text(info.seconds)} long: too long to play here."
            )
            return
        try:
            clip = self.session.dataset.load(record)
            index = resolve_device(
                app.config.audio.output_device, "output", self.devices
            )
            app.services.audio.play(clip, index)
        except (DatasetError, AudioCheckError, AudioError) as error:
            app.error(str(error))
            return
        app.say(f"Playing the latest take ({clip.seconds:.1f} s).")

    def discard(self, *, ask: bool = True) -> bool:
        """Delete the latest take of the prompt shown. False when it is still there."""
        app = self.app
        record = self.latest()
        if record is None:
            app.say("No take to discard.")
            return True
        info = self.info(record)
        if (
            ask
            and info.seconds > CONFIRM_DISCARD_SECONDS
            and not app.confirm(
                "Discard the take?",
                [f"The take is {clock_text(info.seconds)} long.", "Delete it?"],
            )
        ):
            return False
        app.services.audio.stop_playback()
        try:
            self.session.discard(self.prompt)
        except DatasetError as error:
            app.error(f"Not discarded: {error}")
            return False
        self.takes.pop(record.file, None)
        app.say(f"Discarded the take {record.file}.", "warning")
        return True

    def record(self) -> None:
        if self.record_despite_silence():
            self.start_recording()

    def record_again(self) -> None:
        # Asked before the take that is there is discarded.
        if not self.record_despite_silence():
            return
        if self.latest() is None or self.discard():
            self.start_recording()

    # --- keys -------------------------------------------------------------------

    def key(self, key: int) -> bool:
        if self.recording is not None:
            self.key_while_recording(key)
            return True
        if key in KEYS_ENTER:
            self.record()
        elif key in (ord("p"), ord("P")):
            self.play()
        elif key in (ord("r"), ord("R")):
            self.record_again()
        elif key in (ord("d"), ord("D")):
            self.discard()
        elif key in (ord("n"), ord("N"), curses.KEY_RIGHT, curses.KEY_NPAGE):
            self.go(1)
        elif key in (ord("b"), ord("B"), curses.KEY_LEFT, curses.KEY_PPAGE):
            self.go(-1)
        else:
            return False
        return True

    def key_while_recording(self, key: int) -> None:
        """Only Enter and Esc do anything while a recording runs."""
        app = self.app
        prompt = self.recording
        assert prompt is not None
        if not self.capture.recording:
            # It ended by itself between two frames.
            self.collect(keep=True)
            return
        left = prompt.seconds - self.capture.recorded_seconds
        if prompt.min_seconds > 0:
            wait = prompt.min_seconds - self.capture.recorded_seconds
            if key in KEYS_ENTER or key == KEY_ESC:
                if self.can_stop():
                    self.end_recording(keep=True)
                elif key in KEYS_ENTER:
                    app.say(
                        f"It can be stopped and kept in {clock_text(wait)}. "
                        "Esc cancels it.",
                        "warning",
                    )
                elif app.confirm(
                    "Cancel the long recording?",
                    [
                        f"It has run {clock_text(self.capture.recorded_seconds)}.",
                        f"Under {minutes_words(prompt.min_seconds)} nothing is kept.",
                        "Stop it and discard what was recorded?",
                    ],
                ):
                    # It may have passed its minimum while the question stood.
                    self.end_recording(keep=self.can_stop())
            else:
                app.say("Recording the room: leave it running.", "warning")
        elif prompt.hands_off:
            if key == KEY_ESC:
                self.end_recording(keep=False)
            else:
                app.say(
                    f"It stops by itself in {clock_text(left)}. Esc cancels it.",
                    "warning",
                )
        elif key in KEYS_ENTER or key == KEY_ESC:
            self.end_recording(keep=True)
        else:
            app.say("Recording: Enter stops it.", "warning")

    def hint(self) -> str:
        prompt = self.recording
        if prompt is not None:
            if prompt.min_seconds > 0:
                wait = prompt.min_seconds - self.capture.recorded_seconds
                if wait > 0:
                    return (
                        f"Leave it running. Enter can stop it in {clock_text(wait)}; "
                        "Esc cancels it."
                    )
                return "Leave it running, or press Enter to stop it and keep the take."
            if prompt.hands_off:
                return "Say nothing. It stops by itself; Esc cancels it."
            return "Speak now. Enter stops the recording."
        prompt = self.prompt
        if prompt.optional:
            return (
                f"Optional: Enter starts {minutes_words(prompt.seconds)} of room audio "
                f"({clock_text(prompt.min_seconds)} at least); N skips."
            )
        return "Enter records. Esc leaves; the session resumes where it stopped."

    def key_bar(self) -> Sequence[tuple[str, str]]:
        prompt = self.recording
        if prompt is not None:
            if prompt.min_seconds > 0:
                return (("Enter", "Stop (after the minimum)"), ("Esc", "Cancel"))
            if prompt.hands_off:
                return (("Esc", "Cancel"),)
            return (("Enter", "Stop"), ("Esc", "Stop"))
        return (
            ("Enter", "Rec"),
            ("P", "Play"),
            ("R", "Redo"),
            ("D", "Drop"),
            ("N", "Next"),
            ("B", "Back"),
            ("Esc", "Leave"),
        )

    # --- drawing ----------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        theme = canvas.theme
        session = self.session
        prompt = self.prompt
        progress = session.progress()
        width = rect.width - 2

        # Who, where in the script, and how much of it is done.
        place = f"Prompt {self.index + 1} of {len(session.script)}"
        canvas.put_in(rect, 0, 1, "Speaker", theme.normal)
        canvas.put_in(
            rect, 0, 9, fit(session.speaker, width - 10 - len(place)), theme.value
        )
        canvas.put_in(rect, 0, rect.width - 1 - len(place), place, theme.value)
        column = 1
        for label, (done, total) in progress.labels.items():
            if not total:
                continue
            text = f"{label} {done}/{total}"
            canvas.put_in(
                rect, 1, column, text, theme.ok if done >= total else theme.normal
            )
            column += len(text) + 3
        if progress.optional_total:
            text = f"+ optional {progress.optional_done}/{progress.optional_total}"
            canvas.put_in(rect, 1, column, text, theme.dim)
        percent = f"{round(100 * progress.fraction):3d}%"
        bar = max(0, width - len(percent) - 1)
        filled = min(bar, round(bar * progress.fraction))
        canvas.put_in(rect, 2, 1, canvas.glyphs.empty * bar, theme.dim)
        canvas.put_in(rect, 2, 1, canvas.glyphs.full * filled, theme.meter_low)
        canvas.put_in(rect, 2, 2 + bar, percent, theme.value)

        # What to do, and what to say, large and framed.
        canvas.put_in(rect, 3, 1, fit(prompt.instruction, width), theme.heading)
        box_rows = 5 if rect.height < 21 else 7
        box = Rect(rect.y + 4, rect.x + 1, box_rows, width)
        canvas.fill(box, theme.prompt)
        canvas.box(box, self.box_title(prompt), theme.prompt)
        inside = box.inset(1, 2)
        lines = large_type(prompt.text, inside.width, inside.height)
        top = max(0, (inside.height - len(lines)) // 2)
        for offset, line in enumerate(lines):
            canvas.put_in(
                inside,
                top + offset,
                max(0, (inside.width - len(line)) // 2),
                line,
                theme.prompt | curses.A_BOLD,
            )

        row = 4 + box_rows
        if self.stream is None:
            canvas.put_in(rect, row, 1, "Level  (no input stream)", theme.warning)
        else:
            level = self.capture.level()
            draw_meter(canvas, rect, row, "Peak", level.peak, level.hold)
            if self.capture.delivering_silence:
                draw_silence_warning(canvas, rect, row + 1)
            else:
                draw_meter(canvas, rect, row + 1, "RMS", level.rms, 0.0)
        self.draw_status(canvas, rect, row + 2)
        self.draw_takes(canvas, rect, row + 3)

    def box_title(self, prompt: Prompt) -> str:
        if prompt.optional:
            return "optional"
        return "say nothing" if prompt.hands_off else "say"

    def draw_status(self, canvas: Canvas, rect: Rect, row: int) -> None:
        theme = canvas.theme
        capture = self.capture
        prompt = self.recording
        width = rect.width - 2
        if prompt is None:
            takes = len(self.session.clips_for(self.prompt))
            if self.stream is None:
                text, attr = "Enter tries the input device again.", theme.warning
            elif takes:
                text = f"Ready. {takes} take{'' if takes == 1 else 's'} of this prompt."
                attr = theme.ok
            else:
                text, attr = (
                    "Ready. No take of this prompt yet: Enter records.",
                    theme.normal,
                )
            canvas.put_in(rect, row, 1, fit(text, width), attr)
            return
        elapsed = capture.recorded_seconds
        bullet = canvas.glyphs.bullet
        if prompt.min_seconds > 0:
            wait = prompt.min_seconds - elapsed
            when = (
                f"Enter can stop it in {clock_text(wait)}"
                if wait > 0
                else "Enter stops it and keeps the take"
            )
            text = (
                f"{bullet} REC {clock_text(elapsed)} of {clock_text(prompt.seconds)}"
                f"   {when}"
            )
        elif prompt.hands_off:
            text = (
                f"{bullet} REC {clock_text(prompt.seconds - elapsed)} left of "
                f"{clock_text(prompt.seconds)}   say nothing"
            )
        else:
            text = (
                f"{bullet} REC {elapsed:4.1f} s   Enter stops it "
                f"(by itself at {prompt.seconds:g} s)"
            )
        canvas.put_in(rect, row, 1, fit(text, width), theme.error)

    def draw_takes(self, canvas: Canvas, rect: Rect, row: int) -> None:
        theme = canvas.theme
        takes = self.session.clips_for(self.prompt)
        room = rect.height - row
        if room <= 0:
            return
        if not takes:
            canvas.put_in(rect, row, 1, "Takes  (none yet)", theme.dim)
            return
        canvas.put_in(rect, row, 1, f"Takes  {len(takes)}", theme.normal)
        shown = takes[-(room - 1) :] if room > 1 else []
        first = len(takes) - len(shown) + 1
        for offset, record in enumerate(shown):
            info = self.info(record)
            length = (
                clock_text(info.seconds)
                if info.seconds >= 60
                else f"{info.seconds:.1f} s"
            )
            level = "" if info.peak is None else f"peak {dbfs(info.peak)}"
            warning = "" if info.peak is None else take_warning(info.peak, record.label)
            text = f"{first + offset:>3}. {length:>7}  {level:<16} {record.recorded_at[11:19]}"
            canvas.put_in(rect, row + 1 + offset, 1, fit(text, rect.width - 2))
            if warning:
                canvas.put_in(
                    rect,
                    row + 1 + offset,
                    2 + len(text),
                    fit(warning.split(":")[0], rect.width - 3 - len(text)),
                    theme.warning,
                )
