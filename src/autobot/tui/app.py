"""The terminal user interface's application loop, its views and its dialogs.

`App` owns a stack of views (the main menu, then whichever screen is open) and
draws one frame about ten times a second: title bar, the top view inside a
double-line frame, a message line and a key bar. Input never blocks for longer
than a frame, so the level meter and running commands keep updating.

Dialogs (`message`, `confirm`, `pick`, `prompt`, `wait`) are small nested
loops over the same frame function, which keeps screen code linear: ask, get
the answer, act.

`App` talks to the world only through `Services`, so tests build one with
fakes and drive the real application code through a scripted fake screen.
"""

from __future__ import annotations

import curses
import curses.ascii
import locale
import os
import socket
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autobot import __version__
from autobot.config import Config, ConfigError, config_path, repo_root
from autobot.tui.audio import FIXTURE_DIR, AudioBackend, SoundDeviceBackend
from autobot.tui.config_edit import Change, ConfigEditError, ConfigStore
from autobot.tui.menu import Menu, MenuItem
from autobot.tui.pipewire import PipeWire
from autobot.tui.runner import (
    TRAIN_TARGET,
    BackgroundJob,
    CommandResult,
    Runner,
    SubprocessRunner,
    Task,
    module_available,
    train_argv,
)
from autobot.tui.ui import Canvas, Glyphs, Rect, Theme, fit, wrap

MIN_HEIGHT = 22
MIN_WIDTH = 72
# How long one frame waits for a key, in milliseconds.
FRAME_MS = 100

KEY_ESC = 27
KEYS_ENTER = (curses.KEY_ENTER, 10, 13)
KEYS_BACKSPACE = (curses.KEY_BACKSPACE, 127, 8)
KEY_QUIT = curses.KEY_F10
KEY_HELP = curses.KEY_F1

SPINNER = "|/-\\"

HELP_LINES = (
    "Up, Down       move the selection (also k and j)",
    "Enter          choose the selected entry",
    "1 to 9         choose an entry by its number",
    "Esc            go back one screen, or cancel a dialog",
    "F10            quit (also q on the main menu)",
    "F1             this help",
    "",
    "Every change is written to the config file at once.",
    "The config file is the only place configuration lives.",
)


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
    fixture_dir: Path
    module_available: Callable[[str], bool] = module_available


def default_services() -> Services:
    repo = repo_root()
    run_dir = repo / "run"
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
        fixture_dir=repo / FIXTURE_DIR,
    )


class View:
    """One screen. Subclasses draw inside the frame and handle keys."""

    title = ""

    def __init__(self, app: App) -> None:
        self.app = app

    def enter(self) -> None:
        """Called when the view opens."""

    def leave(self) -> None:
        """Called when the view closes; release devices here."""

    def tick(self) -> None:
        """Called every frame while the view is on top."""

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

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        theme = canvas.theme
        self.refresh_menu()
        row = 0
        facts = self.facts()
        label_width = max((len(label) for label, _, _ in facts), default=0)
        for label, value, role in facts:
            canvas.put_in(rect, row, 1, label.ljust(label_width), theme.normal)
            canvas.put_in(
                rect,
                row,
                label_width + 3,
                fit(value, rect.width - label_width - 4),
                getattr(theme, role),
            )
            row += 1
        if facts:
            canvas.rule(rect, row, theme.frame)
            row += 1
        for index, item in enumerate(self.menu.items):
            selected = index == self.menu.selected and item.enabled
            pointer = canvas.glyphs.pointer if selected else " "
            text = f" {pointer} {index + 1}  {item.label} "
            if selected:
                attr = theme.selected
                text = text.ljust(min(rect.width - 2, 60))
            else:
                attr = theme.normal if item.enabled else theme.dim
            canvas.put_in(rect, row, 1, fit(text, rect.width - 2), attr)
            row += 1
        if row < rect.height - 1:
            canvas.rule(rect, row, theme.frame)
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
        self, screen: Any, theme: Theme, glyphs: Glyphs, services: Services
    ) -> None:
        self.screen = screen
        self.canvas = Canvas(screen, theme, glyphs)
        self.services = services
        self.config: Config = services.store.load()
        self.views: list[View] = []
        self.status = ""
        self.status_role = "normal"
        self._frames = 0

    # --- state --------------------------------------------------------------

    def push(self, view: View) -> None:
        self.views.append(view)
        view.enter()

    def pop(self) -> None:
        if self.views:
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
            self.error(str(error))

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
            self.message("Help", HELP_LINES)
        elif view.key(key):
            pass
        elif key == KEY_ESC or key == ord("q"):
            if len(self.views) > 1:
                self.pop()
            else:
                self.quit()

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
        try:
            key = int(self.screen.getch())
        except curses.error:
            return -1
        if key == curses.KEY_RESIZE:
            return -1
        if key != -1 and self.too_small() and key != KEY_QUIT:
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

        title = f" autobot Setup Utility {__version__}"
        right = f"{self.services.hostname} "
        canvas.put(0, 0, " " * width, theme.title)
        canvas.put(0, 0, title, theme.title)
        canvas.put(0, width - len(right), right, theme.title)

        view = self.views[-1] if self.views else None
        frame = Rect(1, 0, height - 3, width)
        canvas.box(frame, view.title if view else "", theme.frame)
        if view:
            view.draw(canvas, frame.inset(1, 1))

        message = self.status or (view.hint() if view else "")
        role = self.status_role if self.status else "normal"
        if role == "error":
            canvas.put(height - 2, 0, " " * width, theme.error)
        canvas.put(height - 2, 1, fit(message, width - 2), getattr(theme, role))

        canvas.put(height - 1, 0, " " * width, theme.key_label)
        column = 0
        for name, label in view.key_bar() if view else ():
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

    def wait(self, title: str, task: Task) -> CommandResult:
        """Show a busy box until `task` ends. Esc asks it to stop."""
        cancelling = False

        def overlay(canvas: Canvas) -> None:
            inside = self._dialog(canvas, title, 3, 44)
            spinner = SPINNER[self._frames % len(SPINNER)]
            text = "Stopping..." if cancelling else f"Working... {spinner}"
            canvas.put_in(inside, 0, 0, text, canvas.theme.dialog)
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


def pick_theme() -> Theme:
    """The colour theme when the terminal has colours, else the mono one."""
    if os.environ.get("NO_COLOR"):
        return Theme.mono()
    try:
        if curses.has_colors():
            curses.start_color()
            if curses.COLORS >= 8 and curses.COLOR_PAIRS > 10:
                return Theme.colour()
    except curses.error:
        pass
    return Theme.mono()


def _curses_main(screen: Any, services: Services) -> int:
    theme = pick_theme()
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    screen.keypad(True)
    screen.timeout(FRAME_MS)
    screen.bkgd(" ", theme.normal)
    return App(screen, theme, pick_glyphs(), services).run()


def main() -> int:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print(
            "the terminal user interface needs a terminal: run 'make deploy' in one",
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
    try:
        with quiet_stderr():
            return curses.wrapper(_curses_main, services)
    except KeyboardInterrupt:
        return 130
    except curses.error as error:
        print(f"cannot start the terminal user interface: {error}", file=sys.stderr)
        return 1
