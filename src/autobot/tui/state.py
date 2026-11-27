"""Where the wake-word listener stands on the road to a trained model. No drawing.

The interface always shows one of five states (TS-2):

- *untrained*: `text_match` is configured, whether or not a model file exists;
- *recording*: a recording session is open;
- *candidate ready*: a trained model awaits the owner's decision;
- *installed*: an `openwakeword` model is configured and its file exists;
- *listener down*: a model is installed but the listener is not running.

When more than one applies the first of recording, candidate ready, listener
down, installed, untrained wins. *listener down* is a state of an installed
model only: before one is installed the road matters more than the process,
so a fresh checkout reads *untrained* whether or not the listener runs.

A model file at the phrase's model path that is not configured as
`kind = "openwakeword"` is a leftover: it does not count as installed, and it
is kept.
"""

from __future__ import annotations

import enum
from pathlib import Path

from autobot.config import KIND_OPENWAKEWORD, Config

# Where the trained models live, and the `autobot` phrase's model path when
# the config names none: where the studio installs.
MODELS_DIR = Path("models/wakeword")
DEFAULT_MODEL = MODELS_DIR / "autobot.onnx"
# What every training run writes, for the owner to compare and install.
CANDIDATE_MODEL = MODELS_DIR / "candidate.onnx"


class State(enum.Enum):
    UNTRAINED = "untrained"
    RECORDING = "recording"
    CANDIDATE_READY = "candidate ready"
    INSTALLED = "installed"
    LISTENER_DOWN = "listener down"


# What each state means, in one sentence for the main screen.
_SENTENCES: dict[State, str] = {
    State.UNTRAINED: "no trained model is installed.",
    State.RECORDING: "a recording session is open.",
    State.CANDIDATE_READY: "a trained model awaits your decision: open Candidate.",
    State.INSTALLED: "the trained model is installed and the listener runs.",
    State.LISTENER_DOWN: (
        "a model is installed but the listener is not running: see Service."
    ),
}
# The `Theme` role each state's badge is drawn in.
BADGE_ROLES: dict[State, str] = {
    State.UNTRAINED: "badge_warning",
    State.RECORDING: "badge_error",
    State.CANDIDATE_READY: "badge_info",
    State.INSTALLED: "badge_ok",
    State.LISTENER_DOWN: "badge_error",
}


def model_path(config: Config, repo: Path) -> Path:
    """The first wake phrase's model path under `repo`.

    A phrase with no model path configured (`text_match` on a fresh install)
    has `models/wakeword/<phrase>.onnx`, the phrase lower-cased with `_` for
    anything that is not a letter or a digit: the rule of
    `autobot.wakeword.install.paths`, repeated here so that drawing the main
    menu does not import the training code.
    """
    phrase = config.wake_phrases[0]
    if phrase.model:
        return repo / Path(phrase.model).expanduser()
    cleaned = "".join(c if c.isalnum() else " " for c in phrase.phrase.lower())
    return repo / MODELS_DIR / f"{'_'.join(cleaned.split()) or 'phrase'}.onnx"


def candidate_path(repo: Path) -> Path:
    return repo / CANDIDATE_MODEL


def candidate_exists(repo: Path) -> bool:
    """Whether a training run has left a candidate to decide on."""
    return candidate_path(repo).is_file()


def installed(config: Config, repo: Path) -> bool:
    """Whether a trained model counts as installed: configured, and its file exists."""
    phrase = config.wake_phrases[0]
    return phrase.kind == KIND_OPENWAKEWORD and model_path(config, repo).is_file()


def leftover_model(config: Config, repo: Path) -> Path | None:
    """A model file that is present but not configured as `openwakeword`."""
    if config.wake_phrases[0].kind == KIND_OPENWAKEWORD:
        return None
    path = model_path(config, repo)
    return path if path.is_file() else None


def parse_listener_status(output: str) -> bool:
    """Whether `python -m autobot.listener.control status` said the listener runs."""
    return any(
        line.strip().startswith("listener: running") for line in output.splitlines()
    )


def listener_state(
    config: Config,
    repo: Path,
    candidate_exists: bool,
    session_open: bool,
    listener_running: bool,
) -> State:
    """The one state to show, by the precedence in the module docstring."""
    if session_open:
        return State.RECORDING
    if candidate_exists:
        return State.CANDIDATE_READY
    if installed(config, repo):
        return State.INSTALLED if listener_running else State.LISTENER_DOWN
    return State.UNTRAINED


def explain(state: State, leftover: Path | str | None = None) -> str:
    """What `state` means, in a sentence; `describe` without the state's name."""
    text = _SENTENCES[state]
    if leftover is not None:
        text += f" {leftover} is kept, not counted as installed."
    return text


def describe(state: State, leftover: Path | str | None = None) -> str:
    """The main screen's line for `state`.

    For a leftover model it adds that the file is kept and does not count as
    installed. Pass the path as it should be shown (relative to the repository).
    """
    return f"{state.value}: {explain(state, leftover)}"
