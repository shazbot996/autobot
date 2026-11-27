"""`autobot.agents.words`: the global action keywords and the shared word lists."""

import tomllib

from autobot.agents.words import action_keyword, match_word, stands_in
from autobot.config import parse_config

CONFIG_TEXT = """
[audio]
input_device = "pipewire"
output_device = "pipewire"

[[wake_phrases]]
phrase = "autobot"
kind = "text_match"
target = "ack"
"""


def test_cancel_stands_among_other_words_and_punctuation() -> None:
    for said in ("cancel", "Cancel!", "could you cancel", "please cancel that"):
        assert match_word(said, ["cancel"]) == "cancel", said


def test_a_word_that_only_begins_with_the_keyword_does_not_match() -> None:
    assert match_word("cancellation", ["cancel"]) is None
    assert match_word("the cancellations", ["cancel"]) is None
    # Nor one that ends with it.
    assert match_word("precancel", ["cancel"]) is None


def test_a_word_of_several_words_ignores_the_spaces_between_them() -> None:
    assert stands_in("sure, go ahead", "go ahead")
    assert stands_in("goahead", "go ahead")
    assert stands_in("I don't know", "don't")
    assert not stands_in("you betcha", "you bet")
    assert not stands_in("nothing", "no")


def test_the_first_word_of_the_list_that_stands_is_the_match() -> None:
    assert match_word("nope, no way", ["no way", "nope"]) == "no way"
    assert match_word("silence", ["no", "nope"]) is None
    assert match_word("anything", []) is None


def test_the_action_keywords_come_from_the_words_table() -> None:
    default = parse_config(tomllib.loads(CONFIG_TEXT))
    assert action_keyword("Cancel that, autobot.", default) == "cancel"
    assert action_keyword("what time is it", default) is None
    own = parse_config(tomllib.loads(CONFIG_TEXT + '\n[words]\nactions = ["abort"]\n'))
    assert action_keyword("abort", own) == "abort"
    assert action_keyword("cancel", own) is None
