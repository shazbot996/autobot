"""`autobot.tui.tree_game`: the protagonist's travels over the map (AG-3)."""

import itertools

import pytest

from autobot.agents.tree import LEVEL_AGENTS, LEVEL_ROOT, Picture, build_tree, layout
from autobot.config import load_config
from autobot.tui.face_reactions import ACROSS, LEVELS, LIDS, Pose
from autobot.tui.tree_game import (
    PHASE_COMMAND,
    PHASE_CONFIRMING,
    PHASE_DONE,
    PHASE_LISTENING,
    PHASE_RUNNING,
    PHASE_WAKE,
    PROTAGONIST_ROWS,
    PROTAGONIST_WIDTH,
    TRAVEL_SECONDS,
    Game,
    Position,
    draw_protagonist,
)
from autobot.tui.ui import Glyphs

START = 100.0
# A panel of the face at 72x22, about.
ROWS, COLS = 10, 40

LISTENING = Position(LEVEL_ROOT, "", PHASE_LISTENING)
WAKE = Position(LEVEL_AGENTS, "", PHASE_WAKE)
WEATHER = Position(LEVEL_AGENTS, "weather", PHASE_COMMAND)
CONFIRMING = Position(LEVEL_AGENTS, "weather", PHASE_CONFIRMING)
RUNNING = Position(LEVEL_AGENTS, "weather", PHASE_RUNNING)
DONE = Position(LEVEL_ROOT, "", PHASE_DONE)


@pytest.fixture(params=[Glyphs.ascii(), Glyphs.unicode()], ids=["ascii", "unicode"])
def picture(request: pytest.FixtureRequest) -> Picture:
    glyphs: Glyphs = request.param
    return layout(build_tree(load_config()), glyphs)


def centred(game: Game, now: float) -> tuple[int, int]:
    """The map cell drawn at the panel's centre at `now`."""
    row, col = game.offset(now, ROWS, COLS)
    return (row + ROWS // 2, col + COLS // 2)


def test_listening_centres_the_root(picture: Picture) -> None:
    game = Game(picture)
    root = picture.tree.root
    game.follow(LISTENING, START)
    assert game.cell(START) == picture.centre(root)
    assert centred(game, START) == picture.centre(root)
    assert game.highlight(START) == "autobot"
    assert not game.level_view(START)


def test_a_wake_takes_him_down_to_the_agents_level_in_a_travel(
    picture: Picture,
) -> None:
    game = Game(picture)
    root_row = picture.centre(picture.tree.root)[0]
    agents_row = picture.centre(picture.tree.agents[0])[0]
    game.follow(WAKE, START)
    halfway = game.cell(START + TRAVEL_SECONDS / 2)
    assert root_row < halfway[0] < agents_row
    assert game.highlight(START + TRAVEL_SECONDS / 2) == ""
    there = game.cell(START + TRAVEL_SECONDS)
    assert there[0] == agents_row
    assert centred(game, START + TRAVEL_SECONDS) == there
    # On the level, before a command, no node is his.
    assert game.highlight(START + TRAVEL_SECONDS) == ""


def test_a_command_takes_him_to_the_agents_box(picture: Picture) -> None:
    game = Game(picture)
    weather = picture.tree.node("weather")
    game.follow(WAKE, START)
    later = START + TRAVEL_SECONDS
    game.follow(WEATHER, later)
    assert game.highlight(later + TRAVEL_SECONDS / 2) == ""
    end = later + TRAVEL_SECONDS
    assert game.cell(end) == picture.centre(weather)
    assert centred(game, end) == picture.centre(weather)
    assert game.highlight(end) == "weather"
    # He went up the line to the edges and down weather's: along the tree.
    for (r1, c1), (r2, c2) in itertools.pairwise(game.route):
        assert abs(r1 - r2) + abs(c1 - c2) == 1


def test_running_opens_the_level_and_done_returns_him_to_the_root(
    picture: Picture,
) -> None:
    game = Game(picture)
    now = START
    for position in (WAKE, WEATHER, CONFIRMING):
        game.follow(position, now)
        now += TRAVEL_SECONDS
    assert not game.level_view(now)
    assert game.highlight(now) == "weather"
    game.follow(RUNNING, now)
    assert game.level_view(now)
    assert game.cell(now) == picture.centre(picture.tree.node("weather"))
    game.follow(DONE, now + 1)
    assert not game.level_view(now + 1)
    end = now + 1 + TRAVEL_SECONDS
    assert game.cell(end) == picture.centre(picture.tree.root)
    assert game.highlight(end) == "autobot"


def test_the_level_waits_for_him_to_reach_the_node(picture: Picture) -> None:
    game = Game(picture)
    game.follow(WAKE, START)
    later = START + TRAVEL_SECONDS
    # The agent starts at once, as the echo does: he still slides there first.
    game.follow(WEATHER, later)
    game.follow(RUNNING, later)
    assert not game.level_view(later + TRAVEL_SECONDS / 2)
    assert game.level_view(later + TRAVEL_SECONDS)


def test_a_change_mid_travel_starts_from_where_he_stands(picture: Picture) -> None:
    game = Game(picture)
    game.follow(WAKE, START)
    middle = START + TRAVEL_SECONDS / 2
    here = game.cell(middle)
    game.follow(WEATHER, middle)
    assert game.route[0] == here
    assert game.cell(middle) == here
    assert game.cell(middle + TRAVEL_SECONDS) == picture.centre(
        picture.tree.node("weather")
    )


def test_no_frame_moves_him_more_than_one_travel(picture: Picture) -> None:
    game = Game(picture)
    changes = {
        0: WAKE,
        3: WEATHER,
        5: LISTENING,
        7: Position(LEVEL_AGENTS, "echo", PHASE_COMMAND),
        12: DONE,
    }
    frame = 0.05
    previous = game.cell(START)
    for step in range(30):
        now = START + step * frame
        if step in changes:
            here = game.cell(now)
            game.follow(changes[step], now)
            assert game.route[0] == here
        cell = game.cell(now)
        moved = abs(cell[0] - previous[0]) + abs(cell[1] - previous[1])
        assert moved <= len(game.route) - 1
        for (r1, c1), (r2, c2) in itertools.pairwise(game.route):
            assert abs(r1 - r2) + abs(c1 - c2) == 1
        previous = cell
    assert previous == picture.centre(picture.tree.root)


def test_the_same_place_starts_no_travel(picture: Picture) -> None:
    game = Game(picture)
    game.follow(WAKE, START)
    route = game.route
    game.follow(Position(LEVEL_AGENTS, "", PHASE_WAKE), START + 0.1)
    assert game.route == route
    assert game.cell(START + TRAVEL_SECONDS) == route[-1]


def test_an_agent_the_tree_does_not_have_stays_on_the_level(picture: Picture) -> None:
    game = Game(picture)
    game.follow(Position(LEVEL_AGENTS, "nobody", PHASE_COMMAND), START)
    end = START + TRAVEL_SECONDS
    assert game.cell(end)[0] == picture.centre(picture.tree.agents[0])[0]
    assert game.highlight(end) == ""


@pytest.mark.parametrize("glyphs", [Glyphs.ascii(), Glyphs.unicode()])
def test_the_protagonist_is_two_rows_of_the_faces_eyes(glyphs: Glyphs) -> None:
    for across, level, lid in itertools.product(ACROSS, LEVELS, LIDS):
        rows = draw_protagonist(Pose(across, level, lid), glyphs)
        assert len(rows) == PROTAGONIST_ROWS
        assert {len(row) for row in rows} == {PROTAGONIST_WIDTH}
        if not glyphs.block_art:
            assert all(row.isascii() and row.isprintable() for row in rows)


def test_the_protagonists_eyes_follow_the_pose() -> None:
    ascii_glyphs = Glyphs.ascii()
    assert draw_protagonist(Pose("center"), ascii_glyphs) == [" .-..-. ", " (o)(o) "]
    assert draw_protagonist(Pose("left"), ascii_glyphs)[1] == "(o)(o)  "
    assert draw_protagonist(Pose("right"), ascii_glyphs)[1] == "  (o)(o)"
    assert draw_protagonist(Pose(lid="closed"), ascii_glyphs)[1] == " (-)(-) "
    unicode_rows = draw_protagonist(Pose(), Glyphs.unicode())
    assert unicode_rows[0] == " ╭─╮╭─╮ "
    assert unicode_rows[1].strip() == "│█││█│"
