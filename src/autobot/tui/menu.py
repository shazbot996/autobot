"""The menu model: a list of items and a selection. No drawing."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class MenuItem:
    label: str
    action: Callable[[], None]
    # One line shown in the message bar while the item is selected.
    hint: str = ""
    # A disabled item is drawn dimmed and cannot be selected.
    enabled: bool = True


class Menu:
    """Items with one selected. Movement wraps and skips disabled items."""

    def __init__(self, items: Sequence[MenuItem], selected: int = 0) -> None:
        self.items: list[MenuItem] = list(items)
        self.selected = 0
        self.select(selected)

    def select(self, index: int) -> None:
        """Select `index`, or the nearest enabled item after it."""
        if not self.items:
            self.selected = 0
            return
        index = max(0, min(index, len(self.items) - 1))
        for offset in range(len(self.items)):
            candidate = (index + offset) % len(self.items)
            if self.items[candidate].enabled:
                self.selected = candidate
                return
        self.selected = index

    def replace(self, items: Sequence[MenuItem]) -> None:
        """Swap the items (labels change as state changes), keeping the position."""
        self.items = list(items)
        self.select(self.selected)

    def move(self, delta: int) -> None:
        """Move the selection by `delta` enabled items, wrapping at the ends."""
        if not any(item.enabled for item in self.items):
            return
        step = 1 if delta > 0 else -1
        for _ in range(abs(delta)):
            index = self.selected
            for _ in range(len(self.items)):
                index = (index + step) % len(self.items)
                if self.items[index].enabled:
                    break
            self.selected = index

    def first(self) -> None:
        self.select(0)

    def last(self) -> None:
        for index in range(len(self.items) - 1, -1, -1):
            if self.items[index].enabled:
                self.selected = index
                return

    @property
    def current(self) -> MenuItem | None:
        if not self.items:
            return None
        item = self.items[self.selected]
        return item if item.enabled else None

    def activate(self) -> bool:
        """Run the selected item's action. False when nothing is selectable."""
        item = self.current
        if item is None:
            return False
        item.action()
        return True

    def activate_number(self, number: int) -> bool:
        """Select and run item `number` (counting from 1), as a digit key does."""
        index = number - 1
        if not 0 <= index < len(self.items) or not self.items[index].enabled:
            return False
        self.selected = index
        self.items[index].action()
        return True
