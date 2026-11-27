"""The Live monitor screen: the listener's scores as they happen, and marks.

It draws `autobot.tui.monitor`: the event stream of the running listener
(`run/listener.sock`) as a scrolling log, one line per score of a stretch of
speech and one per detection, with `WAKE` in the error colour. The stream is
polled once a frame and never waited for, so the screen stays responsive
whether or not the listener runs.

A mark saves the utterance it is about into the voice dataset (TS-9):

    R      the latest detection was right: its stretch, as a positive
    W      the latest detection was wrong: its stretch, as a negative
    M      a wake was missed: pick the stretch (the latest is selected) or
           "the last ten seconds", saved as a positive
    S      start the listener, when it is not running
    Up, Down, PgUp, PgDn, End   scroll the log; End follows it again

Each mark runs `python -m autobot.listener.control snapshot` behind the busy
box, reads the WAV the listener wrote, deletes it, and adds the clip with
`source = "monitor"`. The listener is not imported. A stretch is saved once:
marking it again says where it went. With more than one wake phrase
configured, R and W take the phrase of the detection and M asks which phrase
was missed.
"""

from __future__ import annotations

import curses
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from autobot.tui.app import App, View
from autobot.tui.audio import AudioError, sanitise_clip_name
from autobot.tui.monitor import (
    LAST_TEN_SECONDS,
    MARK_MISSED,
    MARK_RIGHT,
    MARK_WRONG,
    RECONNECT_SECONDS,
    SOCKET_PATH,
    LogLine,
    Mark,
    MonitorError,
    MonitorLog,
    clock_time,
    describe_stretch,
    mark_record,
    read_snapshot,
    score_text,
)
from autobot.tui.runner import (
    CANCELLED,
    LISTENER_CONTROL_MODULE,
    SNAPSHOT_FILE,
    SNAPSHOT_TIMEOUT,
    listener_control_argv,
    listener_snapshot_argv,
)
from autobot.tui.screens.audio import input_device_name
from autobot.tui.ui import Canvas, Rect, fit, wrap
from autobot.wakeword.dataset import DatasetError

# `control up` waits for the listener to load its models.
START_TIMEOUT = 120.0

HINT = "R right, W wrong, M missed: a mark saves that utterance in the dataset"
HINT_DOWN = "The listener is not running: S starts it. Marks need it running."
NOTHING_TO_MARK = "No detection is held: R and W mark the latest WAKE; M a miss."
WAKE_WORD = "WAKE"


def open_monitor(app: App) -> None:
    """Ask who is speaking and open the monitor; the main menu's entry.

    Refuses with the one-line message on any machine but the NUC (TS-11).
    """
    if not app.require_nuc("the live monitor"):
        return
    try:
        initial = app.services.dataset.last_speaker()
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
        "Live monitor",
        "Who is speaking? The marked clips carry this name:",
        initial,
        validate=complaint,
    )
    if typed is None:
        return
    app.push(MonitorScreen(app, typed.strip()))


class MonitorScreen(View):
    title = "Live monitor"
    help_lines = (
        "R              the latest detection was right: save it",
        "W              the latest detection was wrong: save it",
        "M              a wake was missed: pick the stretch to save",
        "S              start the listener, when it is not running",
        "Up, Down       scroll the log (also PgUp, PgDn); End follows it",
        "",
    )

    def __init__(self, app: App, speaker: str) -> None:
        super().__init__(app)
        self.speaker = speaker
        self.log = MonitorLog()
        self.was_connected = False
        # The stretches already saved, with the label and file they went to.
        self.marked: dict[int, tuple[Mark, str]] = {}
        self.saved = 0
        # Lines scrolled back from the end of the log; 0 follows it.
        self.scroll = 0
        self.page = 10
        self.seen = 0
        self.devices: list[Mapping[str, Any]] = []

    # --- opening and closing --------------------------------------------------

    def enter(self) -> None:
        try:
            self.devices = self.app.services.audio.query_devices()
        except AudioError:
            self.devices = []
        self.app.poll_listener(now=True)

    def leave(self) -> None:
        self.app.services.events.close()

    def tick(self) -> None:
        app = self.app
        events_source = app.services.events
        events = events_source.poll()
        connected = events_source.connected
        if (connected or events) and not self.was_connected:
            self.log.note("connected to the listener")
        self.log.feed(events)
        if not connected and (self.was_connected or events):
            # No replay, and a new listener numbers its stretches from 1 again.
            self.log.disconnected()
            self.log.note(
                f"the listener went away: trying again every {RECONNECT_SECONDS:g} s"
            )
        if connected != self.was_connected:
            self.was_connected = connected
            app.poll_listener(now=True)
        else:
            app.poll_listener()
        # A log scrolled back stays on the lines it shows.
        if self.scroll:
            self.scroll += self.log.added - self.seen
        self.seen = self.log.added

    @property
    def connected(self) -> bool:
        return self.app.services.events.connected

    # --- marks ----------------------------------------------------------------

    def device_name(self) -> str:
        """The input device the listener hears through, for the manifest."""
        return input_device_name(self.app.config.audio.input_device, self.devices)

    def mark_detection(self, mark: Mark) -> None:
        """R and W: save the latest detection's stretch."""
        detection = self.log.detection()
        if detection is None:
            self.app.say(NOTHING_TO_MARK)
            return
        self.save_mark(mark, detection.id, detection.wakes[-1].phrase)

    def missed(self) -> None:
        """M: save the stretch the owner picks, or the last ten seconds."""
        app = self.app
        stretches = list(reversed(self.log.stretches))
        options = [*(describe_stretch(stretch) for stretch in stretches)]
        options.append(LAST_TEN_SECONDS)
        picked = app.pick(
            "Which utterance was the missed wake?",
            options,
            0,
            note="It is saved into the dataset as a positive",
        )
        if picked is None:
            return
        phrases = [phrase.phrase for phrase in app.config.wake_phrases]
        phrase = phrases[0]
        if len(phrases) > 1:
            which = app.pick("Which wake phrase was missed?", phrases)
            if which is None:
                return
            phrase = phrases[which]
        stretch = stretches[picked].id if picked < len(stretches) else None
        self.save_mark(MARK_MISSED, stretch, phrase)

    def failed(self, line: str) -> None:
        """Say in full why nothing was saved; nothing was added."""
        app = self.app
        width = max(20, min(60, app.canvas.size()[1] - 12))
        app.message("Not saved", wrap([line], width))
        app.error(f"Not saved: {line}")

    def save_mark(self, mark: Mark, stretch: int | None, phrase: str) -> None:
        app = self.app
        services = app.services
        if stretch is not None and stretch in self.marked:
            earlier, file = self.marked[stretch]
            app.say(f"Stretch {stretch} is already saved as {earlier.label}: {file}")
            return
        if not services.module_available(LISTENER_CONTROL_MODULE):
            app.error(
                f"The listener is not built yet ({LISTENER_CONTROL_MODULE} is missing)."
            )
            return
        result = app.run_command(
            "Saving the utterance",
            listener_snapshot_argv(stretch),
            timeout=SNAPSHOT_TIMEOUT,
        )
        if result.returncode == CANCELLED:
            app.say("Cancelled: nothing was saved.")
            return
        if not result.ok:
            self.failed(result.summary())
            return
        # The command prints the file it wrote, relative to the repository.
        printed = result.summary()
        path = services.repo / (
            Path(printed) if printed.endswith(".wav") else SNAPSHOT_FILE
        )
        try:
            clip = read_snapshot(path)
            stored = services.dataset.add(
                clip, mark_record(mark, phrase, self.speaker, self.device_name())
            )
        except (MonitorError, DatasetError) as error:
            self.failed(str(error))
            return
        if stretch is not None:
            self.marked[stretch] = (mark, stored.file)
        self.saved += 1
        self.log.note(f"marked {mark.name}: saved as {mark.label}", stretch)
        app.say(f"Saved as {mark.label} ({clip.seconds:.1f} s): {stored.file}", "ok")

    def start_listener(self) -> None:
        app = self.app
        services = app.services
        if self.connected:
            app.say("The listener is running.")
            return
        if not services.module_available(LISTENER_CONTROL_MODULE):
            app.error(
                f"The listener is not built yet ({LISTENER_CONTROL_MODULE} is missing)."
            )
            return
        result = app.run_command(
            "Starting the listener", listener_control_argv("up"), timeout=START_TIMEOUT
        )
        if not result.ok:
            app.report_failure("The listener did not start", result)
            return
        # Connect now, not at the next two-second attempt.
        services.events.close()
        app.poll_listener(now=True)
        app.say(f"Start: {result.summary()}", "ok")

    # --- keys -----------------------------------------------------------------

    def key(self, key: int) -> bool:
        if key in (ord("r"), ord("R")):
            self.mark_detection(MARK_RIGHT)
        elif key in (ord("w"), ord("W")):
            self.mark_detection(MARK_WRONG)
        elif key in (ord("m"), ord("M")):
            self.missed()
        elif key in (ord("s"), ord("S")):
            self.start_listener()
        elif key == curses.KEY_UP:
            self.scroll += 1
        elif key == curses.KEY_DOWN:
            self.scroll = max(0, self.scroll - 1)
        elif key == curses.KEY_PPAGE:
            self.scroll += self.page
        elif key == curses.KEY_NPAGE:
            self.scroll = max(0, self.scroll - self.page)
        elif key == curses.KEY_END:
            self.scroll = 0
        else:
            return False
        return True

    def hint(self) -> str:
        return HINT if self.connected else HINT_DOWN

    def key_bar(self) -> Sequence[tuple[str, str]]:
        keys = [("R", "Right"), ("W", "Wrong"), ("M", "Missed")]
        if not self.connected:
            keys.append(("S", "Start"))
        return [*keys, ("Esc", "Back"), ("F10", "Quit")]

    # --- drawing --------------------------------------------------------------

    def facts(self) -> list[tuple[str, str, str]]:
        app = self.app
        if self.connected:
            listener = (f"connected: {SOCKET_PATH}", "ok")
        elif app.listener_running:
            listener = ("running, but its socket does not answer yet", "warning")
        elif app.listener_running is False:
            listener = ("not running: S starts it", "warning")
        else:
            listener = ("not connected: S starts the listener", "warning")
        stretches = self.log.stretches
        if stretches:
            first, last = stretches[0].id, stretches[-1].id
            held = (
                f"stretch #{first}"
                if first == last
                else f"stretches #{first} to #{last}",
                "value",
            )
        else:
            held = ("no stretch yet: the listener keeps the last five", "dim")
        detection = self.log.detection()
        if detection is None:
            latest = ("none held", "dim")
        else:
            wake = detection.wakes[-1]
            score = score_text(wake.score)
            latest = (
                f"#{detection.id}  {clock_time(wake.at)}  {wake.phrase} {score}",
                "value",
            )
        return [
            ("Listener", *listener),
            (
                "Speaker",
                f"{self.speaker}   (marks saved this visit: {self.saved})",
                "value",
            ),
            ("Held", *held),
            ("Detection", *latest),
        ]

    def draw_line(self, canvas: Canvas, rect: Rect, row: int, line: LogLine) -> None:
        theme = canvas.theme
        width = rect.width - 2
        stretch = "" if line.stretch is None else f"#{line.stretch}"
        head = f"{line.time}  {stretch:>7}  "
        if line.note:
            canvas.put_in(rect, row, 1, fit(head + line.text, width), theme.dim)
            return
        text = fit(head + line.text, width)
        canvas.put_in(rect, row, 1, text, theme.normal)
        at = text.find(WAKE_WORD) if line.wake else -1
        if at >= 0:
            canvas.put_in(rect, row, 1 + at, WAKE_WORD, theme.error)

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        theme = canvas.theme
        facts = self.facts()
        inside = canvas.panel(
            Rect(rect.y, rect.x, len(facts) + 2, rect.width),
            "Listener",
            title_attr=theme.heading,
        )
        for row, (label, value, role) in enumerate(facts):
            canvas.put_in(inside, row, 0, label, theme.normal)
            canvas.put_in(
                inside, row, 11, fit(value, inside.width - 11), getattr(theme, role)
            )
        header_row = len(facts) + 2
        header = (
            f"{'Time':<8}  {'Stretch':>7}  {'Phrase':<12} {'Kind':<13} {'Score':>5}"
        )
        canvas.put_in(rect, header_row, 1, fit(header, rect.width - 2), theme.heading)
        room = rect.height - header_row - 1
        if room <= 0:
            return
        self.page = room
        lines = list(self.log.lines)
        if not lines:
            empty = (
                "(nothing yet: speak, and the listener's scores appear here)"
                if self.connected
                else "(not connected to the listener: S starts it)"
            )
            canvas.put_in(
                rect, header_row + 1, 1, fit(empty, rect.width - 2), theme.dim
            )
            return
        self.scroll = max(0, min(self.scroll, len(lines) - room))
        shown = lines[: len(lines) - self.scroll][-room:]
        for row, line in enumerate(shown):
            self.draw_line(canvas, rect, header_row + 1 + row, line)
        if self.scroll:
            note = f" scrolled back {self.scroll} lines: End follows "
            canvas.put_in(
                rect,
                header_row,
                max(1, rect.width - len(note) - 1),
                note,
                theme.warning,
            )
