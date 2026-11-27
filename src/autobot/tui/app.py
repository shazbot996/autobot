"""The application loop of the interface, its views and its dialogs.

The terminal user interface is the application (see the package docstring).
`App` owns a stack of views (the main menu, then whichever screen is open) and
draws one frame about ten times a second: title bar, the top view inside a
double-line frame, a message line and a key bar. Input never blocks for longer
than a frame, so the level meter and running commands keep updating.

Dialogs (`message`, `confirm`, `pick`, `prompt`, `wait`) are small nested
loops over the same frame function, which keeps screen code linear: ask, get
the answer, act.

`App` talks to the world only through `Services`, so tests build one with
fakes and drive the real application code through a scripted fake screen.

`T` switches to the next colour theme from anywhere, except while a text field
has the keyboard; the choice is written to `[tui] theme`.

Three environment variables, read only by `default_services` (the entry point
`python -m autobot.tui`), point the interface at other directories, for trying
it out without touching the real ones: `AUTOBOT_VOICE_DIR` replaces
`data/voice/`, `AUTOBOT_FIXTURE_DIR` replaces `tests/fixtures/audio/`, and
`AUTOBOT_MODELS_ROOT` replaces the repository root as the directory the model
files are looked for under (`models/wakeword/candidate.onnx`, the phrase's
relative `model` path and its `.prev`), so an install or a revert can be tried
on a copy. They are not configuration: nothing writes them and nothing else
reads them. The listener is not told about them: it loads its model from the
repository, as always.
"""

from __future__ import annotations

import curses
import curses.ascii
import locale
import os
import signal
import socket
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autobot import __version__
from autobot.config import Config, ConfigError, config_path, repo_root
from autobot.gcp.client import GoogleCloud
from autobot.gcp.client import connect as gcp_connect
from autobot.gcp.keyfile import GitCheckIgnore, git_check_ignore
from autobot.gcp.secrets import clear
from autobot.tui.audio import FIXTURE_DIR, AudioBackend, Clip, SoundDeviceBackend
from autobot.tui.config_edit import Change, ConfigEditError, ConfigStore
from autobot.tui.face import Seams
from autobot.tui.face_voice import synthesise
from autobot.tui.menu import Menu, MenuItem
from autobot.tui.monitor import SOCKET_PATH, EventSource, EventStream
from autobot.tui.pipewire import PipeWire
from autobot.tui.runner import (
    AGENTS_LOG,
    LISTENER_CONTROL_MODULE,
    LISTENER_STATUS_TIMEOUT,
    TRAIN_TARGET,
    BackgroundJob,
    CommandResult,
    FunctionTask,
    Runner,
    SubprocessRunner,
    Task,
    launch_detached,
    listener_status_argv,
    module_available,
    spawn_function,
    train_argv,
)
from autobot.tui.state import (
    BADGE_ROLES,
    State,
    candidate_exists,
    listener_state,
    parse_listener_status,
)
from autobot.tui.themes import PAIRS_NEEDED, PALETTES, next_theme
from autobot.tui.ui import Canvas, Glyphs, Rect, Theme, fit, wrap
from autobot.wakeword.data import PhraseTable
from autobot.wakeword.dataset import VOICE_DIR, VoiceDataset
from autobot.wakeword.script import Prompt, build_script

if TYPE_CHECKING:
    from autobot.agents.gemini import Model
    from autobot.agents.weather_api import Lookup
    from autobot.wakeword.evaluate import KindScores, RealScores

# Set these to try the interface against other directories (module docstring).
VOICE_DIR_ENV_VAR = "AUTOBOT_VOICE_DIR"
FIXTURE_DIR_ENV_VAR = "AUTOBOT_FIXTURE_DIR"
MODELS_ROOT_ENV_VAR = "AUTOBOT_MODELS_ROOT"

MIN_HEIGHT = 22
MIN_WIDTH = 72
# How long one frame waits for a key, in milliseconds.
FRAME_MS = 100

KEY_ESC = 27
# Tab switches between the main menu and the face (AG-8).
KEY_TAB = 9
KEYS_ENTER = (curses.KEY_ENTER, 10, 13)
KEYS_BACKSPACE = (curses.KEY_BACKSPACE, 127, 8)
KEY_QUIT = curses.KEY_F10
KEY_HELP = curses.KEY_F1
KEYS_THEME = (ord("t"), ord("T"))

# How often the main menu asks whether the listener runs, in seconds.
LISTENER_POLL_SECONDS = 10.0

SPINNER = "|/-\\"
# What a masked field draws for each character of a secret.
SECRET_MASK = "*"

# F1 shows the open screen's own keys (`View.help_lines`), then these: the
# menu keys on a screen that is a menu, and the keys that work everywhere.
MENU_HELP_LINES = (
    "Up, Down       move the selection (also k and j)",
    "Enter          choose the selected entry",
    "1 to 9, 0      choose an entry by its number (0 is the tenth)",
)
HELP_LINES = (
    "Esc            go back one screen, or cancel a dialog",
    "F10            quit (also q on the main menu)",
    "T              the next colour theme: light, dark, sun",
    "Tab            switch between the face and the main menu",
    "F1             this help",
    "",
    "Every change is written to the config file at once.",
)


def score_real(model: Path, dataset: VoiceDataset, phrase: str) -> RealScores:
    """Score a model on the dataset's held-out recordings: the real `Services.score_real`.

    The evaluation is imported when it is first asked for, not when the
    interface starts: it loads the ONNX runtime.
    """
    from autobot.wakeword.evaluate import score_real as score

    return score(model, dataset, phrase)


def score_kind(kind: str, config: Config, dataset: VoiceDataset) -> KindScores:
    """Score a detector kind on the held-out recordings: the real `Services.score_kind`.

    Imported when first asked for, like `score_real`: it builds the listener's
    frame path with that kind's engine.
    """
    from autobot.wakeword.evaluate import score_kind as score

    return score(kind, config, dataset)


@dataclass
class Services:
    """Everything the interface reaches outside itself."""

    store: ConfigStore
    audio: AudioBackend
    pipewire: PipeWire
    runner: Runner
    train_job: BackgroundJob
    train_argv: list[str]
    hostname: str
    repo: Path
    # The committed fixtures: written only by the deliberate export of a clip.
    fixture_dir: Path
    module_available: Callable[[str], bool] = module_available
    # The voice dataset every recording goes into: `repo / VOICE_DIR` unless given.
    dataset: VoiceDataset = field(default=None)  # type: ignore[assignment]
    # The recording session's script for a phrase; tests give a short one.
    build_script: Callable[[str, PhraseTable], list[Prompt]] = build_script
    # Seconds that only ever go forward: paces the listener status poll.
    clock: Callable[[], float] = time.monotonic
    # The listener's event stream, read by the live monitor:
    # `repo / run/listener.sock` unless given. Tests give a fake fed from a list.
    events: EventSource = field(default=None)  # type: ignore[assignment]
    # Scores a model file on the dataset's held-out recordings for a phrase
    # (the Candidate screen); tests give one that needs no model.
    score_real: Callable[[Path, VoiceDataset, str], RealScores] = score_real
    # Scores a detector kind on the same held-out recordings, for the first
    # wake phrase (the Candidate screen's baseline column); tests give a fake.
    score_kind: Callable[[str, Config, VoiceDataset], KindScores] = score_kind
    # Starts a slow function without blocking the interface; tests run it inline.
    spawn: Callable[[Callable[[], object]], FunctionTask] = spawn_function
    # The directory the model files are looked for under: `repo` unless given.
    models_root: Path = field(default=None)  # type: ignore[assignment]
    # autobot's voice: renders (text, piper voice, models root) into a clip,
    # locally; tests give a fake that loads no model.
    voice: Callable[[str, str, Path], Clip] = synthesise
    # Connects to Google Cloud as the service account, or raises `GcpError`
    # (the Google Cloud screen); tests give a fake Google Cloud.
    gcp_connect: Callable[[Config, Path], GoogleCloud] = gcp_connect
    # Asks git whether it ignores the key file's path; tests give a fake.
    git_check_ignore: GitCheckIgnore = git_check_ignore

    # Starts a local command detached, in a folder, and answers its one-line
    # error, empty when it started (the camera agent); tests give a fake.
    launch: Callable[[Sequence[str], Path], str] = field(default=None)  # type: ignore[assignment]
    # The date and time on this machine (the time agent); tests give a fixed one.
    wall_clock: Callable[[], datetime] = datetime.now
    # The weather lookup (the weather agent): Google's Weather API unless
    # given; tests give a fake that looks nothing up.
    weather_lookup: Lookup = field(default=None)  # type: ignore[assignment]
    # Makes the language model behind the question agent for a configuration:
    # Gemini through the ADK unless given; tests give a fake model.
    model: Callable[[Config, Path], Model] = field(default=None)  # type: ignore[assignment]

    def seams(self) -> Seams:
        """What the agents reach the world through, for the face (`face.Seams`)."""
        return Seams(
            wall_clock=self.wall_clock,
            launch=self.launch,
            connect=self.gcp_connect,
            weather=self.weather_lookup,
            model=self.model,
        )

    def __post_init__(self) -> None:
        if self.launch is None:
            log = self.repo / AGENTS_LOG
            self.launch = lambda argv, cwd: launch_detached(argv, cwd, log)
        if self.weather_lookup is None:
            from autobot.agents.weather_api import GoogleWeather

            self.weather_lookup = GoogleWeather(
                self.models_root if self.models_root is not None else self.repo
            )
        if self.model is None:
            from autobot.agents.gemini import AdkModel

            self.model = AdkModel
        if self.dataset is None:
            self.dataset = VoiceDataset(self.repo / VOICE_DIR)
        if self.events is None:
            self.events = EventStream(self.repo / SOCKET_PATH)
        if self.models_root is None:
            self.models_root = self.repo


def default_services() -> Services:
    repo = repo_root()
    run_dir = repo / "run"
    voice_dir = os.environ.get(VOICE_DIR_ENV_VAR)
    fixture_dir = os.environ.get(FIXTURE_DIR_ENV_VAR)
    models_root = os.environ.get(MODELS_ROOT_ENV_VAR)
    return Services(
        store=ConfigStore(config_path()),
        audio=SoundDeviceBackend(),
        pipewire=PipeWire(),
        runner=SubprocessRunner(),
        train_job=BackgroundJob(
            run_dir / "train-wakeword.log", run_dir / "train-wakeword.pid", TRAIN_TARGET
        ),
        train_argv=train_argv(),
        hostname=socket.gethostname(),
        repo=repo,
        fixture_dir=(
            Path(fixture_dir).expanduser() if fixture_dir else repo / FIXTURE_DIR
        ),
        dataset=VoiceDataset(
            Path(voice_dir).expanduser() if voice_dir else repo / VOICE_DIR
        ),
        models_root=Path(models_root).expanduser() if models_root else repo,
    )


class View:
    """One screen. Subclasses draw inside the frame and handle keys."""

    title = ""
    # True while a text field of the view has the keyboard: `T` types then.
    typing = False
    # The screen's own keys, for F1: `"keys           what they do"` lines, the
    # description from column 15, at most 60 characters each.
    help_lines: Sequence[str] = ()
    # Whether the menu keys (Up, Down, Enter, the numbers) work on the screen.
    menu_keys = False
    # Whether the view is drawn inside the double-line frame. A view that
    # shows text to be selected with the mouse sets this False: it is then
    # given the full width, under the title, with no border beside its rows.
    framed = True

    def __init__(self, app: App) -> None:
        self.app = app

    def enter(self) -> None:
        """Called when the view opens."""

    def leave(self) -> None:
        """Called when the view closes; release devices here."""

    def tick(self) -> None:
        """Called every frame while the view is on top."""

    def frame_wait(self) -> int:
        """How long the next frame waits for a key, in milliseconds.

        A view that animates returns less than `FRAME_MS`, to have its next
        frame drawn when it is due.
        """
        return FRAME_MS

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        """Draw inside `rect`, the inside of the frame."""

    def key(self, key: int) -> bool:
        """Handle a key. False lets the application handle it."""
        return False

    def hint(self) -> str:
        """The line shown in the message bar when there is no message."""
        return ""

    def key_bar(self) -> Sequence[tuple[str, str]]:
        return (("F1", "Help"), ("Esc", "Back"), ("F10", "Quit"))


class MenuView(View):
    """A view made of facts at the top, a menu, and a free panel below."""

    # The title set into the frame around the facts.
    facts_title = ""
    # The rows `draw_panel` needs. The facts lose their frame, which costs a
    # row, before the panel loses one in a terminal of the minimum size.
    panel_rows = 0
    menu_keys = True

    def __init__(self, app: App) -> None:
        super().__init__(app)
        # Filled by `refresh_menu` on every draw and key: labels follow state.
        self.menu = Menu([])

    def items(self) -> list[MenuItem]:
        return []

    def facts(self) -> list[tuple[str, str, str]]:
        """Lines of (label, value, role); role is a `Theme` field name."""
        return []

    def draw_panel(self, canvas: Canvas, rect: Rect) -> None:
        """Draw below the menu."""

    def refresh_menu(self) -> None:
        self.menu.replace(self.items())

    def draw_facts(self, canvas: Canvas, rect: Rect, rows_after: int) -> int:
        """Draw the facts at the top of `rect`; return the rows they took.

        They sit in a framed panel when the terminal has the row to spare for
        it (`rows_after` is what the rest of the view needs), and above a rule
        when it has not.
        """
        theme = canvas.theme
        facts = self.facts()
        if not facts:
            return 0
        label_width = max(len(label) for label, _, _ in facts)
        framed = rect.height >= len(facts) + 2 + rows_after
        if framed:
            inside = canvas.panel(
                Rect(rect.y, rect.x, len(facts) + 2, rect.width),
                self.facts_title,
                title_attr=theme.heading,
            )
        else:
            inside = Rect(rect.y, rect.x + 1, len(facts), rect.width - 2)
        for row, (label, value, role) in enumerate(facts):
            canvas.put_in(inside, row, 0, label.ljust(label_width), theme.normal)
            canvas.put_in(
                inside,
                row,
                label_width + 2,
                fit(value, inside.width - label_width - 2),
                getattr(theme, role),
            )
        if framed:
            return len(facts) + 2
        canvas.rule(rect, len(facts), theme.frame)
        return len(facts) + 1

    def draw_menu(self, canvas: Canvas, rect: Rect) -> int:
        """Draw the menu's entries from the top of `rect`; return the rows taken."""
        theme = canvas.theme
        for index, item in enumerate(self.menu.items):
            selected = index == self.menu.selected and item.enabled
            pointer = canvas.glyphs.pointer if selected else " "
            # The tenth entry answers to 0; later ones have no number.
            number = str((index + 1) % 10) if index < 10 else " "
            text = f" {pointer} {number}  {item.label} "
            if selected:
                attr = theme.selected
                text = text.ljust(min(rect.width - 2, 60))
            else:
                attr = theme.normal if item.enabled else theme.dim
            canvas.put_in(rect, index, 1, fit(text, rect.width - 2), attr)
        return len(self.menu.items)

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        self.refresh_menu()
        below = len(self.menu.items)
        if self.panel_rows:
            below += 1 + self.panel_rows
        row = self.draw_facts(canvas, rect, below)
        row += self.draw_menu(
            canvas, Rect(rect.y + row, rect.x, rect.height - row, rect.width)
        )
        if row < rect.height - 1:
            canvas.rule(rect, row, canvas.theme.frame)
            self.draw_panel(
                canvas,
                Rect(rect.y + row + 1, rect.x, rect.height - row - 1, rect.width),
            )

    def key(self, key: int) -> bool:
        self.refresh_menu()
        if key in (curses.KEY_UP, ord("k")):
            self.menu.move(-1)
        elif key in (curses.KEY_DOWN, ord("j")):
            self.menu.move(1)
        elif key == curses.KEY_HOME:
            self.menu.first()
        elif key == curses.KEY_END:
            self.menu.last()
        elif key in KEYS_ENTER:
            self.menu.activate()
        elif ord("1") <= key <= ord("9"):
            self.menu.activate_number(key - ord("0"))
        elif key == ord("0"):
            self.menu.activate_number(10)
        else:
            return False
        return True

    def hint(self) -> str:
        item = self.menu.current
        return item.hint if item else ""

    def key_bar(self) -> Sequence[tuple[str, str]]:
        return (
            ("F1", "Help"),
            (self.app.canvas.glyphs.up_down, "Move"),
            ("Enter", "Select"),
            ("Esc", "Back"),
            ("F10", "Quit"),
        )


class TextView(View):
    """A screen that only says something."""

    def __init__(self, app: App, title: str, lines: Sequence[str]) -> None:
        super().__init__(app)
        self.title = title
        self.lines = list(lines)

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        for row, line in enumerate(self.lines):
            canvas.put_in(rect, row + 1, 2, fit(line, rect.width - 4))

    def key(self, key: int) -> bool:
        if key in KEYS_ENTER:
            self.app.pop()
            return True
        return False


class App:
    def __init__(
        self,
        screen: Any,
        theme: Theme,
        glyphs: Glyphs,
        services: Services,
        themes: Callable[[str], Theme] | None = None,
    ) -> None:
        """`themes` builds the colour theme of a name; None on a terminal without colours."""
        self.screen = screen
        self.canvas = Canvas(screen, theme, glyphs)
        self.services = services
        self.config: Config = services.store.load()
        self.themes = themes
        self.views: list[View] = []
        self.status = ""
        self.status_role = "normal"
        # True while a recording session is open: the state reads *recording*.
        self.session_open = False
        # What `control status` last said; None before the first answer.
        self.listener_running: bool | None = None
        # Counts every view opened or closed, so a view can tell it was covered.
        self.changes = 0
        self._status_task: Task | None = None
        self._status_at: float | None = None
        self._typing = 0
        self._frames = 0

    # --- state --------------------------------------------------------------

    def push(self, view: View) -> None:
        # A screen opens on the config file as it is now: it may have been
        # edited by hand while the interface was open.
        if self.views:
            self.reload()
        self.changes += 1
        self.views.append(view)
        view.enter()

    def pop(self) -> None:
        if self.views:
            self.changes += 1
            self.views.pop().leave()

    def say(self, text: str, role: str = "normal") -> None:
        """Show one line in the message bar until the next key."""
        self.status = " ".join(text.split())
        self.status_role = role

    def error(self, text: str) -> None:
        self.say(text, "error")

    def save(self, *changes: Change) -> bool:
        """Write `changes` to the config file. On failure, say why and change nothing."""
        try:
            self.config = self.services.store.update(*changes)
        except (ConfigError, ConfigEditError) as error:
            self.error(f"Not saved: {error}")
            return False
        self.say("Saved: " + ", ".join(change.describe() for change in changes), "ok")
        return True

    def reload(self) -> None:
        """Re-read the config file, in case it was edited outside the interface."""
        try:
            self.config = self.services.store.load()
        except ConfigError as error:
            # The file's name, not its path: the line has to say what is wrong.
            path = self.services.store.path
            self.error(f"Not read again: {str(error).replace(str(path), path.name)}")

    def require_nuc(self, what: str) -> bool:
        """Whether this machine is the configured NUC; says the one line when not.

        The recording session, the live monitor and the install step call this
        before doing anything (TS-11); the wording is the listener's.
        """
        hostname = self.services.hostname
        nuc = self.config.host.nuc_hostname
        if nuc and nuc.lower() == hostname.lower():
            return True
        self.error(f"{what} runs on the NUC only; this host is {hostname}")
        return False

    # --- the listener's state -------------------------------------------------

    def state(self) -> State:
        """Where the wake-word listener stands (`autobot.tui.state`)."""
        root = self.services.models_root
        return listener_state(
            self.config,
            root,
            candidate_exists(root),
            self.session_open,
            # Not known yet is not "down".
            self.listener_running is not False,
        )

    def poll_listener(self, *, now: bool = False) -> None:
        """Ask whether the listener runs, without blocking; the main menu calls this.

        Every frame it collects the answer of the question in flight, and it
        asks again when `now` is set or `LISTENER_POLL_SECONDS` have passed.
        """
        services = self.services
        if self._status_task is not None:
            result = self._status_task.poll()
            if result is None:
                return
            self._status_task = None
            self.listener_running = (
                parse_listener_status(result.output) if result.ok else None
            )
        moment = services.clock()
        due = (
            self._status_at is None or moment - self._status_at >= LISTENER_POLL_SECONDS
        )
        if not (now or due):
            return
        self._status_at = moment
        if not services.module_available(LISTENER_CONTROL_MODULE):
            self.listener_running = None
            return
        self._status_task = services.runner.start(
            listener_status_argv(), cwd=services.repo, timeout=LISTENER_STATUS_TIMEOUT
        )

    # --- themes ---------------------------------------------------------------

    def apply_theme(self, theme: Theme) -> None:
        self.canvas.theme = theme
        # The page takes the theme's colours. Not its bold: curses adds the
        # background's attributes to every character drawn on it.
        self.screen.bkgd(" ", theme.normal & ~curses.A_BOLD)

    def cycle_theme(self) -> None:
        """Switch to the next colour theme at once and write it to the config file."""
        if self.themes is None:
            self.say("this terminal has no colours")
            return
        name = next_theme(self.config.tui.theme)
        if not self.save(Change("tui", "theme", name)):
            return
        self.apply_theme(self.themes(name))
        self.say(f"Theme: {name}. T switches to the next; saved as [tui] theme.", "ok")

    def typing(self) -> bool:
        """Whether a text field has the keyboard, so that `T` is a letter."""
        return self._typing > 0 or bool(self.views and self.views[-1].typing)

    # --- the loop -----------------------------------------------------------

    def run(self) -> int:
        from autobot.tui.screens.main import MainMenu

        self.push(MainMenu(self))
        try:
            while self.views:
                key = self.frame()
                if key != -1:
                    self.dispatch(key)
        finally:
            while self.views:
                self.pop()
        return 0

    def dispatch(self, key: int) -> None:
        self.status = ""
        view = self.views[-1]
        if key == KEY_QUIT:
            self.quit()
        elif key == KEY_HELP:
            self.message("Help", self.help_lines())
        elif key == KEY_TAB and not self.typing():
            self.switch_mode()
        elif view.key(key):
            pass
        elif key == KEY_ESC or key == ord("q"):
            if len(self.views) > 1:
                self.pop()
            else:
                self.quit()

    def switch_mode(self) -> None:
        """Tab: from the main menu to the face, and from the face back (AG-8).

        The main menu is configuration mode and the face is run mode. On any
        other screen Tab does nothing: Esc leads back to the menu first.
        """
        from autobot.tui.screens.face import FaceScreen
        from autobot.tui.screens.main import MainMenu

        view = self.views[-1]
        if isinstance(view, FaceScreen):
            self.pop()
        elif isinstance(view, MainMenu):
            self.push(FaceScreen(self))

    def help_lines(self) -> list[str]:
        """What F1 shows: the top screen's own keys, then the general ones."""
        view = self.views[-1]
        lines = list(view.help_lines)
        if view.menu_keys:
            lines += MENU_HELP_LINES
        return [*lines, *HELP_LINES]

    def quit(self) -> None:
        """Close every view, after asking what to do with a running training."""
        job = self.services.train_job
        if job.running():
            choice = self.pick(
                "Training is still running",
                (
                    "Leave it running in the background and quit",
                    "Stop the training and quit",
                    "Do not quit",
                ),
            )
            if choice is None or choice == 2:
                return
            if choice == 1:
                job.cancel()
        while self.views:
            self.pop()

    def too_small(self) -> bool:
        height, width = self.canvas.size()
        return height < MIN_HEIGHT or width < MIN_WIDTH

    def frame(self, overlay: Callable[[Canvas], None] | None = None) -> int:
        """Draw one frame and wait up to `FRAME_MS` for a key; -1 when none came."""
        self._frames += 1
        if self.views:
            self.views[-1].tick()
        self.draw(overlay)
        wait = self.views[-1].frame_wait() if self.views else FRAME_MS
        self.screen.timeout(max(1, min(FRAME_MS, wait)))
        try:
            key = int(self.screen.getch())
        except curses.error:
            return -1
        if key == curses.KEY_RESIZE:
            return -1
        if key != -1 and self.too_small() and key != KEY_QUIT:
            return -1
        if key in KEYS_THEME and not self.typing():
            self.cycle_theme()
            return -1
        return key

    def draw(self, overlay: Callable[[Canvas], None] | None = None) -> None:
        canvas = self.canvas
        theme = canvas.theme
        height, width = canvas.size()
        canvas.erase()
        if self.too_small():
            canvas.put(0, 0, fit("Terminal too small", width), theme.heading)
            canvas.put(
                1,
                0,
                fit(f"Need {MIN_WIDTH}x{MIN_HEIGHT}, have {width}x{height}.", width),
            )
            canvas.put(2, 0, fit("Enlarge the window, or F10 to quit.", width))
            canvas.refresh()
            return

        title = f" autobot {__version__}"
        right = f"{self.services.hostname} "
        canvas.put(0, 0, " " * width, theme.title)
        canvas.put(0, 0, title, theme.title)
        canvas.put(0, width - len(right), right, theme.title)
        # The listener's state is on every screen, not only the main menu.
        state = self.state()
        canvas.put(
            0,
            len(title) + 2,
            f" {state.value.upper()} ",
            getattr(theme, BADGE_ROLES[state]),
        )

        view = self.views[-1] if self.views else None
        frame = Rect(1, 0, height - 3, width)
        if view is None or view.framed:
            canvas.box(frame, view.title if view else "", theme.frame)
            if view:
                view.draw(canvas, frame.inset(1, 1))
        else:
            # The top edge and its title only: nothing stands beside a row.
            canvas.put(frame.y, 0, canvas.glyphs.horizontal * width, theme.frame)
            canvas.put(frame.y, 2, f" {view.title[: width - 6]} ", theme.frame)
            view.draw(canvas, Rect(frame.y + 1, 0, frame.height - 2, width))

        message = self.status or (view.hint() if view else "")
        role = self.status_role if self.status else "normal"
        if role == "error":
            canvas.put(height - 2, 0, " " * width, theme.error)
        canvas.put(height - 2, 1, fit(message, width - 2), getattr(theme, role))

        canvas.put(height - 1, 0, " " * width, theme.key_label)
        column = 0
        # `T Theme` works everywhere, so it is drawn here, at the right, when
        # the view's own keys leave room for it.
        keys = list(view.key_bar()) if view else []
        used = sum(len(name) + len(label) + 4 for name, label in keys)
        theme_key = ("T", "Theme")
        if used + len(theme_key[0]) + len(theme_key[1]) + 3 <= width:
            keys.append(theme_key)
        for name, label in keys:
            if (name, label) == theme_key:
                column = max(column, width - len(name) - len(label) - 3)
            canvas.put(height - 1, column, f" {name} ", theme.key)
            column += len(name) + 2
            canvas.put(height - 1, column, f"{label} ", theme.key_label)
            column += len(label) + 2

        if overlay:
            overlay(canvas)
        canvas.refresh()

    # --- dialogs ------------------------------------------------------------

    def _dialog(self, canvas: Canvas, title: str, height: int, width: int) -> Rect:
        """Draw an empty dialog box in the middle of the screen; return its inside."""
        screen_height, screen_width = canvas.size()
        width = min(width + 4, screen_width - 4)
        height = min(height + 2, screen_height - 4)
        rect = Rect(
            (screen_height - height) // 2, (screen_width - width) // 2, height, width
        )
        theme = canvas.theme
        for row in range(rect.y + 1, rect.bottom + 1):
            canvas.put(row, rect.right, "  ", theme.shadow)
        canvas.put(rect.bottom, rect.x + 2, " " * rect.width, theme.shadow)
        canvas.fill(rect, theme.dialog)
        canvas.box(rect, title, theme.dialog)
        return rect.inset(1, 2)

    def message(self, title: str, lines: Sequence[str]) -> None:
        """Show `lines` until Enter or Esc."""
        footer = "Enter: OK"
        width = max([len(title) + 4, len(footer), *(len(line) for line in lines)])

        def overlay(canvas: Canvas) -> None:
            inside = self._dialog(canvas, title, len(lines) + 2, width)
            for row, line in enumerate(lines):
                canvas.put_in(
                    inside, row, 0, fit(line, inside.width), canvas.theme.dialog
                )
            canvas.put_in(
                inside, inside.height - 1, 0, footer, canvas.theme.dialog_selected
            )

        while True:
            key = self.frame(overlay)
            if key in (*KEYS_ENTER, KEY_ESC, KEY_QUIT, ord(" ")):
                return

    def confirm(
        self, title: str, lines: Sequence[str], *, default: bool = False
    ) -> bool:
        """Ask yes or no. Esc is no."""
        answer = default
        width = max([len(title) + 4, 20, *(len(line) for line in lines)])

        def overlay(canvas: Canvas) -> None:
            theme = canvas.theme
            inside = self._dialog(canvas, title, len(lines) + 2, width)
            for row, line in enumerate(lines):
                canvas.put_in(inside, row, 0, fit(line, inside.width), theme.dialog)
            row = inside.height - 1
            canvas.put_in(
                inside,
                row,
                0,
                "  Yes  ",
                theme.dialog_selected if answer else theme.dialog,
            )
            canvas.put_in(
                inside,
                row,
                9,
                "  No  ",
                theme.dialog if answer else theme.dialog_selected,
            )

        while True:
            key = self.frame(overlay)
            if key in (ord("y"), ord("Y")):
                return True
            if key in (ord("n"), ord("N"), KEY_ESC, KEY_QUIT):
                return False
            if key in KEYS_ENTER:
                return answer
            if key in (curses.KEY_LEFT, curses.KEY_RIGHT, ord("\t")):
                answer = not answer

    def pick(
        self,
        title: str,
        options: Sequence[str],
        selected: int = 0,
        note: str = "",
    ) -> int | None:
        """Choose one of `options`; None when cancelled or there are none."""
        if not options:
            self.error(f"{title}: nothing to choose from")
            return None
        menu = Menu([MenuItem(option, lambda: None) for option in options], selected)
        width = max(
            [len(title) + 4, len(note), *(len(option) + 4 for option in options)]
        )
        extra = 2 if note else 0

        def overlay(canvas: Canvas) -> None:
            theme = canvas.theme
            inside = self._dialog(canvas, title, len(options) + extra, width)
            visible = max(1, inside.height - extra)
            top = min(
                max(0, menu.selected - visible + 1), max(0, len(options) - visible)
            )
            for row in range(min(visible, len(options))):
                index = top + row
                chosen = index == menu.selected
                pointer = canvas.glyphs.pointer if chosen else " "
                text = fit(f"{pointer} {options[index]}", inside.width).ljust(
                    inside.width
                )
                canvas.put_in(
                    inside,
                    row,
                    0,
                    text,
                    theme.dialog_selected if chosen else theme.dialog,
                )
            if note:
                canvas.put_in(
                    inside, inside.height - 1, 0, fit(note, inside.width), theme.dialog
                )

        while True:
            key = self.frame(overlay)
            if key in (curses.KEY_UP, ord("k")):
                menu.move(-1)
            elif key in (curses.KEY_DOWN, ord("j")):
                menu.move(1)
            elif key == curses.KEY_HOME:
                menu.first()
            elif key == curses.KEY_END:
                menu.last()
            elif key in KEYS_ENTER:
                return menu.selected
            elif key in (KEY_ESC, KEY_QUIT):
                return None

    def prompt(
        self,
        title: str,
        label: str,
        initial: str = "",
        validate: Callable[[str], str | None] | None = None,
    ) -> str | None:
        """Ask for one line of text; None when cancelled.

        `validate` returns a one-line complaint, or None to accept the text.
        """
        text = initial
        complaint = ""
        width = max(len(title) + 4, len(label), 48)

        def overlay(canvas: Canvas) -> None:
            theme = canvas.theme
            inside = self._dialog(canvas, title, 5, width)
            canvas.put_in(inside, 0, 0, fit(label, inside.width), theme.dialog)
            shown = text[-(inside.width - 1) :] if inside.width > 1 else ""
            canvas.put_in(inside, 1, 0, " " * inside.width, theme.field)
            canvas.put_in(inside, 1, 0, shown, theme.field)
            canvas.put_in(inside, 1, len(shown), "_", theme.field | curses.A_BLINK)
            canvas.put_in(
                inside, 2, 0, fit(complaint, inside.width), theme.dialog_error
            )
            canvas.put_in(
                inside, 4, 0, fit("Enter: OK   Esc: Cancel", inside.width), theme.dialog
            )

        # The text field has the keyboard: `T` types a letter here.
        self._typing += 1
        try:
            while True:
                key = self.frame(overlay)
                if key in (KEY_ESC, KEY_QUIT):
                    return None
                if key in KEYS_ENTER:
                    complaint = (validate(text) if validate else None) or ""
                    if not complaint:
                        return text
                elif key in KEYS_BACKSPACE:
                    text = text[:-1]
                    complaint = ""
                elif key == curses.ascii.NAK:  # Ctrl-U
                    text = ""
                    complaint = ""
                elif 32 <= key < 127:
                    text += chr(key)
                    complaint = ""
        finally:
            self._typing -= 1

    def prompt_secret(self, title: str, label: str) -> bytearray | None:
        """Ask for a secret in a masked field; None when cancelled.

        Every character typed or pasted is drawn as one mask character and
        kept in a `bytearray`, which the caller clears after use (a `str`
        cannot be cleared). The value is never drawn. Escape clears the
        buffer before returning. A paste arrives as keystrokes, so it is
        masked in the same way and its closing newline is the Enter.
        """
        value = bytearray()
        width = max(len(title) + 4, len(label), 48)

        def overlay(canvas: Canvas) -> None:
            theme = canvas.theme
            inside = self._dialog(canvas, title, 5, width)
            canvas.put_in(inside, 0, 0, fit(label, inside.width), theme.dialog)
            shown = SECRET_MASK * min(len(value), max(0, inside.width - 1))
            canvas.put_in(inside, 1, 0, " " * inside.width, theme.field)
            canvas.put_in(inside, 1, 0, shown, theme.field)
            canvas.put_in(inside, 1, len(shown), "_", theme.field | curses.A_BLINK)
            canvas.put_in(
                inside,
                2,
                0,
                fit(f"{len(value)} characters, not shown", inside.width),
                theme.dialog,
            )
            canvas.put_in(
                inside,
                4,
                0,
                fit("Enter: Store   Esc: Cancel", inside.width),
                theme.dialog,
            )

        # The field has the keyboard: `T` types a letter here.
        self._typing += 1
        try:
            while True:
                key = self.frame(overlay)
                if key in (KEY_ESC, KEY_QUIT):
                    clear(value)
                    return None
                if key in KEYS_ENTER:
                    # A secret is one line. What a longer paste still has
                    # waiting is dropped, not taken as keys by the screen.
                    try:
                        curses.flushinp()
                    except curses.error:
                        pass
                    return value
                if key in KEYS_BACKSPACE:
                    if value:
                        value[-1] = 0
                        del value[-1]
                elif key == curses.ascii.NAK:  # Ctrl-U
                    clear(value)
                    del value[:]
                elif 32 <= key < 256 and key != 127:
                    # Bytes above 127 are a pasted character's UTF-8 bytes.
                    value.append(key)
        except BaseException:
            clear(value)
            raise
        finally:
            self._typing -= 1

    def wait(
        self, title: str, task: Task, note: Callable[[], str] | None = None
    ) -> CommandResult:
        """Show a busy box until `task` ends. Esc asks it to stop.

        `note` gives a line to show under the spinner, asked every frame: how
        long a slow task has been running, for one.
        """
        cancelling = False

        def overlay(canvas: Canvas) -> None:
            inside = self._dialog(canvas, title, 3, max(44, len(title) + 4))
            spinner = SPINNER[self._frames % len(SPINNER)]
            text = "Stopping..." if cancelling else f"Working... {spinner}"
            canvas.put_in(inside, 0, 0, text, canvas.theme.dialog)
            if note is not None:
                canvas.put_in(
                    inside, 1, 0, fit(note(), inside.width), canvas.theme.dialog
                )
            canvas.put_in(inside, 2, 0, "Esc: Cancel", canvas.theme.dialog)

        while True:
            result = task.poll()
            if result is not None:
                return result
            key = self.frame(overlay)
            if key in (KEY_ESC, KEY_QUIT) and not cancelling:
                cancelling = True
                task.cancel()

    def report_failure(self, title: str, result: CommandResult) -> None:
        """Say that a command failed, and show the end of what it printed."""
        width = max(20, self.canvas.size()[1] - 12)
        lines = wrap([line for line in result.lines if line.strip()], width)
        self.message(title, lines[-10:] or [result.summary()])
        self.error(f"{title}: {result.summary()}")

    def run_command(
        self, title: str, argv: Sequence[str], timeout: float = 60.0
    ) -> CommandResult:
        """Run a short command from the repository root behind a busy box."""
        task = self.services.runner.start(argv, cwd=self.services.repo, timeout=timeout)
        return self.wait(title, task)


# --- starting a real terminal -------------------------------------------------


@contextmanager
def quiet_stderr() -> Iterator[None]:
    """Send file descriptor 2 to /dev/null for the duration.

    ALSA and PortAudio print diagnostics straight to stderr from C, which
    would scribble over the curses screen.
    """
    sys.stderr.flush()
    saved = os.dup(2)
    null = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(null, 2)
        yield
    finally:
        sys.stderr.flush()
        os.dup2(saved, 2)
        os.close(saved)
        os.close(null)


def pick_glyphs() -> Glyphs:
    """Line-drawing characters when the terminal's locale is UTF-8, else ASCII."""
    # The C library's answer, which is what curses uses to encode output;
    # `locale.getpreferredencoding` says UTF-8 in the C locale (UTF-8 mode).
    try:
        encoding = locale.nl_langinfo(locale.CODESET)
    except (AttributeError, ValueError):
        encoding = ""
    return (
        Glyphs.unicode()
        if encoding.lower().replace("-", "") == "utf8"
        else Glyphs.ascii()
    )


def colours_available() -> bool:
    """Whether the terminal can draw the colour themes. Starts curses' colours."""
    if os.environ.get("NO_COLOR"):
        return False
    try:
        if curses.has_colors():
            curses.start_color()
            return curses.COLORS >= 8 and curses.COLOR_PAIRS > PAIRS_NEEDED
    except curses.error:
        pass
    return False


def colour_theme(name: str) -> Theme:
    """The colour theme of a name; an unknown name gives the first theme."""
    palette = PALETTES.get(name) or next(iter(PALETTES.values()))
    return Theme.from_palette(palette)


def _curses_main(screen: Any, services: Services) -> int:
    themes = colour_theme if colours_available() else None
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    screen.keypad(True)
    screen.timeout(FRAME_MS)
    app = App(screen, Theme.mono(), pick_glyphs(), services, themes)
    # Open in the theme the config file names (TS-13); mono without colours.
    app.apply_theme(themes(app.config.tui.theme) if themes else Theme.mono())
    return app.run()


def _leave_on_signal(signum: int, frame: object) -> None:
    raise SystemExit(128 + signum)


def main() -> int:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print(
            "the terminal user interface needs a terminal: run 'make launch' in one",
            file=sys.stderr,
        )
        return 1
    try:
        services = default_services()
        services.store.load()
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 1
    locale.setlocale(locale.LC_ALL, "")
    # Without this curses waits a full second after Esc to see whether it
    # starts an escape sequence.
    os.environ.setdefault("ESCDELAY", "25")
    # A closed terminal or a `kill` leaves as F10 does: every view is closed
    # on the way out, so a take being recorded is stored and the input stream
    # released.
    for signum in (signal.SIGHUP, signal.SIGTERM):
        signal.signal(signum, _leave_on_signal)
    try:
        with quiet_stderr():
            return curses.wrapper(_curses_main, services)
    except KeyboardInterrupt:
        return 130
    except curses.error as error:
        print(f"cannot start the terminal user interface: {error}", file=sys.stderr)
        return 1
