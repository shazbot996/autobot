"""`autobot.tui.face_ticker`: the face's ticker, fed recorded event lists."""

from pathlib import Path
from typing import Any

import pytest

from autobot.tui.face_ticker import (
    DOWN_TEXT,
    GAP,
    Cell,
    Ticker,
    phrase_span,
)

AT = "2026-11-27T05:30:00.000+00:00"
WIDTH = 40


def transcript(text: str, stretch: int = 1, phrase: str = "autobot") -> dict[str, Any]:
    return {
        "event": "transcript",
        "phrase": phrase,
        "kind": "text_match",
        "text": text,
        "stretch": stretch,
        "at": AT,
    }


def score(
    stretch: int = 1,
    kind: str = "text_match",
    phrase: str = "autobot",
    value: float | None = 0.8,
) -> dict[str, Any]:
    return {
        "event": "score",
        "phrase": phrase,
        "kind": kind,
        "score": value,
        "stretch": stretch,
        "at": AT,
    }


def wake(phrase: str = "autobot", kind: str = "text_match") -> dict[str, Any]:
    return {
        "event": "wake",
        "phrase": phrase,
        "target": "ack",
        "kind": kind,
        "score": 0.8,
        "at": AT,
    }


def shown(cells: list[Cell]) -> str:
    return "".join(cell.char for cell in cells)


def popped(cells: list[Cell]) -> str:
    return "".join(cell.char for cell in cells if cell.wake)


def run(ticker: Ticker, frames: int, width: int = WIDTH) -> list[Cell]:
    for _ in range(frames):
        ticker.advance()
    return ticker.view(width)


# --- phrase_span -------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "phrase", "found"),
    [
        ("Hey autobot.", "autobot", "autobot"),
        ("Hey Autobot!", "autobot", "Autobot"),
        ("yo auto-bot, you there", "autobot", "auto-bot"),
        ("Hey auto bot", "autobot", "auto bot"),
        ("Autobots, roll out", "autobot", "Autobot"),
        ("Autobot's here", "autobot's", "Autobot's"),
        ("hey computer", "hey computer", "hey computer"),
    ],
)
def test_phrase_span_finds_the_phrase_by_the_containment_rule(
    text: str, phrase: str, found: str
) -> None:
    span = phrase_span(text, phrase)
    assert span is not None
    assert text[span[0] : span[1]] == found


@pytest.mark.parametrize(
    ("text", "phrase"),
    [
        ("Hey auto.", "autobot"),
        ("robotautobot", "autobot"),
        ("anything", ""),
        ("anything", "?!"),
    ],
)
def test_phrase_span_is_none_without_a_match_at_a_word_start(
    text: str, phrase: str
) -> None:
    assert phrase_span(text, phrase) is None


# --- scrolling and the wake pop --------------------------------------------


def test_a_transcript_scrolls_in_from_the_right() -> None:
    ticker = Ticker()
    assert shown(ticker.view(WIDTH)) == " " * WIDTH
    ticker.feed([transcript("hey autobot")])
    assert shown(ticker.view(WIDTH)) == " " * WIDTH
    cells = run(ticker, 1)
    assert shown(cells) == " " * (WIDTH - 1) + "h"
    cells = run(ticker, 3)
    assert shown(cells) == " " * (WIDTH - 4) + "hey "
    cells = run(ticker, len("hey autobot") - 4)
    assert shown(cells) == " " * (WIDTH - 11) + "hey autobot"


def test_the_wake_phrase_of_hey_autobot_pops() -> None:
    ticker = Ticker()
    ticker.feed([transcript("hey autobot"), score(), wake()])
    cells = run(ticker, len("hey autobot"))
    assert shown(cells).endswith("hey autobot")
    assert popped(cells) == "autobot"
    assert [cell.wake for cell in cells[-11:]] == [False] * 4 + [True] * 7


def test_a_wake_in_a_later_poll_still_pops_its_stretch() -> None:
    ticker = Ticker()
    ticker.feed([transcript("hey autobot", stretch=3)])
    run(ticker, 5)
    ticker.feed([score(3), wake()])
    assert popped(run(ticker, 6)) == "autobot"


def test_a_wake_pops_the_newest_item_of_the_current_stretch_only() -> None:
    ticker = Ticker()
    ticker.feed([transcript("autobot is a word", stretch=1), score(1)])
    ticker.feed([transcript("hey autobot", stretch=2), score(2), wake()])
    cells = run(ticker, 60, width=60)
    assert "autobot is a word" in shown(cells)
    assert popped(cells) == "autobot"
    # The pop is the second item's word, at the right end.
    assert shown(cells).rstrip().endswith("hey autobot")
    wake_columns = [index for index, cell in enumerate(cells) if cell.wake]
    assert shown(cells)[wake_columns[0] - 4 : wake_columns[0]] == "hey "


def test_a_transcript_without_the_phrase_pops_whole() -> None:
    ticker = Ticker()
    ticker.feed([transcript("or to bolt"), score(), wake()])
    assert popped(run(ticker, 20)) == "or to bolt"


def test_items_are_a_gap_apart_and_scroll_off_the_left() -> None:
    ticker = Ticker()
    ticker.feed([transcript("one", stretch=1), transcript("two", stretch=2)])
    assert ticker.text == "one" + " " * GAP + "two"
    cells = run(ticker, 3 + GAP + 3)
    assert shown(cells).endswith("one" + " " * GAP + "two")
    cells = run(ticker, WIDTH)
    assert shown(cells) == " " * WIDTH
    assert ticker.text == ""


def test_an_empty_transcript_adds_nothing() -> None:
    ticker = Ticker()
    ticker.feed([transcript("   "), transcript("")])
    assert ticker.text == ""


def test_two_text_match_phrases_writing_the_same_words_make_one_item() -> None:
    ticker = Ticker()
    ticker.feed(
        [
            transcript("hey autobot", phrase="autobot"),
            transcript("hey autobot", phrase="hey autobot"),
            score(phrase="autobot"),
            score(phrase="hey autobot"),
            wake("hey autobot"),
        ]
    )
    assert ticker.text == "hey autobot"
    assert popped(run(ticker, 11)) == "hey autobot"


# --- stretch markers -------------------------------------------------------


def test_a_sherpa_kws_stretch_shows_one_marker_popped_on_its_wake() -> None:
    ticker = Ticker(marker="●")
    ticker.feed([score(1, kind="sherpa_kws", value=0.3)])
    cells = run(ticker, 1)
    assert shown(cells) == " " * (WIDTH - 1) + "●"
    assert popped(cells) == ""
    ticker.feed([score(2, kind="sherpa_kws"), wake(kind="sherpa_kws")])
    cells = run(ticker, 1 + GAP)
    assert shown(cells).count("●") == 2
    assert popped(cells) == "●"
    # The popped marker is the second stretch's, the newer one.
    assert cells[-1] == Cell("●", True)


def test_two_marker_phrases_share_one_marker_per_stretch() -> None:
    ticker = Ticker()
    ticker.feed(
        [
            score(1, kind="openwakeword", phrase="autobot"),
            score(1, kind="sherpa_kws", phrase="computer"),
            wake("computer", kind="sherpa_kws"),
        ]
    )
    assert ticker.text == "*"
    assert popped(run(ticker, 1)) == "*"


def test_words_take_precedence_over_a_marker() -> None:
    ticker = Ticker()
    ticker.feed(
        [
            transcript("hey autobot", phrase="autobot"),
            score(1, kind="text_match", phrase="autobot"),
            score(1, kind="openwakeword", phrase="robot"),
        ]
    )
    assert ticker.text == "hey autobot"


def test_a_text_match_score_without_a_transcript_shows_no_marker() -> None:
    ticker = Ticker()
    ticker.feed([score(1, kind="text_match", value=None)])
    assert ticker.text == ""


def test_a_restarted_listener_does_not_pop_an_earlier_runs_item() -> None:
    ticker = Ticker()
    ticker.feed([score(5, kind="openwakeword"), score(6, kind="openwakeword")])
    ticker.feed([transcript("hello", stretch=1), score(1), wake()])
    assert popped(run(ticker, 20)) == "hello"
    assert ticker.text.count("*") == 2


# --- muted and down ---------------------------------------------------------


def test_nothing_is_added_while_muted() -> None:
    ticker = Ticker()
    ticker.muted = True
    ticker.feed([transcript("hey autobot"), score(1, kind="openwakeword"), wake()])
    assert ticker.text == ""
    assert shown(run(ticker, 20)) == " " * WIDTH
    ticker.muted = False
    ticker.feed([transcript("back again", stretch=2)])
    assert ticker.text == "back again"


def test_the_down_sentence_shows_and_does_not_scroll() -> None:
    ticker = Ticker()
    ticker.feed([transcript("hey autobot")])
    run(ticker, 5)
    ticker.down = True
    cells = ticker.view(60)
    assert shown(cells) == DOWN_TEXT.ljust(60)
    assert popped(cells) == ""
    assert shown(run(ticker, 10, width=60)) == DOWN_TEXT.ljust(60)
    assert shown(ticker.view(20)) == DOWN_TEXT[:20]
    ticker.down = False
    assert ticker.text == ""
    assert shown(ticker.view(60)) == " " * 60


# --- the backlog ------------------------------------------------------------


def test_a_long_feed_leaves_no_more_than_one_width_of_backlog() -> None:
    ticker = Ticker()
    ticker.view(WIDTH)
    ticker.feed(
        [transcript(f"sentence number {n} of many", stretch=n) for n in range(1, 30)]
    )
    assert ticker.waiting <= WIDTH
    for _ in range(200):
        ticker.advance()
        assert ticker.waiting <= WIDTH
    # What is on the strip is at most what is on show and what waits.
    assert len(ticker.text) <= 3 * WIDTH


def test_waiting_text_moves_faster() -> None:
    ticker = Ticker()
    ticker.view(WIDTH)
    ticker.feed([transcript("x" * WIDTH)])
    before = ticker.waiting
    ticker.advance()
    assert before - ticker.waiting == 2
    ticker.feed([])
    while ticker.waiting > WIDTH // 2:
        ticker.advance()
    before = ticker.waiting
    ticker.advance()
    assert before - ticker.waiting == 1


def test_the_ticker_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    ticker = Ticker()
    ticker.feed([transcript("hey autobot"), score(), wake()])
    run(ticker, 100)
    assert list(tmp_path.iterdir()) == []
