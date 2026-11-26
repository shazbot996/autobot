"""The Service screen: the listener's systemd user unit.

Every entry runs `python -m autobot.listener.control <action>` and shows what
it printed. The listener is not imported; that command is the whole contract.
"""

from __future__ import annotations

from functools import partial

from autobot.tui.app import MenuView
from autobot.tui.menu import MenuItem
from autobot.tui.runner import LISTENER_CONTROL_MODULE, listener_control_argv
from autobot.tui.ui import Canvas, Rect, wrap

# (action, label, hint, needs confirmation)
ACTIONS = (
    ("install", "Install the unit", "Write the unit to ~/.config/systemd/user/", False),
    ("enable", "Enable at boot", "Enable the unit and turn on lingering", False),
    ("up", "Start", "Start the listener", False),
    ("down", "Stop", "Stop the listener", False),
    ("status", "Status", "Show whether the listener runs", False),
    ("disable", "Disable at boot", "Stop starting the listener at boot", True),
    ("uninstall", "Uninstall the unit", "Remove the unit file", True),
)


class ServiceScreen(MenuView):
    title = "Service"

    def enter(self) -> None:
        self.command = ""
        self.output: list[str] = ["Choose Status to ask systemd about the listener."]
        self.failed = False

    def items(self) -> list[MenuItem]:
        return [
            MenuItem(label, partial(self.run, action), hint)
            for action, label, hint, _ in ACTIONS
        ]

    def run(self, action: str) -> None:
        app = self.app
        label, confirm = next(
            (entry[1], entry[3]) for entry in ACTIONS if entry[0] == action
        )
        if not app.services.module_available(LISTENER_CONTROL_MODULE):
            self.command = action
            self.failed = True
            self.output = [
                f"The listener is not built yet ({LISTENER_CONTROL_MODULE} is missing)."
            ]
            app.error(self.output[0])
            return
        if confirm and not app.confirm(label, [f"{label}?"]):
            return
        result = app.run_command(label, listener_control_argv(action))
        self.command = action
        self.failed = not result.ok
        self.output = [line for line in result.lines if line.strip()] or [
            result.summary()
        ]
        if result.ok:
            app.say(f"{label}: {result.summary()}", "ok")
        else:
            app.error(f"{label} failed: {result.summary()}")

    def draw_panel(self, canvas: Canvas, rect: Rect) -> None:
        theme = canvas.theme
        heading = f"Output of '{self.command}'" if self.command else "Output"
        canvas.put_in(rect, 0, 1, heading, theme.heading)
        room = rect.height - 1
        attr = theme.warning if self.failed else theme.normal
        lines = wrap(self.output, rect.width - 2)
        for row, line in enumerate(lines[-room:] if room > 0 else []):
            canvas.put_in(rect, row + 1, 1, line, attr)
