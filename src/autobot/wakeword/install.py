"""Install a trained candidate, and revert to the model it replaced (TS-8).

A training run writes `models/wakeword/candidate.onnx` and `candidate.json`
and touches nothing else. Installing is the owner's decision, made in the
interface, and this module is what the interface calls:

- `paths` names the files involved for one wake phrase.
- `installed_model` says which model file is installed, and a model is
  installed only when the configuration says to use it: the phrase's kind is
  `openwakeword` and its model file exists. A file at the model path while
  `text_match` is configured is a leftover (`leftover_model`): kept, never
  deleted, and never counted as installed.
- `install_candidate` moves the candidate into place, keeps the model that
  was there, a leftover included, as `<name>.prev.onnx` with its evaluation,
  and writes `kind`, `model` and `threshold` to the config file.
- `revert` swaps the installed model and the previous one and writes the
  previous model's threshold.

Which threshold. The interface judges a candidate at the threshold
recommended on the held-out recordings as they are when the owner decides
(TS-7, TS-8), which can differ from the one the training wrote into
`candidate.json` once recordings have been added. So `install_candidate`
takes that threshold from the caller and writes it; without one it uses the
evaluation file's `recommended_threshold`. When the two differ, the installed
evaluation file gains `installed_threshold`, the threshold the model was
installed at, and nothing else in it changes. `installed_threshold_of` reads
it, falling back to `recommended_threshold`, and that is the threshold a
revert restores: the one the model ran at.

Every file is moved with `os.replace`, and the model that is being replaced
is copied to a temporary name first, so at no moment is the model file
missing or half written: a listener starting meanwhile loads the old model
or the new one. The config file is written through `ConfigStore.update`,
which keeps its comments. When the config cannot be written, the files are
put back as they were and nothing has changed.

No drawing, no audio, no network. A failure is an `InstallError` with one
line.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autobot.config import (
    KIND_OPENWAKEWORD,
    Config,
    ConfigError,
    WakePhraseConfig,
)
from autobot.listener.models import WAKEWORD_MODELS_DIR
from autobot.tui.config_edit import (
    Change,
    ConfigEditError,
    ConfigStore,
    apply_changes,
)
from autobot.wakeword.train import CANDIDATE_NAME, slug

__all__ = [
    "WAKE_PHRASES_TABLE",
    "InstallError",
    "InstallResult",
    "Paths",
    "RevertResult",
    "candidate_exists",
    "install_candidate",
    "installed_model",
    "installed_threshold_of",
    "leftover_model",
    "paths",
    "previous_model",
    "read_threshold",
    "revert",
]

# The config table a wake phrase is an entry of: `Change(WAKE_PHRASES_TABLE,
# key, value, index)` addresses the `index`-th `[[wake_phrases]]` entry,
# counting from zero in file order.
WAKE_PHRASES_TABLE = "wake_phrases"


class InstallError(Exception):
    """The install or the revert could not be done. The message is one line."""


@dataclass(frozen=True)
class Paths:
    """The files an install or a revert moves, for one wake phrase."""

    repo: Path
    phrase: str
    # What a training run writes.
    candidate: Path
    candidate_json: Path
    # The phrase's model path: where the installed model is.
    target: Path
    target_json: Path
    # Where the model an install replaces is kept.
    previous: Path
    previous_json: Path

    @property
    def model_setting(self) -> str:
        """`target` as the config file's `model` value: relative to the repository."""
        try:
            return self.target.relative_to(self.repo).as_posix()
        except ValueError:
            return str(self.target)


@dataclass(frozen=True)
class InstallResult:
    """What an install did."""

    # The threshold written to the config file: the one given to
    # `install_candidate`, or the candidate's evaluation file's.
    threshold: float
    # Whether a model was at the target and is kept as `Paths.previous`.
    kept_previous: bool
    # Whether that model had an evaluation beside it, kept as `Paths.previous_json`.
    previous_has_evaluation: bool
    # The configuration the file now holds.
    config: Config
    # One line for the interface to show.
    message: str


@dataclass(frozen=True)
class RevertResult:
    """What a revert did."""

    # The threshold the config file now holds for the phrase.
    threshold: float
    # False when the model reverted to has no evaluation beside it: the
    # threshold was then left as it was, and `message` says so.
    threshold_restored: bool
    config: Config
    message: str


def paths(repo: Path, phrase_config: WakePhraseConfig) -> Paths:
    """The candidate, the target and the previous model for one phrase.

    The target is the phrase's `model` path, under `repo` when it is
    relative; a phrase with no model path configured (`text_match` on a
    fresh install) gets `models/wakeword/<phrase>.onnx`. The candidate is
    `models/wakeword/candidate.onnx`: there is one candidate at a time.
    """
    if phrase_config.model:
        configured = Path(phrase_config.model).expanduser()
        target = configured if configured.is_absolute() else repo / configured
    else:
        target = repo / WAKEWORD_MODELS_DIR / f"{slug(phrase_config.phrase)}.onnx"
    candidate = repo / WAKEWORD_MODELS_DIR / CANDIDATE_NAME
    previous = target.with_name(f"{target.stem}.prev{target.suffix}")
    return Paths(
        repo=repo,
        phrase=phrase_config.phrase,
        candidate=candidate,
        candidate_json=candidate.with_suffix(".json"),
        target=target,
        target_json=target.with_suffix(".json"),
        previous=previous,
        previous_json=previous.with_suffix(".json"),
    )


def candidate_exists(paths: Paths) -> bool:
    """Whether a trained candidate awaits the owner's decision."""
    return paths.candidate.is_file()


def installed_model(config: Config, repo: Path, index: int = 0) -> Path | None:
    """The installed model file of the `index`-th wake phrase, or None.

    A model is installed only when `kind = "openwakeword"` is configured and
    the file exists. So a leftover is never installed and never compared.
    """
    phrase = config.wake_phrases[index]
    if phrase.kind != KIND_OPENWAKEWORD:
        return None
    target = paths(repo, phrase).target
    return target if target.is_file() else None


def leftover_model(config: Config, repo: Path, index: int = 0) -> Path | None:
    """A model file at the phrase's model path that is not installed, or None."""
    phrase = config.wake_phrases[index]
    if phrase.kind == KIND_OPENWAKEWORD:
        return None
    target = paths(repo, phrase).target
    return target if target.is_file() else None


def previous_model(config: Config, repo: Path, index: int = 0) -> Path | None:
    """The model `revert` would go back to, or None when it cannot revert.

    A revert undoes an install: it needs an installed model and a previous
    one. A `.prev` file beside a leftover is not something to revert to.
    """
    if installed_model(config, repo, index) is None:
        return None
    previous = paths(repo, config.wake_phrases[index]).previous
    return previous if previous.is_file() else None


def _evaluation(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise InstallError(f"cannot read the evaluation {path}") from None
    if not isinstance(data, dict):
        raise InstallError(f"the evaluation {path} is not a JSON object")
    return data


def read_threshold(evaluation: Path) -> float:
    """The recommended threshold an evaluation file holds; `InstallError` otherwise."""
    value = _evaluation(evaluation).get("recommended_threshold")
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InstallError(f"the evaluation {evaluation} recommends no threshold")
    if not 0.0 < value <= 1.0:
        raise InstallError(
            f"the evaluation {evaluation} recommends the threshold {value}, which is "
            "not above 0 and at most 1"
        )
    return float(value)


def _usable_threshold(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if 0.0 < value <= 1.0 else None


def installed_threshold_of(evaluation: Path) -> float:
    """The threshold a model was installed at: what a revert restores.

    The evaluation file's `installed_threshold` when an install wrote one,
    else its `recommended_threshold`; `InstallError` when it has neither.
    """
    installed = _usable_threshold(_evaluation(evaluation).get("installed_threshold"))
    return read_threshold(evaluation) if installed is None else installed


def _record_installed_threshold(evaluation: Path, threshold: float) -> None:
    """Add `installed_threshold` to an evaluation file, through a temporary name."""
    data = _evaluation(evaluation)
    data["installed_threshold"] = threshold
    writing = evaluation.with_name(f".{evaluation.name}.tmp")
    writing.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(writing, evaluation)


class _Journal:
    """File moves that can be taken back, last first, when a later step fails."""

    def __init__(self) -> None:
        self._undo: list[Callable[[], None]] = []

    def move(self, source: Path, destination: Path) -> None:
        os.replace(source, destination)
        self._undo.append(lambda: os.replace(destination, source))

    def copy(self, source: Path, destination: Path) -> None:
        """Copy through a temporary name, so `destination` is never half written."""
        _copy(source, destination)
        self._undo.append(lambda: destination.unlink(missing_ok=True))

    def swap(self, first: Path, second: Path) -> None:
        _swap(first, second)
        self._undo.append(lambda: _swap(first, second))

    def then(self, undo: Callable[[], None]) -> None:
        self._undo.append(undo)

    def roll_back(self) -> None:
        """Best effort: the error that caused it is the one that is raised."""
        for undo in reversed(self._undo):
            with contextlib.suppress(OSError):
                undo()


def _aside(path: Path) -> Path:
    """A temporary name beside `path`."""
    return path.with_name(f".{path.name}.aside")


def _copy(source: Path, destination: Path) -> None:
    copying = destination.with_name(f".{destination.name}.tmp")
    shutil.copy2(source, copying)
    os.replace(copying, destination)


def _swap(first: Path, second: Path) -> None:
    """Exchange two files. `first` is never absent and never half written."""
    aside = _aside(first)
    shutil.copy2(first, aside)
    os.replace(second, first)
    os.replace(aside, second)


def _changes(paths: Paths, index: int, threshold: float | None) -> list[Change]:
    changes = [
        Change(WAKE_PHRASES_TABLE, "kind", KIND_OPENWAKEWORD, index),
        Change(WAKE_PHRASES_TABLE, "model", paths.model_setting, index),
    ]
    if threshold is not None:
        changes.append(Change(WAKE_PHRASES_TABLE, "threshold", threshold, index))
    return changes


def _check_config(store: ConfigStore, changes: Sequence[Change], doing: str) -> None:
    """Fail before any file is moved when the config change could not be written."""
    try:
        apply_changes(store.path.read_text(encoding="utf-8"), changes)
    except (OSError, UnicodeDecodeError, ConfigError, ConfigEditError) as error:
        raise InstallError(f"could not {doing}, nothing was changed: {error}") from None


def install_candidate(
    paths: Paths,
    store: ConfigStore,
    index: int,
    threshold: float | None = None,
) -> InstallResult:
    """Install the candidate as the `index`-th wake phrase's model.

    `index` is the phrase's zero-based position among the config file's
    `[[wake_phrases]]` entries: the `index` of a `Change`.

    `threshold` is the threshold to install it at: the one the interface
    recommended on the held-out recordings as they are now and judged the
    candidate at. None means the `recommended_threshold` of the candidate's
    evaluation file. When it differs from the file's, the installed
    evaluation gains `installed_threshold` (module docstring).

    Whatever model file is at the target, a leftover included, becomes
    `<name>.prev.onnx`, with its evaluation as `<name>.prev.json` when it has
    one; a previous model with no evaluation is kept alone, and an older
    `.prev.json` is removed so that it is not taken for its evaluation. The
    candidate and its evaluation move into place, and `kind =
    "openwakeword"`, the `model` path and the candidate's recommended
    `threshold` are written to the config file. Raises `InstallError` with
    one line when the candidate or its evaluation is missing or unusable,
    when `threshold` is not above 0 and at most 1, or when the config file
    cannot be written; nothing has changed then.
    """
    if threshold is not None and _usable_threshold(threshold) is None:
        raise InstallError(
            f"cannot install at the threshold {threshold}: it is not above 0 and "
            "at most 1"
        )
    if not paths.candidate.is_file():
        raise InstallError(
            f"there is no candidate to install: {paths.candidate} is missing "
            "(train one with 'make train-wakeword')"
        )
    if not paths.candidate_json.is_file():
        raise InstallError(
            f"the candidate has no evaluation: {paths.candidate_json} is missing, "
            "so there is no threshold to install it with"
        )
    evaluation = _evaluation(paths.candidate_json)
    filed = _usable_threshold(evaluation.get("recommended_threshold"))
    if threshold is None:
        threshold = read_threshold(paths.candidate_json)
    trained_for = evaluation.get("phrase")
    if isinstance(trained_for, str) and trained_for.lower() != paths.phrase.lower():
        raise InstallError(
            f"the candidate was trained for {trained_for!r}, not {paths.phrase!r}"
        )
    changes = _changes(paths, index, threshold)
    _check_config(store, changes, "install the candidate")
    had_model = paths.target.is_file()
    had_evaluation = had_model and paths.target_json.is_file()
    journal = _Journal()
    asides = [_aside(paths.previous), _aside(paths.previous_json)]
    try:
        paths.target.parent.mkdir(parents=True, exist_ok=True)
        # The older previous model steps aside until the install has succeeded.
        for old, aside in zip(
            (paths.previous, paths.previous_json), asides, strict=True
        ):
            if old.exists():
                journal.move(old, aside)
        # The model at the target is copied, not moved: the target is never absent.
        if had_model:
            journal.copy(paths.target, paths.previous)
        if had_evaluation:
            journal.copy(paths.target_json, paths.previous_json)
        os.replace(paths.candidate, paths.target)
        journal.then(
            lambda: _restore(paths.target, paths.candidate, had_model, paths.previous)
        )
        os.replace(paths.candidate_json, paths.target_json)
        journal.then(
            lambda: _restore(
                paths.target_json,
                paths.candidate_json,
                had_evaluation,
                paths.previous_json,
            )
        )
        config = store.update(*changes)
    except (OSError, ConfigError, ConfigEditError) as error:
        journal.roll_back()
        reason = error.strerror if isinstance(error, OSError) else str(error)
        raise InstallError(
            f"could not install the candidate, nothing was changed: {reason}"
        ) from None
    for aside in asides:
        with contextlib.suppress(OSError):
            aside.unlink(missing_ok=True)
    if filed is None or abs(filed - threshold) > 1e-9:
        # What a later revert to this model restores. Best effort: the
        # install has been done, and without it a revert restores `filed`.
        with contextlib.suppress(OSError, InstallError):
            _record_installed_threshold(paths.target_json, threshold)
    if not had_model:
        kept_text = "there was no model before it"
    elif had_evaluation:
        kept_text = f"the model it replaced is kept as {paths.previous.name}"
    else:
        kept_text = (
            f"the model it replaced is kept as {paths.previous.name}, without an "
            "evaluation: it had none"
        )
    return InstallResult(
        threshold=threshold,
        kept_previous=had_model,
        previous_has_evaluation=had_evaluation,
        config=config,
        message=f"installed the candidate as {paths.model_setting} at threshold "
        f"{threshold:g}; {kept_text}",
    )


def _restore(installed: Path, candidate: Path, had_one: bool, kept: Path) -> None:
    """Undo one file of an install: the candidate back out, the old file back in."""
    if had_one:
        # `kept` is the copy of what was there; put the candidate's bytes back
        # under its own name first, without ever removing `installed`.
        shutil.copy2(installed, candidate)
        _copy(kept, installed)
    else:
        os.replace(installed, candidate)


def revert(paths: Paths, store: ConfigStore, index: int) -> RevertResult:
    """Swap the installed model and the previous one, with their evaluations.

    The model that was installed becomes the previous one, so a second
    revert undoes the first. The previous model's threshold, the one it was
    installed at (`installed_threshold_of`), is written to the config file; when it has no evaluation beside it (a
    leftover kept alone), the threshold is left as it is and the message
    says so. Raises `InstallError` with one line when no model is installed
    for the phrase, when there is no previous model, or when the config file
    cannot be written; nothing has changed then.
    """
    try:
        phrase = store.load().wake_phrases[index]
    except (ConfigError, IndexError) as error:
        raise InstallError(f"cannot read the configuration: {error}") from None
    if phrase.kind != KIND_OPENWAKEWORD or not paths.target.is_file():
        raise InstallError(
            f"no model is installed for {paths.phrase!r}, so there is nothing to revert"
        )
    if not paths.previous.is_file():
        raise InstallError(
            f"there is no previous model to revert to: {paths.previous} is missing"
        )
    has_evaluation = paths.previous_json.is_file()
    threshold = installed_threshold_of(paths.previous_json) if has_evaluation else None
    changes = _changes(paths, index, threshold)
    _check_config(store, changes, "revert to the previous model")
    journal = _Journal()
    try:
        if has_evaluation and paths.target_json.exists():
            journal.swap(paths.target_json, paths.previous_json)
        elif has_evaluation:
            journal.move(paths.previous_json, paths.target_json)
        elif paths.target_json.exists():
            # The model reverted to has no evaluation: the installed model's
            # goes with it to `.prev`, and none is left beside the target.
            journal.move(paths.target_json, paths.previous_json)
        journal.swap(paths.target, paths.previous)
        config = store.update(*changes)
    except (OSError, ConfigError, ConfigEditError) as error:
        journal.roll_back()
        reason = error.strerror if isinstance(error, OSError) else str(error)
        raise InstallError(
            f"could not revert to the previous model, nothing was changed: {reason}"
        ) from None
    now = config.wake_phrases[index].threshold
    if threshold is None:
        message = (
            f"reverted to the previous model as {paths.model_setting}; it has no "
            f"evaluation, so the threshold stays at {now:g}"
        )
    else:
        message = (
            f"reverted to the previous model as {paths.model_setting} at threshold "
            f"{threshold:g}; the model it replaced is kept as {paths.previous.name}"
        )
    return RevertResult(
        threshold=now,
        threshold_restored=threshold is not None,
        config=config,
        message=message,
    )
