"""`autobot.wakeword.script`: the recording session's prompts."""

from collections import Counter

from autobot.wakeword.data import PhraseTable, phrase_table
from autobot.wakeword.dataset import LABEL_AMBIENT, LABEL_NEGATIVE, LABEL_POSITIVE
from autobot.wakeword.script import (
    AMBIENT_FLOOR_SECONDS,
    AMBIENT_PROMPTS,
    AMBIENT_SECONDS,
    HARD_NEGATIVES,
    LONG_AMBIENT_ID,
    LONG_AMBIENT_MIN_SECONDS,
    LONG_AMBIENT_SECONDS,
    POSITIVE_COUNTS,
    SENTENCE_NEGATIVES,
    Prompt,
    build_script,
)

TABLE = phrase_table("autobot")


def script() -> list[Prompt]:
    return build_script("autobot", TABLE)


def kinds(prompts: list[Prompt]) -> Counter[str]:
    return Counter(prompt.id.rsplit("-", 1)[0] for prompt in prompts)


def test_the_script_is_deterministic_and_its_ids_are_unique() -> None:
    first, second = script(), script()
    assert first == second
    ids = [prompt.id for prompt in first]
    assert len(set(ids)) == len(ids)
    # The split hashes the identifier, and the file name is built from it.
    assert all(len(identifier) <= 20 for identifier in ids)


def test_it_starts_with_the_bare_phrase() -> None:
    first = script()[0]
    assert (first.id, first.text, first.label) == ("bare-01", "autobot", LABEL_POSITIVE)
    assert first.phrase_last and not first.optional


def test_the_positives_cover_every_delivery_the_spec_names() -> None:
    positives = [prompt for prompt in script() if prompt.label == LABEL_POSITIVE]
    assert sum(POSITIVE_COUNTS.values()) == 56
    assert len(positives) == 56
    assert kinds(positives) == Counter(POSITIVE_COUNTS)
    assert Counter(POSITIVE_COUNTS) == Counter(
        {
            "bare": 10,
            "hey": 8,
            "yo": 8,
            "sentence": 8,
            "slow": 4,
            "fast": 4,
            "quiet": 4,
            "raised": 4,
            "near": 3,
            "far": 3,
        }
    )
    by_kind = {prompt.id.rsplit("-", 1)[0]: prompt for prompt in positives}
    assert by_kind["hey"].text == "hey autobot"
    assert by_kind["yo"].text == "yo autobot"
    for kind in ("slow", "fast", "quiet", "raised", "near", "far"):
        assert by_kind[kind].text == "autobot"
        assert by_kind[kind].instruction != by_kind["bare"].instruction
    assert "slowly" in by_kind["slow"].instruction
    assert "microphone" in by_kind["near"].instruction
    assert all("autobot" in prompt.text for prompt in positives)


def test_the_phrase_is_not_last_only_in_the_sentence_prompts() -> None:
    positives = [prompt for prompt in script() if prompt.label == LABEL_POSITIVE]
    sentences = [prompt for prompt in positives if prompt.id.startswith("sentence-")]
    assert len({prompt.text for prompt in sentences}) == 8
    for prompt in positives:
        ends_with_phrase = prompt.text.endswith("autobot")
        assert prompt.phrase_last == ends_with_phrase, prompt
        assert prompt.phrase_last != prompt.id.startswith("sentence-")
    assert any(prompt.text.startswith("hey autobot, ") for prompt in sentences)


def test_the_sound_alikes_start_with_the_nearest_ones() -> None:
    hard = [prompt for prompt in script() if prompt.id.startswith("hard-")]
    assert len(hard) == HARD_NEGATIVES == 20
    assert all(prompt.label == LABEL_NEGATIVE for prompt in hard)
    texts = [prompt.text for prompt in hard]
    assert len(set(texts)) == 20
    nearest = len(TABLE.train_excluded)
    assert set(texts[:nearest]) == set(TABLE.train_excluded)
    # The rest are the first distinct trained ones, in the table's order.
    rest = [
        text for text in dict.fromkeys(TABLE.trained) if text not in texts[:nearest]
    ]
    assert texts[nearest:] == rest[: 20 - nearest]
    assert not set(texts) & set(TABLE.eval_only)
    assert "autobot" not in texts


def test_ten_ordinary_sentences_follow() -> None:
    speech = [prompt for prompt in script() if prompt.id.startswith("speech-")]
    assert len(speech) == SENTENCE_NEGATIVES == 10
    assert all(prompt.label == LABEL_NEGATIVE for prompt in speech)
    assert all("autobot" not in prompt.text.lower() for prompt in speech)


def test_five_minutes_of_ambient_then_the_optional_long_prompt() -> None:
    prompts = script()
    ambient = [prompt for prompt in prompts if prompt.label == LABEL_AMBIENT]
    floor, long = ambient[:-1], ambient[-1]
    assert len(floor) == AMBIENT_PROMPTS == 5
    assert all(prompt.seconds == AMBIENT_SECONDS == 60 for prompt in floor)
    assert all(prompt.text == "say nothing" and prompt.hands_off for prompt in floor)
    assert not any(prompt.optional or prompt.min_seconds for prompt in floor)
    assert sum(prompt.seconds for prompt in floor) == AMBIENT_FLOOR_SECONDS == 300
    # The long prompt is the last one, and the only optional one.
    assert prompts[-1] is long
    assert [prompt for prompt in prompts if prompt.optional] == [long]
    assert (long.id, long.text) == (LONG_AMBIENT_ID, "leave it running")
    assert long.instruction == "Leave it running (60 min, optional)"
    assert (long.seconds, long.min_seconds) == (3600, 1800)
    assert (LONG_AMBIENT_SECONDS, LONG_AMBIENT_MIN_SECONDS) == (3600, 1800)


def test_the_order_is_positives_negatives_ambient() -> None:
    labels = [prompt.label for prompt in script()]
    assert labels == (
        [LABEL_POSITIVE] * 56 + [LABEL_NEGATIVE] * 30 + [LABEL_AMBIENT] * 6
    )


def test_every_instruction_fits_the_smallest_terminal() -> None:
    # 72 columns less the frame and a margin on each side.
    assert all(len(prompt.instruction) <= 68 for prompt in script())
    assert all(prompt.instruction for prompt in script())


def test_a_phrase_without_a_table_still_gets_a_script() -> None:
    table = phrase_table("hello computer")
    prompts = build_script("Hello  Computer", table)
    assert prompts[0].text == "hello computer"
    hard = [prompt.text for prompt in prompts if prompt.id.startswith("hard-")]
    assert hard[:2] == ["hello", "computer"]
    assert len(hard) == len(set(hard)) <= HARD_NEGATIVES
    assert prompts[-1].optional


def test_a_table_with_few_sound_alikes_gives_as_many_as_it_has() -> None:
    table = PhraseTable(
        spellings=("zed",), hard=("said", "zed", "said"), eval_only=frozenset()
    )
    hard = [p.text for p in build_script("zed", table) if p.id.startswith("hard-")]
    assert hard == ["said"]
