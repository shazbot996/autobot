"""The Word lists screen: the yes words, the no words and the global action keywords.

The `[words]` table of `config/autobot.toml` (`autobot.config.WordsConfig`)
holds three lists every agent shares: the words that answer a confirmation
yes, the words that answer it no (AG-13), and the global action keywords,
which act at every level of the tree and are tried before the rules (AG-14).
This screen shows the three lists, edits one as a line of words separated by
commas, and tries a typed sentence against them, as a session would hear it
(`autobot.agents.words`, `autobot.agents.confirm`). A word stands in one
list only. Every change is written to the config file (TS-10).

The splitting and the checking of a typed list do no drawing.
"""

from __future__ import annotations

from functools import partial

from autobot.agents.confirm import Answer, answer
from autobot.agents.words import match_word
from autobot.config import WordsConfig
from autobot.tui.app import MenuView
from autobot.tui.config_edit import Change
from autobot.tui.menu import MenuItem
from autobot.tui.ui import Canvas, Rect, fit

TABLE = "words"
# Each list: its key in the `[words]` table, its menu label, and what one of
# its words is called in a sentence.
LISTS: tuple[tuple[str, str, str], ...] = (
    ("yes", "Yes words", "a yes word"),
    ("no", "No words", "a no word"),
    ("actions", "Global action keywords", "a global action keyword"),
)
NAMES = {key: name for key, _, name in LISTS}
HINTS = {
    "yes": "The words that answer a confirmation yes; Enter changes the list",
    "no": "The words that answer a confirmation no; Enter changes the list",
    "actions": "The words that act at every level, as cancel; Enter changes the list",
}
EMPTY = "type at least one word, with commas between words"
NOT_TRIED = "(type a sentence to see which list it hits)"
NO_MATCH = "in no list: it answers nothing and leaves nothing"


def split_words(text: str) -> list[str]:
    """The words of a typed list: split on commas, trimmed, lower-cased, each once."""
    words: list[str] = []
    for part in text.split(","):
        word = " ".join(part.lower().split())
        if word and word not in words:
            words.append(word)
    return words


def complaint(key: str, text: str, words: WordsConfig) -> str | None:
    """The one line that refuses `text` as the list `key`; None when it is valid.

    A list needs a word, and a word that is in another list is refused with
    that list's name.
    """
    typed = split_words(text)
    if not typed:
        return EMPTY
    for other, _, name in LISTS:
        if other == key:
            continue
        taken = {word.lower() for word in getattr(words, other)}
        for word in typed:
            if word in taken:
                return f'"{word}" is already {name}'
    return None


def describe(sentence: str, words: WordsConfig) -> str:
    """Which list `sentence` hits, in one line, as a session would hear it.

    The global action keywords are tried first, then the no words, then the
    yes words, as `autobot.agents.confirm.answer` does.
    """
    action = match_word(sentence, words.actions)
    if action is not None:
        return f'global action keyword "{action}": leaves what runs'
    said = answer(sentence, words)
    if said is Answer.NO:
        return f'no word "{match_word(sentence, words.no)}": answers no'
    if said is Answer.YES:
        return f'yes word "{match_word(sentence, words.yes)}": answers yes'
    return NO_MATCH


class WordListsScreen(MenuView):
    title = "Word lists"
    facts_title = "What each list is for"
    panel_rows = 4

    def enter(self) -> None:
        # The last sentence tried, and the list it hit.
        self.tried = ""
        self.result = ""

    # --- actions ------------------------------------------------------------

    def edit_list(self, key: str, label: str) -> None:
        words = self.app.config.words
        typed = self.app.prompt(
            label,
            f"{label}, separated by commas; a word is in one list only:",
            ", ".join(getattr(words, key)),
            lambda text: complaint(key, text, words),
        )
        if typed is not None:
            self.app.save(Change(TABLE, key, split_words(typed)))

    def try_word(self) -> None:
        typed = self.app.prompt(
            "Try a word",
            "What you would say to autobot:",
            self.tried,
        )
        if typed is None:
            return
        self.tried = " ".join(typed.split())
        self.result = describe(self.tried, self.app.config.words)
        self.app.say(
            f'"{self.tried}": {self.result}',
            "warning" if self.result == NO_MATCH else "ok",
        )

    # --- drawing ------------------------------------------------------------

    def facts(self) -> list[tuple[str, str, str]]:
        return [
            ("Yes words", "answer a confirmation yes", "value"),
            ("No words", "answer a confirmation no", "value"),
            ("Action keywords", "act at every level, tried before the rules", "value"),
            ("A word", "stands in one list only", "value"),
        ]

    def items(self) -> list[MenuItem]:
        words = self.app.config.words
        # The menu's own margins, pointer and number take the rest of the row.
        width = self.app.canvas.size()[1] - 12
        items = [
            MenuItem(
                fit(f"{label}: {', '.join(getattr(words, key))}", width),
                partial(self.edit_list, key, label),
                HINTS[key],
            )
            for key, label, _ in LISTS
        ]
        items.append(
            MenuItem(
                "Try a word...",
                self.try_word,
                "Type what you would say; see which list it hits",
            )
        )
        items.append(MenuItem("Back", self.app.pop, "Return to the main menu"))
        return items

    def draw_panel(self, canvas: Canvas, rect: Rect) -> None:
        theme = canvas.theme
        width = rect.width - 2
        lines = [
            (
                "A word counts where it stands as whole words in what is heard.",
                "normal",
            ),
            ("Action keywords are tried first, then no words, then yes words.", "dim"),
        ]
        if self.tried:
            lines.append((f'Tried: "{self.tried}"', "normal"))
            lines.append((self.result, "warning" if self.result == NO_MATCH else "ok"))
        else:
            lines.append((NOT_TRIED, "dim"))
        for row, (text, role) in enumerate(lines[: rect.height]):
            canvas.put_in(rect, row, 1, fit(text, width), getattr(theme, role))
