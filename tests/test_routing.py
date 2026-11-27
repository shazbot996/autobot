"""`autobot.routing`: the rules that hand a command after the wake to an agent."""

import pytest

from autobot.config import ROUTE_AGENTS, RouteConfig
from autobot.routing import AGENT_NOTES, commands_text, contains_command, match_route

ECHO = RouteConfig(command="echo", agent="echo")
LIST = RouteConfig(command="make a list", agent="echo")


@pytest.mark.parametrize(
    "transcript",
    ["echo", "Echo!", "please echo my voice", "Autobot, echo that back.", "echoes"],
)
def test_a_command_among_other_words_matches(transcript: str) -> None:
    assert contains_command(transcript, "echo")


@pytest.mark.parametrize("transcript", ["", "hey autobot", "techo", "eco"])
def test_words_without_the_command_do_not_match(transcript: str) -> None:
    assert not contains_command(transcript, "echo")


def test_spaces_inside_a_command_do_not_count() -> None:
    # The listener's rule (WW-6): whisper may split a word in two.
    assert contains_command("ech o", "echo")


def test_a_command_of_several_words_matches_across_punctuation() -> None:
    assert contains_command("Could you make a-list, please?", "make a list")
    assert not contains_command("make the list", "make a list")


def test_an_empty_command_never_matches() -> None:
    assert not contains_command("echo", " ")


def test_the_first_rule_that_matches_wins() -> None:
    match = match_route("make a list and echo it", [ECHO, LIST])
    assert match is not None
    assert (match.route, match.number) == (ECHO, 1)
    match = match_route("make a list and echo it", [LIST, ECHO])
    assert match is not None
    assert (match.route, match.number) == (LIST, 1)


def test_a_match_names_its_rule_and_the_words() -> None:
    match = match_route("  please   echo  ", [LIST, ECHO])
    assert match is not None
    assert match.number == 2
    assert match.transcript == "please echo"
    assert match.describe() == 'rule 2: "echo" -> echo agent'


def test_a_match_names_the_mode_of_its_rule() -> None:
    weather = RouteConfig("weather", "weather")
    forecast = RouteConfig("forecast", "weather", "forecast")
    match = match_route("what is the forecast", [ECHO, weather, forecast])
    assert match is not None
    assert match.route.mode == "forecast"
    assert match.describe() == 'rule 3: "forecast" -> weather agent (forecast)'


def test_every_agent_has_a_note() -> None:
    assert set(AGENT_NOTES) == set(ROUTE_AGENTS)
    assert all(AGENT_NOTES.values())


def test_no_rule_matching_is_none() -> None:
    assert match_route("what time is it", [ECHO]) is None
    assert match_route("echo", []) is None


def test_the_commands_read_as_a_sentence() -> None:
    assert commands_text([]) == ""
    assert commands_text([ECHO]) == '"echo"'
    assert commands_text([ECHO, LIST]) == '"echo" or "make a list"'
    three = [ECHO, LIST, RouteConfig("stop", "echo")]
    assert commands_text(three) == '"echo", "make a list" or "stop"'
