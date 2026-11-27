"""The Agent routing screen: the rules that hand a command after the wake to an agent.

The wake phrase only wakes autobot; the command said after it is routed by
the `[[routes]]` rules of `config/autobot.toml` (`autobot.routing`). This
screen lists them in the order they are tried, edits a rule's command and
its example phrases, and tries a typed sentence against them, as the face
would route it. Every change is written to the config file (TS-10).

A rule's `examples` are phrases that mean what its command means. They are
what the routing cascade's second tier compares a sentence with when no
command stands in it (IS-17, `autobot.cascade`). The sentence tried here is
tried against the keyword rules only, which is tier 1.
"""

from __future__ import annotations

from functools import partial

from autobot.config import KIND_TEXT_MATCH, ROUTE_AGENTS
from autobot.routing import AGENT_NOTES, commands_text, match_route, normalise
from autobot.tui.app import MenuView
from autobot.tui.config_edit import Change
from autobot.tui.menu import MenuItem
from autobot.tui.router import LISTEN_SECONDS
from autobot.tui.ui import Canvas, Rect, fit

TABLE = "routes"
NOT_TRIED = "(type a sentence to see where it goes)"
# What separates the example phrases in the one line they are typed on.
EXAMPLE_SEPARATOR = ";"
NO_MATCH = "no rule matches: autobot keeps listening"


def split_examples(text: str) -> list[str]:
    """The phrases of one typed line, spaces squeezed, empty ones dropped."""
    phrases = (" ".join(part.split()) for part in text.split(EXAMPLE_SEPARATOR))
    return [phrase for phrase in phrases if phrase]


class RoutingScreen(MenuView):
    title = "Agent routing"
    facts_title = "How a command is routed"
    panel_rows = 4

    def enter(self) -> None:
        # The last sentence tried, and where it went.
        self.tried = ""
        self.result = ""

    # --- actions ------------------------------------------------------------

    def edit_command(self, index: int) -> None:
        routes = self.app.config.routes
        route = routes[index]
        others = [
            normalise(other.command)
            for number, other in enumerate(routes)
            if number != index
        ]

        def complaint(text: str) -> str | None:
            if not normalise(text):
                return "type a word for the command, for example echo"
            if normalise(text) in others:
                return "another rule has that command"
            return None

        typed = self.app.prompt(
            f"Rule {index + 1}: the {route.agent} agent",
            "The word to listen for after the wake phrase:",
            route.command,
            complaint,
        )
        if typed is not None:
            self.app.save(Change(TABLE, "command", " ".join(typed.split()), index))

    def edit_examples(self) -> None:
        """Ask which rule, then its example phrases on one line, and write them."""
        routes = self.app.config.routes
        if not routes:
            self.app.error("there is no rule to give examples to")
            return

        def which(text: str) -> str | None:
            if not text.strip().isdigit() or not 1 <= int(text) <= len(routes):
                return f"type a rule's number, from 1 to {len(routes)}"
            return None

        chosen = self.app.prompt(
            "Example phrases", "The number of the rule:", "1", which
        )
        if chosen is None:
            return
        index = int(chosen) - 1
        route = routes[index]

        def complaint(text: str) -> str | None:
            phrases = [normalise(phrase) for phrase in split_examples(text)]
            if len(set(phrases)) != len(phrases):
                return "a phrase is there twice"
            return None

        typed = self.app.prompt(
            f'Rule {index + 1}: "{route.command}" -> {route.agent} agent',
            f"Phrases that mean the same, with {EXAMPLE_SEPARATOR} between them:",
            f"{EXAMPLE_SEPARATOR} ".join(route.examples),
            complaint,
        )
        if typed is not None:
            self.app.save(Change(TABLE, "examples", split_examples(typed), index))

    def try_sentence(self) -> None:
        typed = self.app.prompt(
            "Try a sentence",
            "What you would say after the wake phrase:",
            self.tried,
        )
        if typed is None:
            return
        self.tried = " ".join(typed.split())
        match = match_route(self.tried, self.app.config.routes)
        self.result = (
            NO_MATCH if match is None else f"{match.describe()}: runs the agent"
        )
        self.app.say(f'"{self.tried}": {self.result}', "ok" if match else "warning")

    # --- drawing ------------------------------------------------------------

    def facts(self) -> list[tuple[str, str, str]]:
        config = self.app.config
        phrases = ", ".join(f'"{phrase.phrase}"' for phrase in config.wake_phrases)
        hears = any(phrase.kind == KIND_TEXT_MATCH for phrase in config.wake_phrases)
        return [
            ("Wake phrase", f"{phrases}: wakes autobot, then it listens", "value"),
            ("Commands", commands_text(config.routes) or "(none)", "value"),
            ("Listens for", f"{LISTEN_SECONDS:g} s after the wake", "value"),
            (
                "Words from",
                "the listener's text_match transcripts"
                if hears
                else "nowhere: no wake phrase is text_match, so none is heard",
                "value" if hears else "warning",
            ),
            ("Agents", ", ".join(ROUTE_AGENTS), "value"),
        ]

    def items(self) -> list[MenuItem]:
        items = [
            MenuItem(
                f'Rule {index + 1}: "{route.command}" -> {route.agent} agent'
                + (f" ({route.mode})" if route.mode else "")
                + (f", {len(route.examples)} examples" if route.examples else ""),
                partial(self.edit_command, index),
                " ".join(
                    ["The", route.agent, "agent", AGENT_NOTES.get(route.agent, "")]
                ).rstrip()
                + "; Enter changes the command",
            )
            for index, route in enumerate(self.app.config.routes)
        ]
        items.append(
            MenuItem(
                "Try a sentence...",
                self.try_sentence,
                "Type what you would say after the wake phrase; see which rule takes it",
            )
        )
        items.append(
            MenuItem(
                "Example phrases of a rule...",
                self.edit_examples,
                "Phrases that mean what a rule's command means, for sentences "
                "with no command in them",
            )
        )
        items.append(MenuItem("Back", self.app.pop, "Return to the main menu"))
        return items

    def draw_panel(self, canvas: Canvas, rect: Rect) -> None:
        theme = canvas.theme
        width = rect.width - 2
        lines = [
            ("Say the wake phrase, then a sentence with a command in it.", "normal"),
            ("The first rule whose command is in the sentence wins.", "dim"),
        ]
        if self.tried:
            lines.append((f'Tried: "{self.tried}"', "normal"))
            lines.append((self.result, "warning" if self.result == NO_MATCH else "ok"))
        else:
            lines.append((NOT_TRIED, "dim"))
        for row, (text, role) in enumerate(lines[: rect.height]):
            canvas.put_in(rect, row, 1, fit(text, width), getattr(theme, role))
