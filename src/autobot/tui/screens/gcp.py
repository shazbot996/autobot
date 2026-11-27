"""The Google Cloud screen: connect autobot to Google Cloud, one step at a time.

The screen lists the eight steps of `autobot.gcp.checks` with the state of
each. Enter opens a step: what it is for, what was checked and found, and the
`gcloud` commands that fix what is missing. The owner copies those and runs
them himself; nothing here runs one (GC-2). `W` writes the open step's
commands to `run/gcp/<step>.sh`, and `R` checks every step again.

An open step is drawn without the frame's side borders, and each command
stands alone on its own rows from the left edge, so selecting it with the
mouse copies the command and nothing else. A command longer than the screen
is continued with a backslash (`commands.fold_command`), so the rows pasted
together are still the one command.

The screen changes two things only: `[gcp] project` and `[gcp] region`, typed
into fields on the Project step and written to the config file at once, and a
secret's value, pasted into a masked field on the Secret Manager step and
sent to Secret Manager through the service account (GC-9). The checks run on
a thread (`Services.spawn`), as the service account or not at all (GC-4).
"""

from __future__ import annotations

import curses
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from autobot.config import model_complaint, project_complaint, region_complaint
from autobot.gcp.checks import (
    STEP_GEMINI,
    STEP_ORDER,
    STEP_PROJECT,
    STEP_SECRETS,
    TITLES,
    SecretStatus,
    State,
    Step,
    run_all,
)
from autobot.gcp.client import GcpError, GoogleCloud
from autobot.gcp.commands import Command, fold_command, script_name, write_script
from autobot.gcp.keyfile import check_key
from autobot.gcp.secrets import store_secret
from autobot.tui.app import KEY_ESC, KEYS_BACKSPACE, KEYS_ENTER, SPINNER, View
from autobot.tui.config_edit import Change
from autobot.tui.runner import FunctionTask
from autobot.tui.ui import Canvas, Rect, fit, wrap

TABLE = "gcp"
CHECKING = "checking..."
TYPE_A_MODEL = "Type another model's id..."

# The colour role of each state, in the list and as the open step's badge.
STATE_ROLES = {
    State.READY: ("ok", "badge_ok"),
    State.MISSING: ("error", "badge_error"),
    State.UNCHECKED: ("warning", "badge_warning"),
    State.NOT_USED: ("dim", "badge_info"),
}


@dataclass(frozen=True)
class Outcome:
    """What one round of checks found."""

    steps: tuple[Step, ...]
    # The connection the checks used; None when there was none.
    cloud: GoogleCloud | None


@dataclass(frozen=True)
class Action:
    """A row of an open step that Enter does something on."""

    label: str
    hint: str
    run: Callable[[], None]


@dataclass(frozen=True)
class Row:
    """One row of an open step."""

    text: str = ""
    role: str = "normal"
    # A command row: drawn from the left edge with nothing beside it.
    command: bool = False
    # The index of the action the row is, or -1.
    action: int = -1


class GcpScreen(View):
    title = "Google Cloud"
    help_lines = (
        "Up, Down       move between the steps; in a step, its fields",
        "Enter          open a step; on a field or a secret, change it",
        "R              check every step again",
        "W              write the step's commands to run/gcp/ as a script",
        "PgUp, PgDn     scroll an open step",
        "",
        "autobot only shows the gcloud commands. You run them yourself.",
    )

    def enter(self) -> None:
        self.steps: tuple[Step, ...] | None = None
        self.cloud: GoogleCloud | None = None
        self.task: FunctionTask | None = None
        self.selected = 0
        # Whether the selected step is open, and where its rows are scrolled to.
        self.open = False
        self.top = 0
        self.cursor = 0
        # What the last draw of an open step could show, for the scroll keys.
        self.row_count = 0
        self.visible = 1
        self.action_rows: list[int] = []
        # Whether something changed while a check was running: check again after it.
        self.again = False
        self.recheck()

    # --- checking -----------------------------------------------------------

    def recheck(self) -> None:
        """Check every step again, on a thread: the frame never waits for it."""
        if self.task is not None:
            self.again = True
            return
        app = self.app
        app.reload()
        services = app.services
        config = app.config

        def job() -> Outcome:
            key = check_key(config.gcp, services.repo, services.git_check_ignore)
            cloud: GoogleCloud | None = None
            refused = ""
            if key.ready:
                try:
                    cloud = services.gcp_connect(config, services.repo)
                except GcpError as error:
                    refused = str(error)
            return Outcome(run_all(config.gcp, cloud, key, refused), cloud)

        self.task = services.spawn(job)

    def tick(self) -> None:
        task = self.task
        if task is None:
            return
        result = task.poll()
        if result is None:
            return
        self.task = None
        outcome = task.value
        if result.ok and isinstance(outcome, Outcome):
            self.steps = outcome.steps
            self.cloud = outcome.cloud
        else:
            self.app.error(f"Not checked: {result.summary()}")
        if self.again:
            self.again = False
            self.recheck()

    @property
    def step(self) -> Step | None:
        return self.steps[self.selected] if self.steps else None

    # --- what Enter does on a row of an open step -----------------------------

    def actions(self, step: Step) -> list[Action]:
        gcp = self.app.config.gcp
        if step.id == STEP_PROJECT:
            return [
                Action(
                    f"Project  {gcp.project}",
                    "Enter changes the project; it is written to [gcp] project",
                    self.edit_project,
                ),
                Action(
                    f"Region   {gcp.region}",
                    "Enter changes the region; it is written to [gcp] region",
                    self.edit_region,
                ),
            ]
        if step.id == STEP_SECRETS:
            return [self.secret_action(secret) for secret in step.secrets]
        if step.id == STEP_GEMINI:
            return [
                Action(
                    f"Model    {gcp.gemini_model or '(not chosen)'}",
                    "Enter lists the models the API offers now; the choice is "
                    "written to [gcp] gemini_model",
                    lambda: self.choose_model(step),
                )
            ]
        return []

    def secret_action(self, secret: SecretStatus) -> Action:
        name = f"{secret.name}  (the {secret.agent} agent)"
        if secret.exists and secret.can_add:
            has = "has a value" if secret.has_version else "has no value yet"
            return Action(
                f"{name}: {has}",
                "Enter opens a masked field: paste the value, and it goes to "
                "Secret Manager only",
                lambda: self.paste_secret(secret),
            )
        return Action(
            f"{name}: not ready for a value",
            "Run its create and grant commands below, then press R",
            lambda: self.app.say(
                f"{secret.name} cannot take a value yet: run its create and "
                "grant commands below, then press R",
                "warning",
            ),
        )

    def edit_project(self) -> None:
        self.edit("project", "The Google Cloud project id:", project_complaint)

    def edit_region(self) -> None:
        self.edit("region", "The region, for example us-east5:", region_complaint)

    def edit(
        self, key: str, label: str, complaint: Callable[[str], str | None]
    ) -> None:
        app = self.app
        current = str(getattr(app.config.gcp, key))
        typed = app.prompt(f"Google Cloud {key}", label, current, complaint)
        if typed is None or typed == current:
            return
        if app.save(Change(TABLE, key, typed)):
            self.recheck()

    def choose_model(self, step: Step) -> None:
        """Pick the Gemini model from the ones the API offers, or type one."""
        app = self.app
        current = app.config.gcp.gemini_model
        chosen: str | None = None
        if step.models:
            options = [*step.models, TYPE_A_MODEL]
            selected = step.models.index(current) if current in step.models else 0
            choice = app.pick(
                "Gemini model",
                options,
                selected,
                f"The {len(step.models)} Gemini models the API offers now",
            )
            if choice is None:
                return
            if choice < len(step.models):
                chosen = step.models[choice]
        if chosen is None:
            # No list (the step is unchecked), or a model the list lacks.
            chosen = app.prompt(
                "Gemini model",
                "The model's id, as it stands in a request:",
                current,
                lambda text: model_complaint(text.strip()),
            )
            if chosen is None:
                return
            chosen = chosen.strip()
        if chosen != current and app.save(Change(TABLE, "gemini_model", chosen)):
            self.recheck()

    def paste_secret(self, secret: SecretStatus) -> None:
        app = self.app
        cloud = self.cloud
        if cloud is None:
            app.error(f"{secret.name} not stored: not connected; press R")
            return
        value = app.prompt_secret(
            f"Paste {secret.name}",
            "The value goes to Secret Manager and is kept nowhere else:",
        )
        if value is None:
            app.say(f"Cancelled: {secret.name} is unchanged")
            return
        try:
            # Clears `value` whether it works or not.
            store_secret(cloud, app.config.gcp, secret.name, value)
        except GcpError as error:
            app.error(str(error))
            return
        app.say(f"Stored: {secret.name} has a new version in Secret Manager", "ok")
        self.recheck()

    def write(self) -> None:
        """`W`: write the step's commands as a script. It is never run from here."""
        app = self.app
        step = self.step
        if step is None:
            app.say("The steps are still being checked")
            return
        if not step.script:
            app.say(f"{step.title} has no commands to write")
            return
        try:
            write_script(step.id, step.script, app.services.repo / "run")
        except OSError as error:
            app.error(f"Not written: {error.strerror or error}")
            return
        name = script_name(step.id)
        app.say(f"Written: {name}. Read it, then run it yourself: bash {name}", "ok")

    # --- keys ---------------------------------------------------------------

    def key(self, key: int) -> bool:
        if key in (ord("r"), ord("R")):
            self.recheck()
            self.app.say("Checking every step again")
            return True
        if key in (ord("w"), ord("W")):
            self.write()
            return True
        return self.open_key(key) if self.open else self.list_key(key)

    def list_key(self, key: int) -> bool:
        count = len(STEP_ORDER)
        if key in (curses.KEY_UP, ord("k")):
            self.selected = (self.selected - 1) % count
        elif key in (curses.KEY_DOWN, ord("j")):
            self.selected = (self.selected + 1) % count
        elif key == curses.KEY_HOME:
            self.selected = 0
        elif key == curses.KEY_END:
            self.selected = count - 1
        elif ord("1") <= key <= ord("0") + count:
            self.selected = key - ord("1")
            self.open_step()
        elif key in KEYS_ENTER:
            self.open_step()
        else:
            return False
        return True

    def open_step(self) -> None:
        if self.steps is None:
            self.app.say("The steps are still being checked")
            return
        self.open = True
        self.framed = False
        self.top = 0
        self.cursor = 0

    def close_step(self) -> None:
        self.open = False
        self.framed = True

    def open_key(self, key: int) -> bool:
        step = self.step
        actions = self.actions(step) if step else []
        last = max(0, self.row_count - self.visible)
        page = max(1, self.visible - 1)
        if key in (KEY_ESC, ord("q"), curses.KEY_LEFT, *KEYS_BACKSPACE):
            self.close_step()
        elif key in KEYS_ENTER:
            if actions:
                actions[min(self.cursor, len(actions) - 1)].run()
        elif key in (curses.KEY_UP, ord("k")):
            if actions and self.cursor > 0:
                self.cursor -= 1
                self.show_cursor()
            else:
                self.top = max(0, self.top - 1)
        elif key in (curses.KEY_DOWN, ord("j")):
            if self.cursor < len(actions) - 1:
                self.cursor += 1
                self.show_cursor()
            else:
                self.top = min(last, self.top + 1)
        elif key == curses.KEY_PPAGE:
            self.top = max(0, self.top - page)
        elif key in (curses.KEY_NPAGE, ord(" ")):
            self.top = min(last, self.top + page)
        elif key == curses.KEY_HOME:
            self.top = 0
        elif key == curses.KEY_END:
            self.top = last
        else:
            return False
        return True

    def show_cursor(self) -> None:
        """Scroll so that the selected field is on the screen."""
        if self.cursor >= len(self.action_rows):
            return
        row = self.action_rows[self.cursor]
        if row < self.top:
            self.top = row
        elif row >= self.top + self.visible:
            self.top = row - self.visible + 1

    # --- drawing ------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        if self.open and self.step is not None:
            self.draw_step(canvas, rect, self.step)
        else:
            self.draw_list(canvas, rect)

    def state_text(self, step: Step | None) -> tuple[str, str]:
        """A step's state as the list shows it, and its colour role."""
        if step is None:
            return CHECKING, "dim"
        return step.label, STATE_ROLES[step.state][0]

    def draw_list(self, canvas: Canvas, rect: Rect) -> None:
        theme = canvas.theme
        steps = self.steps
        for index, name in enumerate(STEP_ORDER):
            step = steps[index] if steps else None
            chosen = index == self.selected
            pointer = canvas.glyphs.pointer if chosen else " "
            head = f" {pointer} {index + 1}  {TITLES[name]:<16}"
            canvas.put_in(
                rect, index, 1, head, theme.selected if chosen else theme.normal
            )
            text, role = self.state_text(step)
            canvas.put_in(
                rect,
                index,
                2 + len(head),
                fit(text, rect.width - len(head) - 3),
                getattr(theme, role),
            )
        row = len(STEP_ORDER)
        if row >= rect.height - 1:
            return
        canvas.rule(rect, row, theme.frame)
        below = Rect(rect.y + row + 1, rect.x, rect.height - row - 1, rect.width)
        if self.task is not None:
            spinner = SPINNER[self.app._frames % len(SPINNER)]
            canvas.put_in(
                below, 0, 1, f"Checking every step... {spinner}", theme.accent
            )
            if steps is None:
                return
        step = self.step
        if step is None:
            return
        lines: list[tuple[str, str]] = []
        if self.task is None:
            lines += [(line, "normal") for line in wrap([step.purpose], rect.width - 2)]
        if step.waits_on:
            lines.append((f"Builds on {step.waits_on}, which is not ready yet.", "dim"))
        lines += [(finding.text, finding.role) for finding in step.findings]
        first = 1 if self.task is not None else 0
        room = below.height - first - 1
        for offset, (text, role) in enumerate(lines[: max(0, room)]):
            canvas.put_in(
                below,
                first + offset,
                1,
                fit(text, rect.width - 2),
                getattr(theme, role),
            )
        fixes = len(step.commands)
        tail = "Enter opens the step" + (
            f": {fixes} command{'s' if fixes != 1 else ''} to run yourself"
            if fixes
            else ""
        )
        canvas.put_in(below, below.height - 1, 1, fit(tail, rect.width - 2), theme.dim)

    def rows(self, step: Step, width: int) -> list[Row]:
        """An open step as rows: the text, the fields, then the commands."""
        text_width = max(8, width - 4)
        rows = [Row(line) for line in wrap([step.purpose], text_width)]
        if step.waits_on:
            rows.append(
                Row(f"Builds on {step.waits_on}, which is not ready yet.", "warning")
            )
        rows += [Row(), Row("What was checked and found", "heading")]
        for finding in step.findings:
            rows += [
                Row(line, finding.role) for line in wrap([finding.text], text_width)
            ]
        actions = self.actions(step)
        if actions:
            rows += [Row(), Row("Enter changes", "heading")]
            rows += [
                Row(action.label, "value", action=index)
                for index, action in enumerate(actions)
            ]
        rows += self.command_rows(
            f"Commands for your own shell (W writes {script_name(step.id)})"
            if step.commands
            else "",
            step.commands,
            width,
        )
        rows += self.command_rows(
            "To look for yourself: these change nothing"
            + ("" if step.commands else f" (W writes {script_name(step.id)})"),
            step.read_only,
            width,
        )
        return rows

    def command_rows(
        self, heading: str, commands: Sequence[Command], width: int
    ) -> list[Row]:
        if not commands:
            return []
        # The heading is a shell comment from the left edge, as the purposes
        # are: a selection that takes it in with the commands still pastes.
        rows = [Row(), Row(fit(f"# {heading}", width - 1), "heading", command=True)]
        for command in commands:
            # The purpose is a shell comment: copying it with the command is harmless.
            rows.append(
                Row(fit(f"# {command.purpose}", width - 1), "dim", command=True)
            )
            rows += [
                Row(line, "value", command=True)
                for line in fold_command(command.text, width - 1)
            ]
            rows.append(Row())
        return rows[:-1]

    def draw_step(self, canvas: Canvas, rect: Rect, step: Step) -> None:
        theme = canvas.theme
        head = f" Step {self.selected + 1} of {len(STEP_ORDER)}: {step.title}"
        canvas.put_in(rect, 0, 0, head, theme.heading)
        badge = f" {fit(step.label, rect.width - len(head) - 4).upper()} "
        canvas.put_in(
            rect,
            0,
            max(len(head) + 1, rect.width - len(badge) - 1),
            badge,
            getattr(theme, STATE_ROLES[step.state][1]),
        )
        rows = self.rows(step, rect.width)
        body = Rect(rect.y + 2, rect.x, max(1, rect.height - 2), rect.width)
        self.row_count = len(rows)
        self.visible = body.height
        self.action_rows = [index for index, row in enumerate(rows) if row.action >= 0]
        self.top = max(0, min(self.top, len(rows) - body.height))
        more = len(rows) > body.height
        if more:
            shown = f"rows {self.top + 1}-{min(len(rows), self.top + body.height)} of {len(rows)}"
            canvas.put_in(
                rect, 1, max(0, rect.width - len(shown) - 1), shown, theme.dim
            )
        if self.task is not None:
            spinner = SPINNER[self.app._frames % len(SPINNER)]
            canvas.put_in(rect, 1, 1, f"Checking every step... {spinner}", theme.accent)
        for offset, row in enumerate(rows[self.top : self.top + body.height]):
            if row.command:
                # Alone on its row, from the left edge: nothing is drawn beside it.
                canvas.put_in(body, offset, 0, row.text, getattr(theme, row.role))
            elif row.action >= 0:
                chosen = row.action == self.cursor
                pointer = canvas.glyphs.pointer if chosen else " "
                canvas.put_in(
                    body,
                    offset,
                    1,
                    fit(f" {pointer} {row.text} ", rect.width - 2),
                    theme.selected if chosen else theme.value,
                )
            else:
                canvas.put_in(
                    body,
                    offset,
                    2,
                    fit(row.text, rect.width - 3),
                    getattr(theme, row.role),
                )

    def hint(self) -> str:
        step = self.step
        if step is None:
            return "Checking what is in place in Google Cloud"
        if not self.open:
            return step.purpose
        actions = self.actions(step)
        if actions:
            return actions[min(self.cursor, len(actions) - 1)].hint
        if step.script:
            return "Select a command with the mouse to copy it, or W writes them as a script"
        return "Esc returns to the steps"

    def key_bar(self) -> Sequence[tuple[str, str]]:
        if self.open:
            step = self.step
            change = (("Enter", "Change"),) if step and self.actions(step) else ()
            return (
                ("F1", "Help"),
                *change,
                ("R", "Check"),
                ("W", "Script"),
                ("PgDn", "More"),
                ("Esc", "Steps"),
            )
        return (
            ("F1", "Help"),
            (self.app.canvas.glyphs.up_down, "Step"),
            ("Enter", "Open"),
            ("R", "Check"),
            ("W", "Script"),
            ("Esc", "Back"),
        )
