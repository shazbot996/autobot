"""`autobot.tui.state`: the five states of the road to a trained model."""

from pathlib import Path

import pytest
from tui_fakes import CONFIG_TEXT

from autobot.config import Config, load_config
from autobot.tui.state import (
    BADGE_ROLES,
    CANDIDATE_MODEL,
    DEFAULT_MODEL,
    State,
    candidate_exists,
    describe,
    explain,
    installed,
    leftover_model,
    listener_state,
    model_path,
    parse_listener_status,
)
from autobot.tui.ui import theme_roles
from autobot.wakeword.install import paths

OPENWAKEWORD = CONFIG_TEXT.replace(
    'kind = "text_match"', 'kind = "openwakeword"'
).replace('model = ""', 'model = "models/wakeword/autobot.onnx"')


def config(tmp_path: Path, text: str = CONFIG_TEXT) -> Config:
    path = tmp_path / "autobot.toml"
    path.write_text(text, encoding="utf-8")
    return load_config(path)


def model(tmp_path: Path, relative: Path = DEFAULT_MODEL) -> Path:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"onnx")
    return path


def state(
    cfg: Config,
    repo: Path,
    *,
    candidate: bool = False,
    session: bool = False,
    running: bool = True,
) -> State:
    return listener_state(cfg, repo, candidate, session, running)


def test_a_fresh_checkout_is_untrained_whether_or_not_the_listener_runs(
    tmp_path: Path,
) -> None:
    cfg = config(tmp_path)
    assert state(cfg, tmp_path) is State.UNTRAINED
    assert state(cfg, tmp_path, running=False) is State.UNTRAINED
    assert leftover_model(cfg, tmp_path) is None
    assert not installed(cfg, tmp_path)


def test_text_match_with_a_model_file_is_untrained_with_a_leftover(
    tmp_path: Path,
) -> None:
    cfg = config(tmp_path)
    path = model(tmp_path)
    assert state(cfg, tmp_path) is State.UNTRAINED
    assert not installed(cfg, tmp_path)
    assert leftover_model(cfg, tmp_path) == path
    line = describe(State.UNTRAINED, DEFAULT_MODEL)
    assert line.startswith("untrained: no trained model is installed.")
    assert line.endswith(
        "models/wakeword/autobot.onnx is kept, not counted as installed."
    )


def test_a_leftover_at_a_configured_path_is_found_too(tmp_path: Path) -> None:
    text = CONFIG_TEXT.replace('model = ""', 'model = "models/other.onnx"')
    cfg = config(tmp_path, text)
    path = model(tmp_path, Path("models/other.onnx"))
    assert model_path(cfg, tmp_path) == path
    assert leftover_model(cfg, tmp_path) == path


def test_a_phrase_without_a_model_path_has_one_named_after_it(tmp_path: Path) -> None:
    assert model_path(config(tmp_path), tmp_path) == tmp_path / DEFAULT_MODEL
    text = CONFIG_TEXT.replace('phrase = "autobot"', 'phrase = "Hey there!"')
    cfg = config(tmp_path, text)
    # The rule of `autobot.wakeword.install.paths`.
    assert model_path(cfg, tmp_path) == tmp_path / "models/wakeword/hey_there.onnx"
    assert paths(tmp_path, cfg.wake_phrases[0]).target == model_path(cfg, tmp_path)
    assert paths(tmp_path, cfg.wake_phrases[0]).candidate == tmp_path / CANDIDATE_MODEL


def test_openwakeword_with_its_file_is_installed(tmp_path: Path) -> None:
    cfg = config(tmp_path, OPENWAKEWORD)
    model(tmp_path)
    assert installed(cfg, tmp_path)
    assert state(cfg, tmp_path) is State.INSTALLED
    # An installed model is not a leftover.
    assert leftover_model(cfg, tmp_path) is None


def test_openwakeword_without_its_file_is_untrained(tmp_path: Path) -> None:
    cfg = config(tmp_path, OPENWAKEWORD)
    assert state(cfg, tmp_path) is State.UNTRAINED
    assert leftover_model(cfg, tmp_path) is None


def test_listener_down_is_an_installed_model_without_its_listener(
    tmp_path: Path,
) -> None:
    cfg = config(tmp_path, OPENWAKEWORD)
    model(tmp_path)
    assert state(cfg, tmp_path, running=False) is State.LISTENER_DOWN


def test_candidate_ready_and_recording(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    assert state(cfg, tmp_path, candidate=True) is State.CANDIDATE_READY
    assert state(cfg, tmp_path, session=True) is State.RECORDING


def test_the_precedence(tmp_path: Path) -> None:
    cfg = config(tmp_path, OPENWAKEWORD)
    model(tmp_path)
    # recording > candidate ready > listener down > installed > untrained
    assert (
        state(cfg, tmp_path, candidate=True, session=True, running=False)
        is State.RECORDING
    )
    assert state(cfg, tmp_path, candidate=True, running=False) is State.CANDIDATE_READY
    assert state(cfg, tmp_path, running=False) is State.LISTENER_DOWN
    assert state(cfg, tmp_path) is State.INSTALLED
    assert state(config(tmp_path), tmp_path) is State.UNTRAINED


def test_the_five_values() -> None:
    assert [value.value for value in State] == [
        "untrained",
        "recording",
        "candidate ready",
        "installed",
        "listener down",
    ]


def test_every_state_has_a_line_and_a_badge() -> None:
    for value in State:
        line = describe(value)
        assert line == f"{value.value}: {explain(value)}"
        assert "kept" not in line
        assert BADGE_ROLES[value] in theme_roles()


def test_the_candidate_is_looked_for_where_the_training_writes_it(
    tmp_path: Path,
) -> None:
    assert Path("models/wakeword/candidate.onnx") == CANDIDATE_MODEL
    assert not candidate_exists(tmp_path)
    model(tmp_path, CANDIDATE_MODEL)
    assert candidate_exists(tmp_path)


@pytest.mark.parametrize(
    ("output", "running"),
    [
        (
            "listener: running (pid 12; foreground, run/listener.pid)\nuptime: 1 s\n",
            True,
        ),
        ("listener: not running\nunit:     not installed (foreground mode)\n", False),
        ("listener: not running (it stopped while being measured)\n", False),
        ("", False),
    ],
)
def test_parse_listener_status(output: str, running: bool) -> None:
    assert parse_listener_status(output) is running
