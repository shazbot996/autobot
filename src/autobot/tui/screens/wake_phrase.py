"""The Wake phrase screen: threshold, kind, training, the acknowledgement.

Training (`make train-wakeword`) can run for hours, so it runs as a
`BackgroundJob`: the training pane shows its log as it grows, and leaving the
pane, or the whole interface, does not stop it.
"""

from __future__ import annotations

import curses
from pathlib import Path

from autobot.config import KIND_OPENWAKEWORD, WAKE_PHRASE_KINDS, WakePhraseConfig
from autobot.tui.app import KEYS_ENTER, MenuView, View
from autobot.tui.config_edit import Change
from autobot.tui.menu import MenuItem
from autobot.tui.runner import LISTENER_ACK_MODULE, listener_ack_argv
from autobot.tui.ui import Canvas, Rect, fit

DEFAULT_MODEL = "models/wakeword/autobot.onnx"
THRESHOLD_STEP = 0.05
TABLE = "wake_phrases"

KIND_NOTES = {
    "text_match": "text_match: the local transcript contains the phrase",
    "openwakeword": "openwakeword: a trained model file scores the audio",
}


def parse_threshold(text: str) -> float:
    """The threshold the owner typed. Raises `ValueError` with a one-line reason."""
    try:
        value = float(text.strip())
    except ValueError:
        raise ValueError("type a number, for example 0.5") from None
    if not 0.0 < value <= 1.0:
        raise ValueError("the threshold must be above 0 and at most 1")
    return round(value, 4)


def step_threshold(current: float, steps: int) -> float:
    """`current` moved by whole steps, kept inside the valid range."""
    moved = round(current + steps * THRESHOLD_STEP, 2)
    return min(1.0, max(THRESHOLD_STEP, moved))


class WakePhraseScreen(MenuView):
    title = "Wake phrase"

    def enter(self) -> None:
        self.index = 0

    @property
    def phrase(self) -> WakePhraseConfig:
        phrases = self.app.config.wake_phrases
        self.index = min(self.index, len(phrases) - 1)
        return phrases[self.index]

    def change(self, key: str, value: str | float) -> Change:
        return Change(TABLE, key, value, self.index)

    def model_missing(self) -> bool:
        """Whether openwakeword is chosen and its model file is not there."""
        phrase = self.phrase
        if phrase.kind != KIND_OPENWAKEWORD:
            return False
        # Relative to the repository root, as `WakePhraseConfig.model_path` is.
        path = self.app.services.repo / Path(phrase.model).expanduser()
        return not phrase.model or not path.is_file()

    def warn_if_model_missing(self) -> None:
        if self.model_missing():
            self.app.say(
                f"Saved, but the model file {self.phrase.model} does not exist yet: "
                "train it first, or the listener cannot start.",
                "warning",
            )

    # --- actions ------------------------------------------------------------

    def edit_threshold(self) -> None:
        def complaint(text: str) -> str | None:
            try:
                parse_threshold(text)
            except ValueError as error:
                return str(error)
            return None

        typed = self.app.prompt(
            "Threshold",
            "Score from 0 to 1 at which the detector fires:",
            f"{self.phrase.threshold:g}",
            complaint,
        )
        if typed is not None:
            self.app.save(self.change("threshold", parse_threshold(typed)))

    def nudge_threshold(self, steps: int) -> None:
        value = step_threshold(self.phrase.threshold, steps)
        if value != self.phrase.threshold:
            self.app.save(self.change("threshold", value))

    def choose_kind(self) -> None:
        kinds = list(WAKE_PHRASE_KINDS)
        picked = self.app.pick(
            "Kind of detector",
            [KIND_NOTES.get(kind, kind) for kind in kinds],
            kinds.index(self.phrase.kind) if self.phrase.kind in kinds else 0,
        )
        if picked is None:
            return
        kind = kinds[picked]
        changes = []
        if kind == KIND_OPENWAKEWORD and not self.phrase.model:
            # openwakeword is invalid without a model path; write both at once.
            changes.append(self.change("model", DEFAULT_MODEL))
        changes.append(self.change("kind", kind))
        if self.app.save(*changes):
            self.warn_if_model_missing()

    def edit_model(self) -> None:
        needs_model = self.phrase.kind == KIND_OPENWAKEWORD

        def complaint(text: str) -> str | None:
            if needs_model and not text.strip():
                return "openwakeword needs a model file path"
            return None

        typed = self.app.prompt(
            "Model file",
            "Path of the model file, relative to the repository root:",
            self.phrase.model or DEFAULT_MODEL,
            complaint,
        )
        if typed is not None and self.app.save(self.change("model", typed.strip())):
            self.warn_if_model_missing()

    def play_ack(self) -> None:
        app = self.app
        if not app.services.module_available(LISTENER_ACK_MODULE):
            app.error(
                f"The acknowledgement is not built yet ({LISTENER_ACK_MODULE} is missing)."
            )
            return
        result = app.run_command("Playing the acknowledgement", listener_ack_argv())
        if result.ok:
            app.say(f"Acknowledgement played ({app.config.ack.text!r}).", "ok")
        else:
            app.report_failure("Acknowledgement failed", result)

    def next_phrase(self) -> None:
        self.index = (self.index + 1) % len(self.app.config.wake_phrases)

    # --- drawing ------------------------------------------------------------

    def facts(self) -> list[tuple[str, str, str]]:
        phrase = self.phrase
        count = len(self.app.config.wake_phrases)
        if not phrase.model:
            model, role = "(none)", "dim"
        elif self.model_missing():
            model, role = f"{phrase.model}  (file does not exist)", "warning"
        else:
            model, role = phrase.model, "value"
        job = self.app.services.train_job
        return [
            ("Phrase", f"{phrase.phrase}  ({self.index + 1} of {count})", "value"),
            ("Kind", phrase.kind, "value"),
            ("Model", model, role),
            ("Threshold", f"{phrase.threshold:g}", "value"),
            ("Acknowledgement", f"{self.app.config.ack.text!r}", "value"),
            (
                "Training",
                f"running (pid {job.pid()})" if job.running() else "not running",
                "value",
            ),
        ]

    def items(self) -> list[MenuItem]:
        items = [
            MenuItem(
                "Threshold...",
                self.edit_threshold,
                "Type a new threshold; + and - move it by 0.05",
            ),
            MenuItem(
                "Kind...",
                self.choose_kind,
                "Choose the detector that implements the phrase",
            ),
            MenuItem(
                "Model file...", self.edit_model, "The model file openwakeword loads"
            ),
            MenuItem(
                "Train the wake-word model",
                lambda: self.app.push(TrainScreen(self.app)),
                "Runs `make train-wakeword` and shows its progress",
            ),
            MenuItem(
                "Play the acknowledgement",
                self.play_ack,
                "Speaks the acknowledgement once on the output device",
            ),
        ]
        if len(self.app.config.wake_phrases) > 1:
            items.append(
                MenuItem("Next wake phrase", self.next_phrase, "Show the next phrase")
            )
        return items

    def key(self, key: int) -> bool:
        if key in (ord("+"), ord("="), curses.KEY_RIGHT):
            self.nudge_threshold(1)
        elif key in (ord("-"), ord("_"), curses.KEY_LEFT):
            self.nudge_threshold(-1)
        else:
            return super().key(key)
        return True

    def key_bar(self) -> list[tuple[str, str]]:
        return [*super().key_bar()[:3], ("+/-", "Threshold"), *super().key_bar()[3:]]


class TrainScreen(View):
    """`make train-wakeword`, with its output scrolling as it arrives."""

    title = "Train the wake-word model"

    def enter(self) -> None:
        # Lines scrolled back from the end of the log; 0 follows the output.
        self.scroll = 0
        self.page = 10

    def start(self) -> None:
        app = self.app
        job = app.services.train_job
        if job.running():
            app.say("The training is already running.")
            return
        try:
            job.start(app.services.train_argv, cwd=app.services.repo)
        except RuntimeError as error:
            app.error(f"Cannot start the training: {error}")
            return
        self.scroll = 0
        app.say("Training started. Esc leaves it running.", "ok")

    def cancel(self) -> None:
        app = self.app
        job = app.services.train_job
        if not job.running():
            app.say("The training is not running.")
            return
        if app.confirm("Stop the training?", ["The training is running.", "Stop it?"]):
            job.cancel()
            app.say("Asked the training to stop.", "warning")

    def state(self) -> tuple[str, str]:
        job = self.app.services.train_job
        if job.running():
            return f"running (pid {job.pid()})", "ok"
        code = job.returncode
        if code is None:
            return "not running", "value"
        if code == 0:
            return "finished", "ok"
        return f"stopped with exit status {code}", "warning"

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        theme = canvas.theme
        job = self.app.services.train_job
        text, role = self.state()
        canvas.put_in(rect, 0, 1, "State", theme.normal)
        canvas.put_in(rect, 0, 8, text, getattr(theme, role))
        log = f"Log    {job.log_path}"
        canvas.put_in(rect, 1, 1, fit(log, rect.width - 2), theme.dim)
        canvas.rule(rect, 2, theme.frame)
        room = max(1, rect.height - 3)
        self.page = room
        lines = job.tail(room + self.scroll)
        self.scroll = min(self.scroll, max(0, len(lines) - room))
        shown = lines[: len(lines) - self.scroll][-room:]
        if not shown:
            canvas.put_in(
                rect, 3, 1, "(no output yet: press S to start the training)", theme.dim
            )
        for row, line in enumerate(shown):
            canvas.put_in(rect, 3 + row, 1, fit(line, rect.width - 2), theme.normal)
        if self.scroll:
            note = f" scrolled back {self.scroll} lines: End follows "
            canvas.put_in(
                rect, 2, max(1, rect.width - len(note) - 1), note, theme.warning
            )

    def key(self, key: int) -> bool:
        if key in (*KEYS_ENTER, ord("s"), ord("S")):
            self.start()
        elif key in (ord("c"), ord("C")):
            self.cancel()
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
        return (
            "Training can run for hours. Esc leaves it running; the log stays in run/."
        )

    def key_bar(self) -> list[tuple[str, str]]:
        return [
            ("S", "Start"),
            ("C", "Cancel"),
            (self.app.canvas.glyphs.up_down, "Scroll"),
            ("End", "Follow"),
            ("Esc", "Back"),
            ("F10", "Quit"),
        ]
