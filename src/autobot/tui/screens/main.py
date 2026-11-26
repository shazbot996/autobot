"""The main menu."""

from __future__ import annotations

from autobot.tui.app import MenuView, TextView
from autobot.tui.menu import MenuItem
from autobot.tui.screens.audio import AudioScreen
from autobot.tui.screens.host import HostScreen
from autobot.tui.screens.service import ServiceScreen
from autobot.tui.screens.wake_phrase import WakePhraseScreen
from autobot.tui.ui import Canvas, Rect, fit, fit_left

NO_CLOUD_PROJECT = (
    "The Google Cloud project does not exist yet.",
    "",
    "Nothing can be done here until it does. This build is the headless",
    "wake-word prototype: it runs on this machine and uses no cloud service.",
    "",
    "Press Esc to go back.",
)


class MainMenu(MenuView):
    title = "Main Menu"

    def items(self) -> list[MenuItem]:
        app = self.app
        return [
            MenuItem(
                "Audio",
                lambda: app.push(AudioScreen(app)),
                "Pick the microphone and speaker, watch the level, record a test clip",
            ),
            MenuItem(
                "Wake phrase",
                lambda: app.push(WakePhraseScreen(app)),
                "Threshold, detector kind, training, the spoken acknowledgement",
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
                "Cloud deployment",
                lambda: app.push(TextView(app, "Cloud deployment", NO_CLOUD_PROJECT)),
                "Not available: the Google Cloud project does not exist yet",
            ),
            MenuItem(
                "Identity and access management",
                lambda: app.push(
                    TextView(app, "Identity and access management", NO_CLOUD_PROJECT)
                ),
                "Not available: the Google Cloud project does not exist yet",
            ),
            MenuItem("Quit", app.quit, "Leave the setup utility"),
        ]

    def draw_panel(self, canvas: Canvas, rect: Rect) -> None:
        config = self.app.config
        store = self.app.services.store
        phrase = config.wake_phrases[0]
        lines = (
            f"Config file  {fit_left(str(store.path), rect.width - 15)}",
            f"NUC          {config.host.nuc_hostname or '(not set)'}",
            f"Audio        in: {config.audio.input_device}   out: {config.audio.output_device}",
            f"Wake phrase  {phrase.phrase!r}, {phrase.kind}, threshold {phrase.threshold:g}",
        )
        for row, line in enumerate(lines):
            canvas.put_in(rect, row + 1, 1, fit(line, rect.width - 2), canvas.theme.dim)
