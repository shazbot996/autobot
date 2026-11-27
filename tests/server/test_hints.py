"""`autobot.server.hints`: autobot's own words, once each, capped (IS-16)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from autobot.config import load_config
from autobot.server.hints import SEPARATOR, build_hints
from server.server_fakes import config_with


def test_the_hints_are_the_wake_phrase_the_routes_and_the_word_lists(
    tmp_path: Path,
) -> None:
    hints = build_hints(config_with(tmp_path))
    assert hints[:9] == (
        "autobot",
        "question",
        "weather",
        "forecast",
        "time",
        "camera",
        "cameras",
        "introduce",
        "echo",
    )
    # An agent no rule names, the yes and no words, and the action keywords.
    assert hints[9] == "core"
    assert hints[10:13] == ("yes", "yeah", "yep")
    assert "never mind" in hints and hints[-1] == "cancel"
    assert len(set(hints)) == len(hints)


def test_a_word_is_listed_once_whatever_its_case(tmp_path: Path) -> None:
    extra = """
[[routes]]
command = "Weather"
agent = "weather"

[[routes]]
command = "  the   forecast "
agent = "weather"
mode = "forecast"
"""
    hints = build_hints(config_with(tmp_path, extra))
    assert hints[:4] == ("autobot", "weather", "the forecast", "forecast")
    assert [hint.lower() for hint in hints].count("weather") == 1


def test_the_hints_are_cut_at_the_cap(tmp_path: Path) -> None:
    config = config_with(tmp_path, hint_chars=30)
    hints = build_hints(config)
    assert hints == ("autobot", "question", "weather")
    assert len(SEPARATOR.join(hints)) <= 30
    assert build_hints(config_with(tmp_path, hint_chars=3)) == ()


def test_the_shipped_configuration_fits_its_cap() -> None:
    config = load_config()
    hints = build_hints(config)
    assert hints[0] == config.wake_phrases[0].phrase
    assert len(SEPARATOR.join(hints)) <= config.server.hint_chars
    # With room to spare the cap cuts nothing.
    roomy = replace(config, server=replace(config.server, hint_chars=100_000))
    assert build_hints(roomy)[-1] == config.words.actions[-1]
