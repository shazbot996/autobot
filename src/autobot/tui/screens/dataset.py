"""The Dataset screen: what the voice dataset holds, clip by clip.

Every clip in the manifest is listed with its speaker, label, length, prompt
and which side of the split it is on. A clip can be played, deleted, or
exported as one of the committed test fixtures. The export is the only way a
recording leaves the dataset directory, it is one clip at a time, and it asks
first (TS-5): nothing here writes a fixture as a side effect.

    Up, Down, PgUp, PgDn, Home, End   move through the list
    P or Enter                        play the clip
    D                                 delete it (asks)
    E                                 export it as a test fixture (asks)
"""

from __future__ import annotations

import curses
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from autobot.audio_check import AudioCheckError, resolve_device
from autobot.tui.app import KEYS_ENTER, View
from autobot.tui.audio import AudioError
from autobot.tui.ui import Canvas, Rect, fit, fit_left
from autobot.wakeword.dataset import (
    FIXTURE_LEAD_IN,
    FIXTURE_NAMES,
    FIXTURE_TAIL,
    HELDOUT,
    LABEL_AMBIENT,
    LABEL_NEGATIVE,
    LABEL_POSITIVE,
    ClipRecord,
    DatasetError,
    HeldoutSize,
    LabelCount,
    export_fixture,
    split,
)
from autobot.wakeword.evaluate import MIN_VERDICT

# The smallest held-out set the comparison gives a verdict on (TS-8): twenty
# positives, twenty negatives, twelve minutes of ambient. The comparison is
# the authority; this screen only shows how far the dataset is from it.
VERDICT_POSITIVES = MIN_VERDICT.positives
VERDICT_NEGATIVES = MIN_VERDICT.negatives
VERDICT_AMBIENT_MINUTES = MIN_VERDICT.ambient_minutes
# A clip longer than this is not loaded to be played.
LONG_CLIP_SECONDS = 120.0

AMBIENT_SPLIT = "last fifth held out"
SPLIT_WIDTH = len(AMBIENT_SPLIT)
DELETE_WARNING = (
    "Deleting a clip changes the held-out set and makes",
    "earlier evaluations incomparable.",
)


def split_text(record: ClipRecord) -> str:
    """Which side of the split a clip is on, as the list shows it."""
    if record.label == LABEL_AMBIENT:
        return AMBIENT_SPLIT
    return "held out" if split(record) == HELDOUT else "train"


def minutes_text(minutes: float) -> str:
    """Minutes as a count line shows them: `0`, `1.5`, `5`."""
    return f"{round(minutes, 1):g}"


def length_text(seconds: float) -> str:
    if seconds >= 60:
        return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"
    return f"{seconds:.1f}"


class DatasetScreen(View):
    title = "Dataset"
    help_lines = (
        "Up, Down       move through the clips (also PgUp, PgDn)",
        "Enter, P       play the selected clip",
        "D              delete it (asks first)",
        "E              export it as a test fixture (asks first)",
        "",
    )

    def enter(self) -> None:
        self.records: list[ClipRecord] = []
        self.seconds: dict[str, float] = {}
        self.counts: dict[str, LabelCount] = {}
        self.heldout = HeldoutSize()
        self.error = ""
        self.selected = 0
        self.top = 0
        # Rows of the list at the last draw: what a page is.
        self.page = 10
        self.devices: list[Mapping[str, Any]] = []
        try:
            self.devices = self.app.services.audio.query_devices()
        except AudioError:
            self.devices = []
        self.load()

    def leave(self) -> None:
        self.app.services.audio.stop_playback()

    def load(self) -> None:
        """Read the manifest and the clips' lengths again."""
        dataset = self.app.services.dataset
        phrase = self.app.config.wake_phrases[0].phrase
        try:
            self.records = dataset.records()
            self.seconds = {}
            for record in self.records:
                try:
                    self.seconds[record.file] = dataset.seconds(record)
                except DatasetError:
                    self.seconds[record.file] = 0.0
            self.counts = dataset.counts(phrase)
            self.heldout = dataset.heldout_size(phrase)
            self.error = ""
        except DatasetError as error:
            self.records = []
            self.counts = {}
            self.heldout = HeldoutSize()
            self.error = str(error)
        self.selected = max(0, min(self.selected, len(self.records) - 1))

    def shown(self, path: Path) -> str:
        """A path relative to the repository when it is inside it."""
        try:
            return str(path.relative_to(self.app.services.repo))
        except ValueError:
            return str(path)

    @property
    def current(self) -> ClipRecord | None:
        return self.records[self.selected] if self.records else None

    # --- actions --------------------------------------------------------------

    def play(self) -> None:
        app = self.app
        record = self.current
        if record is None:
            return
        if self.seconds.get(record.file, 0.0) > LONG_CLIP_SECONDS:
            app.say(
                f"{record.file} is {length_text(self.seconds[record.file])} long: "
                "too long to play here."
            )
            return
        try:
            clip = app.services.dataset.load(record)
            index = resolve_device(
                app.config.audio.output_device, "output", self.devices
            )
            app.services.audio.play(clip, index)
        except (DatasetError, AudioCheckError, AudioError) as error:
            app.error(str(error))
            return
        app.say(f"Playing {record.file} ({clip.seconds:.1f} s).")

    def delete(self) -> None:
        app = self.app
        record = self.current
        if record is None:
            return
        if not app.confirm(
            "Delete the clip?", [record.file, "", *DELETE_WARNING, "", "Delete it?"]
        ):
            return
        app.services.audio.stop_playback()
        try:
            app.services.dataset.remove(record.file)
        except DatasetError as error:
            app.error(f"Not deleted: {error}")
            return
        self.load()
        app.say(f"Deleted {record.file}.", "warning")

    def export(self) -> None:
        """Write the clip as one of the committed fixtures, after asking."""
        app = self.app
        record = self.current
        if record is None:
            return
        services = app.services
        directory = self.shown(services.fixture_dir)
        picked = app.pick(
            "Export as which fixture?",
            FIXTURE_NAMES,
            note=f"Into {fit_left(directory, 40)}/, which is committed",
        )
        if picked is None:
            return
        destination = services.fixture_dir / FIXTURE_NAMES[picked]
        lines = [
            f"{record.file}",
            f"  -> {fit_left(f'{directory}/{destination.name}', 60)}",
            "",
            "A fixture is committed: this recording becomes public.",
            f"Speech gets {FIXTURE_LEAD_IN:g} s of quiet before, {FIXTURE_TAIL:g} s after.",
        ]
        if destination.exists():
            lines.append("It replaces the fixture that is there now.")
        lines.extend(["", "Export it?"])
        if not app.confirm("Export the clip?", lines):
            app.say("Nothing was exported.")
            return
        try:
            export_fixture(record, services.dataset.path(record), destination)
        except DatasetError as error:
            app.error(f"Not exported: {error}")
            return
        app.say(f"Exported to {fit_left(f'{directory}/{destination.name}', 50)}.", "ok")

    # --- keys -----------------------------------------------------------------

    def move(self, delta: int) -> None:
        if self.records:
            self.selected = max(0, min(len(self.records) - 1, self.selected + delta))

    def key(self, key: int) -> bool:
        if key in (curses.KEY_UP, ord("k")):
            self.move(-1)
        elif key in (curses.KEY_DOWN, ord("j")):
            self.move(1)
        elif key == curses.KEY_PPAGE:
            self.move(-self.page)
        elif key == curses.KEY_NPAGE:
            self.move(self.page)
        elif key == curses.KEY_HOME:
            self.move(-len(self.records))
        elif key == curses.KEY_END:
            self.move(len(self.records))
        elif key in (*KEYS_ENTER, ord("p"), ord("P")):
            self.play()
        elif key in (ord("d"), ord("D")):
            self.delete()
        elif key in (ord("e"), ord("E")):
            self.export()
        else:
            return False
        return True

    def hint(self) -> str:
        if not self.records:
            return "No clips yet: the Recording session records them."
        return "P plays the clip, D deletes it, E exports it as a test fixture."

    def key_bar(self) -> Sequence[tuple[str, str]]:
        return (
            (self.app.canvas.glyphs.up_down, "Move"),
            ("P", "Play"),
            ("D", "Delete"),
            ("E", "Export"),
            ("Esc", "Back"),
            ("F10", "Quit"),
        )

    # --- drawing --------------------------------------------------------------

    def facts(self) -> list[tuple[str, str, str]]:
        if self.error:
            return [("Dataset", self.error, "warning")]
        counts = self.counts
        held = self.heldout
        enough = (
            held.positives >= VERDICT_POSITIVES
            and held.negatives >= VERDICT_NEGATIVES
            and held.ambient_minutes >= VERDICT_AMBIENT_MINUTES
        )
        ambient = counts[LABEL_AMBIENT]
        clips = (
            f"{counts[LABEL_POSITIVE].clips} positives, "
            f"{counts[LABEL_NEGATIVE].clips} negatives, "
            f"{ambient.clips} ambient ({minutes_text(ambient.minutes)} min)"
        )
        held_out = (
            f"{held.positives} of {VERDICT_POSITIVES} positives, "
            f"{held.negatives} of {VERDICT_NEGATIVES} negatives, "
            f"{minutes_text(held.ambient_minutes)} of "
            f"{VERDICT_AMBIENT_MINUTES:g} min ambient"
        )
        if enough:
            verdict = "the held-out set is large enough to compare two models"
        else:
            verdict = "too small yet to tell two models apart: figures only"
        return [
            ("Clips", clips, "value"),
            ("Held out", held_out, "ok" if enough else "warning"),
            ("Verdict", verdict, "ok" if enough else "dim"),
        ]

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        theme = canvas.theme
        facts = self.facts()
        panel = Rect(rect.y, rect.x, len(facts) + 2, rect.width)
        directory = self.shown(self.app.services.dataset.root)
        inside = canvas.panel(
            panel,
            f"Voice dataset: {fit_left(directory, rect.width - 26)}/",
            title_attr=theme.heading,
        )
        for row, (label, value, role) in enumerate(facts):
            canvas.put_in(inside, row, 0, label, theme.normal)
            canvas.put_in(
                inside, row, 9, fit(value, inside.width - 9), getattr(theme, role)
            )

        width = rect.width - 2
        prompt_width = max(6, width - 33 - SPLIT_WIDTH)
        header_row = panel.height
        header = (
            f"{'#':>3} {'Speaker':<10} {'Label':<8} {'Length':>6}  "
            f"{'Prompt':<{prompt_width}} Split"
        )
        canvas.put_in(rect, header_row, 1, fit(header, width), theme.heading)
        room = rect.height - header_row - 1
        if room <= 0:
            return
        self.page = room
        if not self.records:
            canvas.put_in(
                rect,
                header_row + 1,
                1,
                fit("(no clips yet: the Recording session records them)", width),
                theme.dim,
            )
            return
        # Keep the selection in view.
        self.top = max(0, min(self.top, len(self.records) - room))
        if self.selected < self.top:
            self.top = self.selected
        elif self.selected >= self.top + room:
            self.top = self.selected - room + 1
        for offset, record in enumerate(self.records[self.top : self.top + room]):
            number = self.top + offset
            text = (
                f"{number + 1:>3} {fit(record.speaker, 10):<10} {record.label:<8} "
                f"{length_text(self.seconds.get(record.file, 0.0)):>6}  "
                f"{fit(record.prompt, prompt_width):<{prompt_width}} {split_text(record)}"
            )
            chosen = number == self.selected
            if chosen:
                canvas.put_in(
                    rect, header_row + 1 + offset, 0, canvas.glyphs.pointer, theme.value
                )
            canvas.put_in(
                rect,
                header_row + 1 + offset,
                1,
                fit(text, width).ljust(width) if chosen else fit(text, width),
                theme.selected if chosen else theme.normal,
            )
        if len(self.records) > room:
            place = f" {self.selected + 1} of {len(self.records)} "
            canvas.put_in(
                rect, header_row, rect.width - 1 - len(place), place, theme.dim
            )
