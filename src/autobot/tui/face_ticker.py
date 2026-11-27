"""The face's ticker: what the listener hears, scrolling along one line. No drawing.

`autobot.tui.screens.face` draws this. The ticker reads the events
`EventSource.poll` returns, from `run/listener.sock` (TS-10), and nothing
else: the face runs no speech-to-text of its own (FS-3, `CN-1`).

- A `transcript` event becomes an item holding its text.
- A `score` event from a phrase whose kind does not transcribe speech
  (`openwakeword`, `sherpa_kws`) becomes a stretch marker, one per stretch,
  but only for a stretch that has no transcript: words take precedence.
- A `wake` pops the newest item for its phrase in the current stretch, the
  stretch of the last `score` or `transcript` line (a wake carries no
  identifier; `autobot.listener.events`). On a transcript the words of the
  phrase are popped, found by `phrase_span`; on a marker, or a transcript
  the phrase cannot be found in, the whole item is.

The items lie along one strip, a gap apart, which enters at the right and
moves at a steady speed by the clock, `SPEED` columns a second, never more
than one column a frame. A terminal can move text no finer than a column, so
the smoothest scroll is one column at a time at even intervals: `due` says
when the next column is due, and the screen times its next frame by it. While
text waits to enter, the speed rises with the backlog, smoothly, up to
`1 + CATCH_UP` times `SPEED` with a whole width waiting, and falls again as it
clears. The strip is never more than one width behind: a feed that would
leave it further behind skips ahead. What has scrolled off the left is
dropped, and nothing is written anywhere: a transcript of the room is not
something to keep.

While the face holds the listener (`muted`) events are dropped, so nothing
autobot says itself is shown. While the event stream is disconnected
(`down`) the ticker shows one sentence and does not scroll.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from autobot.tui.monitor import EVENT_SCORE, EVENT_WAKE, Event

EVENT_TRANSCRIPT = "transcript"
# The detector kinds that yield no words: their stretches show a marker.
MARKER_KINDS = ("openwakeword", "sherpa_kws")
# The marker, unless the screen gives its glyph set's bullet.
DEFAULT_MARKER = "*"
# Blank columns between two items.
GAP = 3
# Columns a second the strip moves while nothing waits to enter: a little
# faster than speech is spoken, so it seldom falls behind.
SPEED = 16.0
# How much faster it moves with a whole width waiting, as a multiple of SPEED.
CATCH_UP = 1.0
# The width assumed until the first `view`.
DEFAULT_WIDTH = 68
# What the ticker says while the listener's socket is absent.
DOWN_TEXT = "The listener is down: nothing is being heard."

# The containment rule's normalisation (`autobot.listener.engines.text_match`,
# which the interface does not import): lower case, apostrophes dropped, any
# other character that is not a letter or a digit a word break.
_APOSTROPHES = "'’"
_WORD_CHARACTER = re.compile(r"[a-z0-9]")


@dataclass(frozen=True)
class Cell:
    """One column of the ticker's view."""

    char: str
    # True for a character of a popped wake phrase, drawn in `theme.wake`.
    wake: bool = False


def _letters(text: str) -> tuple[list[tuple[str, int]], list[int]]:
    """The normalised letters of `text`, each with its index in `text`, and
    the positions in that list where a word starts."""
    letters: list[tuple[str, int]] = []
    starts: list[int] = []
    in_word = False
    for index, character in enumerate(text):
        for lowered in character.lower():
            if lowered in _APOSTROPHES:
                continue
            if _WORD_CHARACTER.fullmatch(lowered):
                if not in_word:
                    starts.append(len(letters))
                    in_word = True
                letters.append((lowered, index))
            else:
                in_word = False
    return letters, starts


def phrase_span(text: str, phrase: str) -> tuple[int, int] | None:
    """Where `phrase` stands in `text`, as `(start, end)` indices of `text`.

    The match is the listener's containment rule (WW-6): the phrase's
    letters, spaces removed, among the text's letters, starting at the start
    of a word. So "Hey Auto-bot!" holds `autobot` at "Auto-bot". The first
    match is given; None when there is none.
    """
    wanted = "".join(letter for letter, _ in _letters(phrase)[0])
    if not wanted:
        return None
    letters, starts = _letters(text)
    compact = "".join(letter for letter, _ in letters)
    for start in starts:
        if compact.startswith(wanted, start):
            return letters[start][1], letters[start + len(wanted) - 1][1] + 1
    return None


@dataclass
class _Item:
    """One transcript, or one stretch's marker, on the strip."""

    text: str
    # The phrases whose events made it: one for a transcript, unless two
    # `text_match` phrases wrote the same words.
    phrases: list[str]
    # None once the listener has started again: the identifier is reused.
    stretch: int | None
    # Where it starts on the strip.
    start: int
    marker: bool = False
    # The popped part of `text`, as indices; None until a wake pops it.
    popped: tuple[int, int] | None = field(default=None)

    @property
    def end(self) -> int:
        return self.start + len(self.text)


def _stretch_id(event: Event) -> int | None:
    value = event.get("stretch")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


class Ticker:
    """The ticker's model: items on a strip, and how far it has scrolled.

    Positions are columns of the strip. `_head` is the column that has just
    entered at the right; a view `width` wide shows the columns
    `_head - width` to `_head - 1`.
    """

    def __init__(self, marker: str = DEFAULT_MARKER) -> None:
        self.marker = marker
        # Set while the face holds the listener: events are dropped.
        self.muted = False
        # The width of the last view.
        self.width = DEFAULT_WIDTH
        self._down = False
        self._items: list[_Item] = []
        self._head = 0
        self._end = 0
        # The clock at the last `advance`, and how far towards the next
        # column the strip has moved since, in columns.
        self._clock: float | None = None
        self._carry = 0.0
        # The stretch of the last score or transcript line.
        self._stretch: int | None = None

    # --- state -------------------------------------------------------------

    @property
    def down(self) -> bool:
        """Whether the event stream is disconnected."""
        return self._down

    @down.setter
    def down(self, value: bool) -> None:
        if value and not self._down:
            # What was on the strip belongs to a listener that has gone.
            self._items = []
            self._head = self._end
            self._stretch = None
        self._down = value

    @property
    def waiting(self) -> int:
        """How many columns of text have yet to enter at the right."""
        return max(0, self._end - self._head)

    @property
    def text(self) -> str:
        """What the strip holds, items a gap apart: for tests and reading."""
        return (" " * GAP).join(item.text for item in self._items)

    # --- events ------------------------------------------------------------

    def feed(self, events: Iterable[Event]) -> None:
        """Take what `EventSource.poll` returned."""
        for event in events:
            if self.muted:
                continue
            kind = event.get("event")
            if kind == EVENT_TRANSCRIPT:
                self._transcript(event)
            elif kind == EVENT_SCORE:
                self._score(event)
            elif kind == EVENT_WAKE:
                self._wake(str(event.get("phrase", "")))
        self._catch_up()

    def note(self, text: str) -> None:
        """Put a line of the face's own on the strip, as "server up" (IS-23).

        It is not something the listener heard, so it belongs to no stretch
        and is shown even while the face holds the listener.
        """
        line = " ".join(text.split())
        if line:
            self._add(_Item(line, [], None, 0))
            self._catch_up()

    def _see(self, stretch: int) -> None:
        if self._stretch is not None and stretch < self._stretch:
            # The listener started again and numbers its stretches from 1.
            for item in self._items:
                item.stretch = None
        self._stretch = stretch

    def _transcript(self, event: Event) -> None:
        stretch = _stretch_id(event)
        text = " ".join(str(event.get("text", "")).split())
        if stretch is None or not text:
            return
        self._see(stretch)
        phrase = str(event.get("phrase", ""))
        newest = self._items[-1] if self._items else None
        if (
            newest is not None
            and not newest.marker
            and newest.stretch == stretch
            and newest.text == text
        ):
            # Another `text_match` phrase transcribed the same speech.
            if phrase not in newest.phrases:
                newest.phrases.append(phrase)
            return
        self._add(_Item(text, [phrase], stretch, 0))

    def _score(self, event: Event) -> None:
        stretch = _stretch_id(event)
        if stretch is None:
            return
        self._see(stretch)
        if event.get("kind") not in MARKER_KINDS:
            return
        phrase = str(event.get("phrase", ""))
        held = [item for item in self._items if item.stretch == stretch]
        if any(not item.marker for item in held):
            return
        if held:
            if phrase not in held[-1].phrases:
                held[-1].phrases.append(phrase)
            return
        self._add(_Item(self.marker, [phrase], stretch, 0, marker=True))

    def _wake(self, phrase: str) -> None:
        if self._stretch is None:
            return
        for item in reversed(self._items):
            if item.stretch != self._stretch or phrase not in item.phrases:
                continue
            if item.popped is not None:
                continue
            span = None if item.marker else phrase_span(item.text, phrase)
            item.popped = span or (0, len(item.text))
            return

    def _add(self, item: _Item) -> None:
        item.start = max(self._end + GAP, self._head) if self._items else self._head
        self._items.append(item)
        self._end = item.end

    # --- scrolling ---------------------------------------------------------

    def _catch_up(self) -> None:
        """Never more than one width behind: skip ahead, and drop what has gone."""
        self._head = max(self._head, self._end - self.width)
        left = self._head - self.width
        self._items = [item for item in self._items if item.end > left]

    def speed(self) -> float:
        """Columns a second: `SPEED`, rising smoothly with the text waiting."""
        backlog = min(self.waiting, self.width) / max(1, self.width)
        return SPEED * (1.0 + CATCH_UP * backlog)

    def advance(self, now: float) -> None:
        """One frame at `now`, in seconds: the strip moves on at its speed.

        It moves at most one column, however long the frame was: a late frame
        is not made up in a jump, which is what would make the scroll stutter.
        """
        last, self._clock = self._clock, now
        if self._down or last is None:
            self._carry = 0.0
            return
        self._carry += max(0.0, now - last) * self.speed()
        if self._carry >= 1.0:
            self._head += 1
            self._carry = 0.0 if self._carry >= 2.0 else self._carry - 1.0
            self._catch_up()

    def due(self, now: float) -> float:
        """Seconds from `now` until the next column is due; 0 when it is."""
        if self._down or self._clock is None:
            return 1.0 / SPEED
        elapsed = max(0.0, now - self._clock)
        return max(0.0, (1.0 - self._carry) / self.speed() - elapsed)

    def view(self, width: int) -> list[Cell]:
        """The `width` columns on show, left to right."""
        width = max(0, width)
        self.width = width or DEFAULT_WIDTH
        if self._down:
            return [Cell(char) for char in DOWN_TEXT[:width].ljust(width)]
        cells = [Cell(" ") for _ in range(width)]
        left = self._head - width
        for item in self._items:
            if item.end <= left or item.start >= self._head:
                continue
            for index, char in enumerate(item.text):
                column = item.start + index - left
                if 0 <= column < width:
                    popped = item.popped is not None and (
                        item.popped[0] <= index < item.popped[1]
                    )
                    cells[column] = Cell(char, popped)
        return cells
