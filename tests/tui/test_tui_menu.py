"""`autobot.tui.menu`: the menu model."""

from functools import partial

from autobot.tui.menu import Menu, MenuItem


def menu(*enabled: bool) -> tuple[Menu, list[int]]:
    chosen: list[int] = []
    items = [
        MenuItem(f"item {number}", partial(chosen.append, number), enabled=on)
        for number, on in enumerate(enabled)
    ]
    return Menu(items), chosen


def test_movement_wraps_at_both_ends() -> None:
    model, _ = menu(True, True, True)
    model.move(-1)
    assert model.selected == 2
    model.move(1)
    assert model.selected == 0


def test_movement_skips_disabled_items() -> None:
    model, _ = menu(True, False, False, True)
    model.move(1)
    assert model.selected == 3
    model.move(1)
    assert model.selected == 0


def test_the_first_enabled_item_starts_selected() -> None:
    model, _ = menu(False, True)
    assert model.selected == 1


def test_activate_runs_the_selected_action() -> None:
    model, chosen = menu(True, True)
    model.move(1)
    assert model.activate()
    assert chosen == [1]


def test_a_number_selects_and_runs() -> None:
    model, chosen = menu(True, True, True)
    assert model.activate_number(3)
    assert (model.selected, chosen) == (2, [2])


def test_a_number_for_a_disabled_or_missing_item_does_nothing() -> None:
    model, chosen = menu(True, False)
    assert not model.activate_number(2)
    assert not model.activate_number(9)
    assert chosen == []


def test_nothing_is_selectable_when_everything_is_disabled() -> None:
    model, chosen = menu(False, False)
    model.move(1)
    assert model.current is None
    assert not model.activate()
    assert chosen == []


def test_replace_keeps_the_position_and_leaves_a_disabled_item() -> None:
    model, _ = menu(True, True, True)
    model.move(1)
    model.replace(
        [
            MenuItem("a", lambda: None),
            MenuItem("b", lambda: None, enabled=False),
            MenuItem("c", lambda: None),
        ]
    )
    assert model.selected == 2


def test_first_and_last() -> None:
    model, _ = menu(False, True, True, False)
    model.last()
    assert model.selected == 2
    model.first()
    assert model.selected == 1
