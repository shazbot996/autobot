"""`autobot.tui.face_reactions`: the eyes' poses, reactions as data, and the animator."""

import random
from typing import Any

import pytest

from autobot.tui.face_reactions import (
    BUILTIN_REACTIONS,
    BUILTIN_SET,
    EYE_ROWS,
    LEVELS,
    LIDS,
    REACTION_NAMES,
    Eyes,
    Pose,
    ReactionError,
    draw_eyes,
    reaction_set,
)
from autobot.tui.ui import Glyphs

ACROSS = ("left", "center", "right")
# The interface draws about ten frames a second.
FRAME = 0.1


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def make_eyes(seed: int = 7) -> tuple[Eyes, FakeClock]:
    clock = FakeClock()
    return Eyes(clock, random.Random(seed)), clock


def poses_over(eyes: Eyes, clock: FakeClock, seconds: float) -> list[Pose]:
    shown = []
    for _ in range(round(seconds / FRAME)):
        shown.append(eyes.pose())
        clock.now += FRAME
    return shown


# --- reactions as data -----------------------------------------------------


def test_the_built_in_set_defines_every_reaction_the_face_needs() -> None:
    assert set(REACTION_NAMES) <= set(BUILTIN_SET)
    assert BUILTIN_SET == reaction_set(BUILTIN_REACTIONS)


def test_a_pose_reads_its_pupil_words_in_either_order() -> None:
    reactions = reaction_set(
        {
            "look": {
                "poses": [
                    {"pupil": "up left", "hold": 100},
                    {"pupil": "right down", "lid": "half", "hold": [200, 400]},
                    {"hold": 50},
                ]
            }
        }
    )
    first, second, third = reactions["look"].poses
    assert first == Pose("left", "up", "open", (100, 100))
    assert second == Pose("right", "down", "half", (200, 400))
    assert third == Pose("center", "middle", "open", (50, 50))
    assert not reactions["look"].loop
    assert not reactions["look"].random


@pytest.mark.parametrize(
    ("data", "named"),
    [
        ({"pupil": "sideways", "hold": 100}, "'sideways'"),
        ({"pupil": "left right", "hold": 100}, "'right'"),
        ({"lid": "squint", "hold": 100}, "'squint'"),
        ({"pupil": "left"}, "no hold"),
        ({"pupil": "left", "hold": [300, 100]}, "hold"),
        ({"pupil": "left", "hold": 0}, "hold"),
        ({"pupil": "left", "hold": True}, "hold"),
        ({"pupil": "left", "hold": 100, "brow": "up"}, "'brow'"),
    ],
)
def test_an_unknown_pose_word_is_a_one_line_error(
    data: dict[str, Any], named: str
) -> None:
    with pytest.raises(ReactionError) as raised:
        reaction_set({"odd": {"poses": [data]}})
    message = str(raised.value)
    assert "\n" not in message
    assert "'odd'" in message
    assert named in message


@pytest.mark.parametrize(
    "data",
    [
        {"poses": []},
        {"loop": True},
        {"poses": [{"hold": 100}], "loop": "yes"},
        {"poses": [{"hold": 100}], "speed": 2},
        "wide",
    ],
)
def test_a_malformed_reaction_is_a_one_line_error(data: Any) -> None:
    with pytest.raises(ReactionError) as raised:
        reaction_set({"odd": data})
    assert "\n" not in str(raised.value)


# --- the animator ------------------------------------------------------------


def test_idle_looks_around_and_blinks_within_30_seconds() -> None:
    eyes, clock = make_eyes()
    assert eyes.name == "idle"
    shown = poses_over(eyes, clock, 30.0)
    looks = {(pose.across, pose.level) for pose in shown if pose.lid != "closed"}
    assert len(looks) >= 3
    assert any(pose.lid == "closed" for pose in shown)
    assert eyes.name == "idle"


def test_idle_holds_its_glances_for_a_second_or_more() -> None:
    eyes, clock = make_eyes()
    shown = poses_over(eyes, clock, 30.0)
    # Runs of one pose: glances last 0.8 to 3 s, a blink a frame or two.
    runs: list[tuple[Pose, int]] = []
    for pose in shown:
        if runs and runs[-1][0] is pose:
            runs[-1] = (pose, runs[-1][1] + 1)
        else:
            runs.append((pose, 1))
    for pose, frames in runs[:-1]:
        if pose.lid == "closed":
            assert frames <= 2
        else:
            assert 8 <= frames <= 31


def test_wake_changes_the_pose_on_the_very_next_call() -> None:
    eyes, clock = make_eyes()
    poses_over(eyes, clock, 2.0)
    before = eyes.pose()
    eyes.play("wake")
    after = eyes.pose()
    assert after != before
    assert after == BUILTIN_SET["wake"].poses[0]
    assert (after.across, after.level, after.lid) == ("center", "middle", "wide")


def test_a_reaction_that_does_not_loop_returns_to_idle() -> None:
    eyes, clock = make_eyes()
    eyes.play("done")
    assert eyes.pose().lid == "closed"
    clock.now += 0.2
    assert eyes.pose() == BUILTIN_SET["done"].poses[1]
    # It holds the last pose for its time, then looks around again.
    clock.now += 1.1
    assert eyes.name == "done"
    eyes.pose()
    clock.now += 0.2
    eyes.pose()
    assert eyes.name == "idle"
    assert eyes.pose() == BUILTIN_SET["idle"].poses[0]


def test_a_looping_reaction_keeps_playing() -> None:
    eyes, clock = make_eyes()
    eyes.play("working")
    shown = poses_over(eyes, clock, 10.0)
    assert eyes.name == "working"
    assert {(pose.across, pose.level) for pose in shown} == {
        ("left", "up"),
        ("right", "up"),
    }


def test_a_set_that_defines_only_wake_still_plays_the_built_in_done() -> None:
    own = reaction_set(
        {"wake": {"poses": [{"pupil": "up", "lid": "wide", "hold": 500}]}}
    )
    eyes, clock = make_eyes()
    eyes.play("wake", own)
    assert eyes.pose() == Pose("center", "up", "wide", (500, 500))
    eyes.play("done", own)
    assert eyes.name == "done"
    assert eyes.pose() == BUILTIN_SET["done"].poses[0]
    # It hands back to the built-in idle too, since the set has none.
    poses_over(eyes, clock, 2.0)
    assert eyes.name == "idle"


def test_a_sets_own_idle_is_what_its_reactions_return_to() -> None:
    own = reaction_set(
        {
            "idle": {"loop": True, "poses": [{"pupil": "down", "hold": 400}]},
            "wake": {"poses": [{"lid": "wide", "hold": 300}]},
        }
    )
    eyes, clock = make_eyes()
    eyes.play("wake", own)
    poses_over(eyes, clock, 1.0)
    assert eyes.name == "idle"
    assert eyes.pose() == Pose("center", "down", "open", (400, 400))


def test_a_name_no_set_defines_looks_around() -> None:
    eyes, _ = make_eyes()
    eyes.play("dance")
    assert eyes.name == "idle"
    assert eyes.pose() == BUILTIN_SET["idle"].poses[0]


def test_a_random_reaction_never_shows_one_entry_twice_running() -> None:
    eyes, clock = make_eyes(seed=3)
    entries = BUILTIN_SET["idle"].poses
    previous = None
    for _ in range(300):
        pose = eyes.pose()
        index = eyes._index
        if previous is not None and pose is not entries[previous]:
            assert index != previous
        previous = index
        clock.now += FRAME


# --- the drawing ---------------------------------------------------------------


def every_pose() -> list[Pose]:
    return [
        Pose(across, level, lid)
        for across in ACROSS
        for level in LEVELS
        for lid in LIDS
    ]


@pytest.mark.parametrize("glyphs", [Glyphs.unicode(), Glyphs.ascii()])
def test_every_pose_draws_two_eyes_of_one_size(glyphs: Glyphs) -> None:
    for pose in every_pose():
        rows = draw_eyes(pose, glyphs)
        assert len(rows) == EYE_ROWS
        assert len({len(row) for row in rows}) == 1
        width = len(rows[0])
        half = (width - 3) // 2
        # The two eyes are the same.
        assert [row[:half] for row in rows] == [row[-half:] for row in rows]


def test_the_ascii_drawing_is_printable_ascii_only() -> None:
    for pose in every_pose():
        for row in draw_eyes(pose, Glyphs.ascii()):
            assert row.isascii()
            assert row.isprintable()


def test_the_unicode_drawing_uses_rounded_corners() -> None:
    rows = draw_eyes(Pose(), Glyphs.unicode())
    assert rows[0].startswith("╭") and rows[-1].startswith("╰")


def test_the_pupil_moves_with_the_pose() -> None:
    glyphs = Glyphs.ascii()
    drawings = {
        (across, level): draw_eyes(Pose(across, level), glyphs)
        for across in ACROSS
        for level in LEVELS
    }
    assert len({tuple(rows) for rows in drawings.values()}) == 9
    assert draw_eyes(Pose(), glyphs)[1:3] == ["( . )   ( . )", "( ' )   ( ' )"]
    assert draw_eyes(Pose("left", "up"), glyphs)[1:3] == [
        "(o  )   (o  )",
        "(   )   (   )",
    ]


def test_the_lids_draw_differently() -> None:
    for glyphs in (Glyphs.unicode(), Glyphs.ascii()):
        drawings = {tuple(draw_eyes(Pose(lid=lid), glyphs)) for lid in LIDS}
        assert len(drawings) == len(LIDS)
