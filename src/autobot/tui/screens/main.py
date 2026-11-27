"""The main menu: the studio's road first, then the settings screens.

The top of the screen is the menu beside the banner; below them a panel shows
where the wake-word listener stands (`autobot.tui.state`), what the voice
dataset holds, and whether a session is complete, which is when the first
training run is offered (TS-2). Training is never refused on less.
"""

from __future__ import annotations

import textwrap
from collections.abc import Sequence

from autobot.tui.app import App, MenuView
from autobot.tui.menu import MenuItem
from autobot.tui.screens.audio import AudioScreen
from autobot.tui.screens.candidate import CandidateScreen
from autobot.tui.screens.dataset import DatasetScreen, minutes_text
from autobot.tui.screens.gcp import GcpScreen
from autobot.tui.screens.host import HostScreen
from autobot.tui.screens.monitor import open_monitor
from autobot.tui.screens.routing import RoutingScreen
from autobot.tui.screens.service import ServiceScreen
from autobot.tui.screens.session import open_session
from autobot.tui.screens.wake_phrase import TrainScreen, WakePhraseScreen
from autobot.tui.server_state import LINE_ROLES as SERVER_LINE_ROLES
from autobot.tui.server_state import summary as server_summary
from autobot.tui.session import complete_speakers
from autobot.tui.state import BADGE_ROLES, State, explain, leftover_model
from autobot.tui.ui import Canvas, Rect, fit, fit_left
from autobot.wakeword.data import phrase_table
from autobot.wakeword.dataset import (
    LABEL_AMBIENT,
    LABEL_NEGATIVE,
    LABEL_POSITIVE,
    DatasetError,
    LabelCount,
)

# "autobot" in block letters, two rows high; `BANNER_PLAIN` where the terminal
# cannot draw them (ASCII mode).
BANNER = (
    "▄▀█ █ █ ▀█▀ █▀█ █▄▄ █▀█ ▀█▀",
    "█▀█ █▄█  █  █▄█ █▄█ █▄█  █ ",
)
BANNER_PLAIN = ("", "a u t o b o t")
TAGLINE = "wake-word training studio"
# The menu's column; the banner takes the rest of the width.
MENU_WIDTH = 30
BANNER_ROWS = 5
# The rows the state panel under the menu needs: two for the state's
# sentence, then the dataset line and the training line.
PANEL_ROWS = 4
# How often the dataset line is counted again while the menu is on top.
STATS_SECONDS = 10.0

TRAINING_NOT_YET = "Training is offered once a session is complete"


def dataset_line(counts: dict[str, LabelCount]) -> str:
    return (
        f"Dataset: {counts[LABEL_POSITIVE].clips} positives, "
        f"{counts[LABEL_NEGATIVE].clips} negatives, "
        f"{minutes_text(counts[LABEL_AMBIENT].minutes)} min ambient"
    )


def open_agent_editor(app: App) -> None:
    """Open the Agent editor's tree; its module is loaded when it is first opened."""
    from autobot.tui.screens.agent_editor import AgentTreeScreen

    app.push(AgentTreeScreen(app))


def open_word_lists(app: App) -> None:
    from autobot.tui.screens.agent_words import WordListsScreen

    app.push(WordListsScreen(app))


class MainMenu(MenuView):
    title = "Main Menu"

    def enter(self) -> None:
        self.counts: dict[str, LabelCount] | None = None
        self.complete: list[str] = []
        self.dataset_error = ""
        # `App.changes` when the menu was last on top, to see it come back.
        self.seen = -1
        self.stats_at = 0.0
        self.refresh_stats()

    def tick(self) -> None:
        app = self.app
        back_on_top = self.seen != app.changes
        self.seen = app.changes
        # Whether the listener runs: asked when the menu is entered or comes
        # back on top, and every ten seconds while it stays there.
        app.poll_listener(now=back_on_top)
        if back_on_top or app.services.clock() - self.stats_at >= STATS_SECONDS:
            self.refresh_stats()

    def refresh_stats(self) -> None:
        """Count the dataset again; the manifest is small, the answer is kept."""
        services = self.app.services
        phrase = self.app.config.wake_phrases[0].phrase
        self.stats_at = services.clock()
        try:
            self.counts = services.dataset.counts(phrase)
            self.complete = complete_speakers(
                services.dataset,
                phrase,
                services.build_script(phrase, phrase_table(phrase)),
            )
            self.dataset_error = ""
        except DatasetError as error:
            self.counts = None
            self.complete = []
            self.dataset_error = str(error)

    def items(self) -> list[MenuItem]:
        app = self.app
        # The *candidate ready* state links to the screen that decides on it.
        ready = app.state() is State.CANDIDATE_READY
        return [
            MenuItem(
                "Recording session",
                lambda: open_session(app),
                "Record your voice, one prompt at a time; leave and resume any time",
            ),
            MenuItem(
                "Train the model",
                lambda: app.push(TrainScreen(app)),
                "Trains on the dataset; the run writes a candidate and installs nothing",
            ),
            MenuItem(
                "Candidate (ready)" if ready else "Candidate",
                lambda: app.push(CandidateScreen(app)),
                "A trained model awaits your decision: compare it, install it"
                if ready
                else "Compare the candidate with the installed model; install or revert",
            ),
            MenuItem(
                "Live monitor",
                lambda: open_monitor(app),
                "Watch the listener's scores; mark detections right, wrong or missed",
            ),
            MenuItem(
                "Dataset",
                lambda: app.push(DatasetScreen(app)),
                "The recorded clips: play, delete, export one as a test fixture",
            ),
            MenuItem(
                "Audio",
                lambda: app.push(AudioScreen(app)),
                "Pick the microphone and speaker, watch the level, record a test clip",
            ),
            MenuItem(
                "Wake phrase",
                lambda: app.push(WakePhraseScreen(app)),
                "Threshold, detector kind, model file, the spoken acknowledgement",
            ),
            MenuItem(
                "Service",
                lambda: app.push(ServiceScreen(app)),
                "Install, enable, start and stop the listener's systemd unit",
            ),
            MenuItem(
                "Host",
                lambda: app.push(HostScreen(app)),
                "Name this machine as the NUC",
            ),
            MenuItem(
                "Agent editor",
                lambda: open_agent_editor(app),
                "The agents on their tree: walk it with the arrows, Enter edits one",
            ),
            MenuItem(
                "Agent routing",
                lambda: app.push(RoutingScreen(app)),
                "The rules that hand a command after the wake to an agent",
            ),
            MenuItem(
                "Word lists",
                lambda: open_word_lists(app),
                "The yes words, the no words and the global action keywords",
            ),
            MenuItem(
                "Google Cloud",
                lambda: app.push(GcpScreen(app)),
                "Connect autobot to Google Cloud as its own service account, "
                "one step at a time",
            ),
            MenuItem("Quit", app.quit, "Leave autobot"),
        ]

    def key_bar(self) -> Sequence[tuple[str, str]]:
        # Tab opens the face: run mode (AG-8). Esc quits here, as F10 does.
        return (
            ("F1", "Help"),
            (self.app.canvas.glyphs.up_down, "Move"),
            ("Enter", "Select"),
            ("Tab", "Face"),
            ("F10", "Quit"),
        )

    # --- drawing ------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        self.refresh_menu()
        rows = max(len(self.menu.items), BANNER_ROWS)
        menu_width = min(MENU_WIDTH, rect.width)
        self.draw_menu(canvas, Rect(rect.y, rect.x, rows, menu_width))
        self.draw_banner(
            canvas, Rect(rect.y, rect.x + menu_width, rows, rect.width - menu_width)
        )
        # The panel wants four rows under a rule. A terminal of the minimum
        # height has three under the menu's fourteen entries: the rule is
        # left out there, and the panel fits itself to what it is given.
        ruled = rect.height - rows - 1 >= PANEL_ROWS
        if ruled:
            canvas.rule(rect, rows, canvas.theme.frame)
        top = rows + 1 if ruled else rows
        if top < rect.height:
            self.draw_panel(
                canvas, Rect(rect.y + top, rect.x, rect.height - top, rect.width)
            )

    def draw_banner(self, canvas: Canvas, rect: Rect) -> None:
        """The framed banner, and under it what the config file says."""
        theme = canvas.theme
        if rect.width < 20:
            return
        inside = canvas.panel(Rect(rect.y, rect.x, BANNER_ROWS, rect.width))
        letters = BANNER if canvas.glyphs.block_art else BANNER_PLAIN
        for row, line in enumerate(letters):
            canvas.put_in(
                inside, row, max(0, (inside.width - len(line)) // 2), line, theme.banner
            )
        canvas.put_in(
            inside,
            len(letters),
            max(0, (inside.width - len(TAGLINE)) // 2),
            TAGLINE,
            theme.accent,
        )
        config = self.app.config
        services = self.app.services
        phrase = config.wake_phrases[0]
        nuc = config.host.nuc_hostname
        here = nuc.lower() == services.hostname.lower()
        facts = (
            ("NUC", f"{nuc or '(not set)'}{' (this machine)' if nuc and here else ''}"),
            ("Audio", f"{config.audio.input_device} / {config.audio.output_device}"),
            ("Phrase", f"{phrase.phrase!r}, {phrase.kind}, {phrase.threshold:g}"),
            ("Theme", canvas.theme.name or "no colours"),
            ("Config", fit_left(str(services.store.path), rect.width - 11)),
        )
        row = BANNER_ROWS
        for row, (label, value) in enumerate(facts, start=BANNER_ROWS):
            if row >= rect.height:
                break
            canvas.put_in(rect, row, 2, label, theme.dim)
            canvas.put_in(rect, row, 10, fit(value, rect.width - 11), theme.dim)
        # The inference server's state, with its one-line reason, under what
        # the config file says and beside the listener's state (IS-23).
        row += 1
        if row < rect.height:
            watch = self.app.server
            canvas.put_in(rect, row, 2, "Server", theme.dim)
            canvas.put_in(
                rect,
                row,
                10,
                fit(server_summary(watch), rect.width - 11),
                getattr(theme, SERVER_LINE_ROLES[watch.state]),
            )

    def state_lines(self, width: int, indent: int) -> list[str]:
        """The state's sentence, folded so that its first line starts at `indent`."""
        services = self.app.services
        leftover = leftover_model(self.app.config, services.models_root)
        shown = None
        if leftover is not None:
            try:
                shown = str(leftover.relative_to(services.models_root))
            except ValueError:
                shown = str(leftover)
        text = explain(self.app.state(), shown)
        lines = textwrap.wrap("\x00" * indent + text, max(indent + 8, width))
        return [line.replace("\x00", " ") for line in lines]

    def training_line(self) -> tuple[str, str]:
        if not self.complete:
            return TRAINING_NOT_YET, "dim"
        names = ", ".join(self.complete)
        return f"A session is complete ({names}): Train the model is next", "ok"

    def draw_panel(self, canvas: Canvas, rect: Rect) -> None:
        theme = canvas.theme
        state = self.app.state()
        label = "State  "
        badge = f" {state.value.upper()} "
        canvas.put_in(rect, 0, 1, label, theme.normal)
        canvas.put_in(
            rect, 0, 1 + len(label), badge, getattr(theme, BADGE_ROLES[state])
        )
        indent = len(label) + len(badge) + 1
        lines: Sequence[str] = self.state_lines(rect.width - 2, indent)
        # Up to three rows for the sentence; the dataset and training lines
        # follow. A panel of three rows gives the sentence two of them.
        room = rect.height - 2 if rect.height >= PANEL_ROWS else rect.height - 1
        shown = lines[: max(1, min(3, room))]
        for row, line in enumerate(shown):
            start = indent if row == 0 else 0
            text = line[indent:] if row == 0 else line
            canvas.put_in(rect, row, 1 + start, fit(text, rect.width - 2 - start))
        # With four rows or more the dataset line keeps its place on the third
        # row. With three it follows the sentence, and the training line is
        # shown when the sentence left it a row.
        row = max(2, len(shown)) if rect.height >= PANEL_ROWS else len(shown)
        if self.counts is not None:
            canvas.put_in(rect, row, 1, fit(dataset_line(self.counts), rect.width - 2))
        else:
            canvas.put_in(
                rect,
                row,
                1,
                # The end of the message says what is wrong; its start is a path.
                "Dataset: " + fit_left(self.dataset_error, rect.width - 11),
                theme.warning,
            )
        text, role = self.training_line()
        canvas.put_in(rect, row + 1, 1, fit(text, rect.width - 2), getattr(theme, role))
