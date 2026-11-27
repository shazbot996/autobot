"""`autobot.agents.tree`: the root, the agents, the commands between them (AG-2)."""

import itertools
import tomllib

from autobot.agents.tree import (
    LEVEL_AGENTS,
    LEVEL_ROOT,
    MARK_ENTRY,
    MARK_REQUEST,
    Picture,
    Tree,
    build_tree,
    layout,
)
from autobot.config import ROUTE_AGENTS, Config, load_config, parse_config
from autobot.tui.ui import Glyphs

COMMITTED = load_config()

SMALL = """
[audio]
input_device = "pipewire"
output_device = "pipewire"

[[wake_phrases]]
phrase = "autobot"
kind = "text_match"
target = "ack"

[[routes]]
command = "question"
agent = "question"

[[routes]]
command = "echo"
agent = "echo"
"""


def small(extra: str = "") -> Config:
    return parse_config(tomllib.loads(SMALL + extra))


def pictures(tree: Tree) -> tuple[Picture, Picture]:
    return layout(tree, Glyphs.ascii()), layout(tree, Glyphs.unicode())


def test_the_root_is_the_wake_phrase_and_the_agents_stand_in_rule_order() -> None:
    tree = build_tree(COMMITTED)
    assert tree.root.name == "autobot"
    assert tree.root.root
    names = [node.name for node in tree.agents]
    assert names == ["question", "weather", "time", "camera", "introduce", "echo"]
    assert sorted(names) == sorted(ROUTE_AGENTS)
    assert tree.levels == ((tree.root,), tree.agents)


def test_two_rules_to_one_agent_are_two_edges_into_one_node() -> None:
    tree = build_tree(COMMITTED)
    weather = tree.node("weather")
    assert [edge.label for edge in tree.edges_to(weather)] == [
        "weather",
        "forecast (forecast)",
    ]
    assert [edge.command for edge in tree.edges_to(tree.node("camera"))] == [
        "camera",
        "cameras",
    ]
    assert len(tree.edges) == len(COMMITTED.routes)


def test_every_edge_label_stands_in_the_picture_where_it_says() -> None:
    tree = build_tree(COMMITTED)
    for picture in pictures(tree):
        for edge, (row, col) in picture.labels.items():
            assert picture.rows[row][col : col + len(edge.label)] == edge.label


def test_the_keywords_stand_beside_both_levels() -> None:
    tree = build_tree(COMMITTED)
    assert tree.keywords == ("cancel",)
    for picture in pictures(tree):
        assert set(picture.keywords) == {LEVEL_ROOT, LEVEL_AGENTS}
        for level, (row, col) in picture.keywords.items():
            assert picture.rows[row][col:].rstrip() == "[cancel]", level
        root_row = picture.keywords[LEVEL_ROOT][0]
        agents_row = picture.keywords[LEVEL_AGENTS][0]
        assert root_row == picture.centre(tree.root)[0]
        assert agents_row == picture.centre(tree.agents[0])[0]


def test_no_keywords_draws_none() -> None:
    picture = layout(build_tree(small("[words]\nactions = []\n")), Glyphs.ascii())
    assert picture.keywords == {}
    assert "[" not in "".join(picture.rows)


def test_a_confirmation_on_the_way_in_marks_its_node() -> None:
    plain = build_tree(small())
    assert not plain.node("question").confirm_entry
    marked = build_tree(small('[[agents]]\nname = "question"\nconfirm_entry = true\n'))
    question = marked.node("question")
    assert question.confirm_entry and not question.confirm_request
    for before, after in zip(pictures(plain), pictures(marked), strict=True):
        box = after.boxes[after.tree.node("question")]
        top = after.rows[box.y][box.x : box.right]
        assert MARK_ENTRY in top
        old = before.boxes[before.tree.node("question")]
        assert MARK_ENTRY not in before.rows[old.y][old.x : old.right]


def test_a_confirmation_before_a_request_carries_its_own_mark() -> None:
    tree = build_tree(
        small(
            '[[agents]]\nname = "question"\n'
            "confirm_entry = true\nconfirm_request = true\n"
        )
    )
    for picture in pictures(tree):
        box = picture.boxes[tree.node("question")]
        top = picture.rows[box.y][box.x : box.right]
        assert f"{MARK_ENTRY} {MARK_REQUEST}" in top
        # The marks leave the line that comes into the box where it is.
        _, col = picture.centre(tree.node("question"))
        assert top[col - box.x] not in "?$ "


def test_every_ascii_row_is_printable_ascii() -> None:
    picture = layout(build_tree(COMMITTED), Glyphs.ascii())
    for row in picture.rows:
        assert row.isascii() and row.isprintable(), row


def test_every_unicode_row_is_the_same_width_in_line_drawing() -> None:
    picture = layout(build_tree(COMMITTED), Glyphs.unicode())
    assert {len(row) for row in picture.rows} == {picture.width}
    text = "".join(picture.rows)
    assert "┌" in text and "│" in text and "┴" in text
    assert not any(char in text for char in "+-|")


def test_the_boxes_cover_the_text_of_each_name() -> None:
    tree = build_tree(COMMITTED)
    for picture in pictures(tree):
        assert set(picture.boxes) == {tree.root, *tree.agents}
        for node, box in picture.boxes.items():
            inside = picture.rows[box.y + 1][box.x : box.right]
            assert node.name in inside, node.name
            row, col = picture.centre(node)
            assert row == box.y + 1
            assert box.x < col < box.right - 1


def test_a_path_runs_one_cell_at_a_time_from_the_root_to_an_agent() -> None:
    tree = build_tree(COMMITTED)
    for picture in pictures(tree):
        for node in tree.agents:
            path = picture.path(tree.root, node)
            assert path[0] == picture.centre(tree.root)
            assert path[-1] == picture.centre(node)
            for (r1, c1), (r2, c2) in itertools.pairwise(path):
                assert abs(r1 - r2) + abs(c1 - c2) == 1
            # Every cell between the boxes is on a line or a label.
            for row, col in path:
                assert picture.rows[row][col] != " " or any(
                    box.y <= row < box.bottom for box in picture.boxes.values()
                )
            assert picture.path(node, tree.root) == tuple(reversed(path))


def test_a_path_between_two_agents_goes_up_through_the_root() -> None:
    tree = build_tree(COMMITTED)
    picture = layout(tree, Glyphs.ascii())
    weather, time = tree.node("weather"), tree.node("time")
    path = picture.path(weather, time)
    assert picture.centre(tree.root) in path
    assert path[0] == picture.centre(weather)
    assert path[-1] == picture.centre(time)
    assert picture.path(time, time) == (picture.centre(time),)


def test_the_picture_is_as_wide_as_the_agents_need() -> None:
    tree = build_tree(COMMITTED)
    picture = layout(tree, Glyphs.ascii())
    widest = sum(box.width for node, box in picture.boxes.items() if not node.root)
    assert picture.width > widest
    assert picture.height == len(picture.rows)


def test_a_model_behind_an_agent_comes_from_the_registry() -> None:
    tree = build_tree(COMMITTED)
    assert not tree.node("echo").uses_model
