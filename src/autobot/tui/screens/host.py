"""The Host screen: name this machine as the NUC."""

from __future__ import annotations

from autobot.tui.app import MenuView
from autobot.tui.config_edit import Change
from autobot.tui.menu import MenuItem
from autobot.tui.ui import Canvas, Rect, fit


class HostScreen(MenuView):
    title = "Host"

    def is_nuc(self) -> bool:
        nuc = self.app.config.host.nuc_hostname
        return bool(nuc) and nuc.lower() == self.app.services.hostname.lower()

    def facts(self) -> list[tuple[str, str, str]]:
        nuc = self.app.config.host.nuc_hostname
        return [
            ("This machine", self.app.services.hostname, "value"),
            ("nuc_hostname", nuc or "(not set)", "ok" if self.is_nuc() else "warning"),
        ]

    def items(self) -> list[MenuItem]:
        hostname = self.app.services.hostname
        return [
            MenuItem(
                f"Set nuc_hostname to this machine ({hostname})",
                self.set_to_this_machine,
                "Writes [host] nuc_hostname to the config file",
            ),
            MenuItem("Back", self.app.pop, "Return to the main menu"),
        ]

    def set_to_this_machine(self) -> None:
        self.app.save(Change("host", "nuc_hostname", self.app.services.hostname))

    def draw_panel(self, canvas: Canvas, rect: Rect) -> None:
        if self.is_nuc():
            lines = [
                "This machine is the NUC.",
                "`make audio-check` and the listener run here.",
            ]
            attr = canvas.theme.ok
        else:
            lines = [
                "This machine is not the configured NUC.",
                "`make audio-check` refuses to run on any other host,",
                "and when nuc_hostname is empty.",
            ]
            attr = canvas.theme.warning
        for row, line in enumerate(lines):
            canvas.put_in(rect, row + 1, 1, fit(line, rect.width - 2), attr)
