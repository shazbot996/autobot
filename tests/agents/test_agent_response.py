"""`autobot.agents.response`: an answer in two parts, fitted to the panel (AG-4)."""

from autobot.agents.response import Response, layout, parse_model_response

ANSWER = """\
{"summary": "The moon is about 384,000 kilometres away.",
 "detail": ["Average: 384,400 km", "Closest: 363,300 km", "Farthest: 405,500 km"]}
"""


def test_a_json_answer_is_parsed_into_its_two_parts() -> None:
    assert parse_model_response(ANSWER) == Response(
        "The moon is about 384,000 kilometres away.",
        ("Average: 384,400 km", "Closest: 363,300 km", "Farthest: 405,500 km"),
    )


def test_a_fenced_json_answer_is_unwrapped() -> None:
    fenced = "Here you go:\n```json\n" + ANSWER + "```\n"
    assert parse_model_response(fenced) == parse_model_response(ANSWER)


def test_a_detail_given_as_one_string_is_split_into_lines() -> None:
    text = '{"summary": "Two lines.", "detail": "first\\nsecond"}'
    assert parse_model_response(text) == Response("Two lines.", ("first", "second"))


def test_a_plain_answer_still_gives_both_parts() -> None:
    text = (
        "The moon is about 384,000 kilometres away. It drifts about 3.8 cm "
        "further every year, because the tides slow the earth down."
    )
    response = parse_model_response(text)
    assert response.summary == "The moon is about 384,000 kilometres away."
    assert response.detail == (text,)
    # The whole text is folded into the panel.
    lines = layout(response, 5, 30)
    assert len(lines) == 5
    assert all(len(line) <= 30 for line in lines)
    assert " ".join(lines) == text


def test_json_of_another_shape_is_read_as_plain_text() -> None:
    text = '{"answer": "yes"}'
    assert parse_model_response(text) == Response(text, (text,))


def test_a_long_detail_is_laid_out_to_fit_the_panel() -> None:
    detail = tuple(
        f"line {number}: " + "a fairly long run of words " * 3
        for number in range(1, 13)
    )
    lines = layout(Response("Twelve lines.", detail), 5, 40)
    assert len(lines) == 5
    assert all(len(line) <= 40 for line in lines)
    # The first lines are kept: nothing is scrolled into.
    assert lines[0].startswith("line 1: ")


def test_a_word_longer_than_the_panel_is_cut_and_blank_lines_stay() -> None:
    lines = layout(Response("", ("x" * 12, "", "end")), 10, 5)
    assert lines == ["xxxxx", "xxxxx", "xx", "", "end"]


def test_an_empty_panel_shows_nothing() -> None:
    assert layout(Response("s", ("a",)), 0, 40) == []
    assert layout(Response("s", ("a",)), 5, 0) == []
