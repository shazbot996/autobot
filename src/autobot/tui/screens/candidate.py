"""The Candidate screen: compare the trained candidate, install it, revert (TS-8).

A training run writes `models/wakeword/candidate.onnx` and touches nothing
else. Here the owner sees the candidate's figures on the held-out real
recordings beside the installed model's, both scored now on the dataset as it
is now, and decides. The comparison, the install and the revert are
`autobot.wakeword.evaluate` and `autobot.wakeword.install`; this screen draws
them and asks.

What is offered follows the comparison:

- nothing is installed, or the candidate is at least as good: "Install the
  candidate";
- the candidate is worse: no install;
- no verdict (the held-out set is too small): the figures, the warning, and
  "Install on my own judgment".

Beside them, in a last column, are the figures of the `sherpa_kws` baseline
on the same held-out recordings: a keyword spotter that needs no training,
scored at its default threshold only (it gives no score to sweep). The column
tells whether training is paying off (TS-14); it takes no part in the verdict
or in what is offered. When the baseline cannot be scored (its model files
are not fetched) one line says so and the rest is as it would be.

The threshold an install writes is the one shown here: the one recommended
just now on the held-out recordings as they are now, which is what the
verdict was reached at. With nothing held out to recommend one, it is the
threshold of the candidate's evaluation file.

A model file that is present but not configured as `openwakeword` is a
leftover: it is named as kept, and is not compared. "Revert to the previous
model" appears when an install left one. Each asks first, and afterwards
offers to restart a running listener, which loads its model when it starts.

Scoring runs behind the busy box (it takes from seconds to minutes with an
hour of room audio; Esc stops waiting for it) and is kept for the visit:
"Score again" asks afresh. Installing and reverting refuse on any machine but
the NUC (TS-11).

    Up, Down, Enter   the menu
    PgUp, PgDn        scroll the figures when they do not fit
"""

from __future__ import annotations

import curses
import json
from pathlib import Path
from typing import Any

from autobot.tui.app import MenuView
from autobot.tui.menu import MenuItem
from autobot.tui.runner import (
    CANCELLED,
    LISTENER_CONTROL_MODULE,
    LISTENER_STATUS_TIMEOUT,
    FunctionTask,
    listener_control_argv,
    listener_status_argv,
)
from autobot.tui.state import parse_listener_status
from autobot.tui.ui import Canvas, Rect, fit, wrap
from autobot.wakeword.evaluate import (
    BASELINE_KIND,
    OFFER_INSTALL,
    OFFER_JUDGMENT,
    OFFER_NONE,
    TOO_SMALL,
    Comparison,
    EvaluateError,
    KindScores,
    RealScores,
    compare,
)
from autobot.wakeword.install import (
    InstallError,
    Paths,
    install_candidate,
    installed_model,
    leftover_model,
    paths,
    previous_model,
    read_threshold,
    revert,
)

# `control up` waits for the listener to load its models.
START_TIMEOUT = 120.0

NO_CANDIDATE = "no candidate yet: train the model first"
INSTALL_QUESTION = "Install the candidate?"
REVERT_QUESTION = "Revert to the previous model?"
NO_REAL_RECORDINGS = (
    "no real recordings are held out: these are the candidate's synthetic "
    "figures, from its evaluation file"
)
SCORING_TITLE = "Scoring on the held-out recordings"

NO_BASELINE = f"baseline ({BASELINE_KIND}) not scored"

# A scored model is known again by its file (inode, size, time) and the
# manifest's, so neither a retrained candidate nor a changed dataset is ever
# shown with figures that were not measured on it: (model, manifest).
type ScoreKey = tuple[tuple[int, int, int], tuple[int, int, int]]


def elapsed_text(seconds: float) -> str:
    whole = max(0, int(seconds))
    return f"{whole // 60}:{whole % 60:02d}"


def _stat(path: Path) -> tuple[int, int, int]:
    try:
        status = path.stat()
    except OSError:
        return (0, 0, 0)
    return (status.st_ino, status.st_size, status.st_mtime_ns)


def read_evaluation(path: Path) -> dict[str, Any]:
    """An evaluation file's contents; empty when it is absent or unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def synthetic_lines(evaluation: dict[str, Any]) -> list[str]:
    """The synthetic figures of an evaluation file, at its recommended threshold."""
    metrics = evaluation.get("metrics_at_threshold")
    if not isinstance(metrics, dict):
        return ["the evaluation file holds no figures"]

    def figure(key: str, form: str) -> str:
        value = metrics.get(key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            return "-"
        return format(value, form)

    threshold = f"synthetic, at threshold {figure('threshold', 'g')}"
    hard = (
        f"false accepts: hard negatives {figure('fa_hard_seen', '.3f')} seen, "
        f"{figure('fa_hard_unseen', '.3f')} unseen"
    )
    return [
        f"{threshold}: recall {figure('recall', '.3f')}",
        hard,
        f"validation stream: {figure('val_fa_per_hour', '.2f')} false accepts per hour",
    ]


class CandidateScreen(MenuView):
    title = "Candidate"
    help_lines = ("PgUp, PgDn     scroll the figures when they do not fit",)
    facts_title = "Models"

    def enter(self) -> None:
        # The first wake phrase: the one the studio trains.
        self.index = 0
        self.scores: dict[ScoreKey, RealScores] = {}
        # The baseline kind's figures by the manifest they were measured on,
        # or the one line that says why there are none.
        self.baselines: dict[tuple[int, int, int], KindScores | str] = {}
        self.baseline: KindScores | None = None
        self.baseline_problem = ""
        self.task: FunctionTask | None = None
        self.comparison: Comparison | None = None
        self.candidate_scores: RealScores | None = None
        # One line when the scoring failed or was cancelled.
        self.problem = ""
        # What the last install or revert said.
        self.last = ""
        self.scroll = 0
        self.page = 5
        if self.files.candidate.is_file():
            self.score()
        else:
            self.app.say(NO_CANDIDATE, "warning")

    # --- the files ------------------------------------------------------------

    @property
    def root(self) -> Path:
        return self.app.services.models_root

    @property
    def files(self) -> Paths:
        return paths(self.root, self.app.config.wake_phrases[self.index])

    def installed(self) -> Path | None:
        return installed_model(self.app.config, self.root, self.index)

    def leftover(self) -> Path | None:
        return leftover_model(self.app.config, self.root, self.index)

    def previous(self) -> Path | None:
        return previous_model(self.app.config, self.root, self.index)

    def shown(self, path: Path) -> str:
        """A path relative to the directory the models are under."""
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)

    # --- scoring --------------------------------------------------------------

    def key_of(self, model: Path) -> ScoreKey:
        return (_stat(model), _stat(self.app.services.dataset.manifest))

    def score(self) -> None:
        """Score the candidate and the installed model now, behind the busy box."""
        app = self.app
        services = app.services
        files = self.files
        installed = self.installed()
        phrase = app.config.wake_phrases[self.index].phrase
        models = [files.candidate, *([installed] if installed else [])]
        self.comparison = None
        self.candidate_scores = None
        self.baseline = None
        self.baseline_problem = ""
        self.problem = ""
        self.scroll = 0
        config = app.config

        def job() -> None:
            for model in models:
                key = self.key_of(model)
                if key not in self.scores:
                    self.scores[key] = services.score_real(
                        model, services.dataset, phrase
                    )
            manifest = _stat(services.dataset.manifest)
            if manifest not in self.baselines:
                # The baseline is informational: when it cannot be scored the
                # models' figures stand without it.
                try:
                    self.baselines[manifest] = services.score_kind(
                        BASELINE_KIND, config, services.dataset
                    )
                except EvaluateError as error:
                    self.baselines[manifest] = f"{NO_BASELINE}: {error}"

        if self.task is not None and not self.task.finished:
            # An earlier scoring was left running: wait for that one.
            self.task.resume()
        else:
            self.task = services.spawn(job)
        task = self.task
        started = services.clock()
        result = app.wait(
            SCORING_TITLE,
            task,
            lambda: (
                f"{elapsed_text(services.clock() - started)} so far; "
                "long room audio takes minutes"
            ),
        )
        if result.returncode == CANCELLED:
            self.problem = "not scored: cancelled. Score again asks afresh."
            app.say(self.problem, "warning")
            return
        if not task.finished or not result.ok:
            self.problem = f"not scored: {result.summary()}"
            app.error(self.problem)
            return
        candidate = self.scores.get(self.key_of(files.candidate))
        installed_scores = (
            self.scores.get(self.key_of(installed)) if installed else None
        )
        if candidate is None or (installed and installed_scores is None):
            # The files changed while they were being scored.
            self.problem = "not scored: the model files changed meanwhile. Score again."
            app.error(self.problem)
            return
        if installed_scores is not None and installed_scores.size != candidate.size:
            self.problem = "not scored: the dataset changed meanwhile. Score again."
            app.error(self.problem)
            return
        self.candidate_scores = candidate
        baseline = self.baselines.get(_stat(services.dataset.manifest))
        if isinstance(baseline, str):
            self.baseline_problem = baseline
        elif baseline is not None and not baseline.empty:
            if baseline.size == candidate.size:
                self.baseline = baseline
            else:
                self.baseline_problem = (
                    f"{NO_BASELINE}: the dataset changed meanwhile. Score again."
                )
        # The held-out set both were scored on: the dataset's as it is now.
        self.comparison = compare(
            candidate, installed_scores, candidate.size, baseline=self.baseline
        )

    def rescore(self) -> None:
        self.scores.clear()
        self.baselines.clear()
        self.last = ""
        self.score()

    def offer(self) -> str:
        """What may be offered: one of the comparison's `OFFER_*`."""
        if not self.files.candidate.is_file():
            return OFFER_NONE
        if self.comparison is not None:
            return self.comparison.offer
        # Not scored. With nothing installed there is still nothing to beat.
        return OFFER_INSTALL if self.installed() is None else OFFER_NONE

    # --- actions --------------------------------------------------------------

    def install(self, on_judgment: bool = False) -> None:
        app = self.app
        if not app.require_nuc("installing a model"):
            return
        files = self.files
        if not files.candidate_json.is_file():
            app.error(
                f"the candidate has no evaluation: {files.candidate_json.name} is "
                "missing, so it has no threshold"
            )
            return
        if not read_evaluation(files.candidate_json):
            # Refused here, before the question, and without the file's path.
            app.error(
                f"Not installed: {files.candidate_json.name} cannot be read. Train again."
            )
            return
        # The threshold the figures on this screen are for: the one recommended
        # now, on the held-out set as it is now (TS-7, TS-8). The evaluation
        # file's is the fallback when nothing is held out to recommend one.
        scored = (
            self.candidate_scores.recommended_threshold
            if self.candidate_scores
            else None
        )
        try:
            filed: float | None = read_threshold(files.candidate_json)
        except InstallError as error:
            if scored is None:
                app.error(str(error))
                return
            filed = None
        threshold = filed if scored is None else scored
        assert threshold is not None
        lines: list[str] = []
        if on_judgment:
            lines += wrap([f"Warning: {TOO_SMALL}."], 64)
            lines += ["There is no verdict: you decide.", ""]
        lines.append(f"It becomes {files.model_setting} at threshold {threshold:g}.")
        if scored is None:
            lines.append(
                f"({threshold:g} is from its evaluation file, from the training.)"
            )
        elif filed is not None and abs(scored - filed) > 1e-9:
            lines += [
                f"({threshold:g} is recommended on the held-out set as it is",
                f"now; its evaluation file, from the training, says {filed:g}.)",
            ]
        if files.target.is_file():
            replaces = files.previous.is_file()
            lines.append(
                f"The model there now is kept as {files.previous.name}"
                + ("," if replaces else ".")
            )
            if replaces:
                lines.append("which replaces the one kept before.")
        lines += ["", INSTALL_QUESTION]
        if not app.confirm(INSTALL_QUESTION, lines):
            app.say("Nothing was installed.")
            return
        try:
            result = install_candidate(
                files, app.services.store, self.index, threshold=scored
            )
        except InstallError as error:
            app.error(str(error))
            return
        app.config = result.config
        self.done(result.message)

    def revert(self) -> None:
        app = self.app
        if not app.require_nuc("installing a model"):
            return
        files = self.files
        lines = [
            f"{files.previous.name} becomes {files.model_setting} again,",
            "with its evaluation and its threshold; the model",
            f"installed now is kept as {files.previous.name}.",
            "",
            REVERT_QUESTION,
        ]
        if not app.confirm(REVERT_QUESTION, lines):
            app.say("Nothing was changed.")
            return
        try:
            result = revert(files, app.services.store, self.index)
        except InstallError as error:
            app.error(str(error))
            return
        app.config = result.config
        self.done(result.message)

    def done(self, message: str) -> None:
        """After an install or a revert: show what happened, offer the restart."""
        self.last = message[:1].upper() + message[1:] + "."
        self.comparison = None
        self.candidate_scores = None
        self.baseline = None
        self.baseline_problem = ""
        self.problem = ""
        self.scroll = 0
        self.app.say(self.last, "ok")
        self.offer_restart()
        if self.files.candidate.is_file():
            # A candidate still waits (after a revert): compare it afresh.
            self.score()

    def offer_restart(self) -> None:
        """The listener loads its model when it starts: offer to restart a running one."""
        app = self.app
        services = app.services
        if not services.module_available(LISTENER_CONTROL_MODULE):
            return
        status = app.run_command(
            "Asking whether the listener runs",
            listener_status_argv(),
            timeout=LISTENER_STATUS_TIMEOUT,
        )
        running = status.ok and parse_listener_status(status.output)
        if status.ok:
            app.listener_running = running
        if not running:
            app.say(f"{self.last} The listener is not running.", "ok")
            return
        if not app.confirm(
            "Restart the listener?",
            [
                "The listener is running, and it loads its model",
                "when it starts.",
                "",
                "Restart it now, to use this model?",
            ],
            default=True,
        ):
            app.say(
                "The listener keeps its old model until it is restarted.", "warning"
            )
            return
        for title, action, timeout in (
            ("Stopping the listener", "down", 60.0),
            ("Starting the listener", "up", START_TIMEOUT),
        ):
            result = app.run_command(
                title, listener_control_argv(action), timeout=timeout
            )
            if not result.ok:
                app.report_failure(f"{title} failed", result)
                return
        app.say("The listener was restarted and runs the model.", "ok")

    # --- the menu -------------------------------------------------------------

    def items(self) -> list[MenuItem]:
        items: list[MenuItem] = []
        offer = self.offer()
        if offer == OFFER_INSTALL:
            items.append(
                MenuItem(
                    "Install the candidate",
                    self.install,
                    "Asks first; the model in place is kept as the previous one",
                )
            )
        elif offer == OFFER_JUDGMENT:
            items.append(
                MenuItem(
                    "Install on my own judgment",
                    lambda: self.install(on_judgment=True),
                    "No verdict: the held-out set is too small. Asks first.",
                )
            )
        if self.previous() is not None:
            items.append(
                MenuItem(
                    "Revert to the previous model",
                    self.revert,
                    "Swaps the installed model and the previous one; asks first",
                )
            )
        if self.files.candidate.is_file():
            items.append(
                MenuItem(
                    "Score again",
                    self.rescore,
                    "Score the models afresh on the held-out recordings",
                )
            )
        return items

    def key(self, key: int) -> bool:
        if key == curses.KEY_PPAGE:
            self.scroll = max(0, self.scroll - self.page)
        elif key == curses.KEY_NPAGE:
            self.scroll += self.page
        else:
            return super().key(key)
        return True

    def hint(self) -> str:
        if not self.files.candidate.is_file() and not self.menu.items:
            return NO_CANDIDATE
        return super().hint()

    # --- drawing --------------------------------------------------------------

    def facts(self) -> list[tuple[str, str, str]]:
        files = self.files
        if files.candidate.is_file():
            evaluation = read_evaluation(files.candidate_json)
            trained = evaluation.get("trained")
            if not files.candidate_json.is_file():
                detail = "  (no evaluation file)"
            elif not evaluation:
                detail = "  (evaluation unreadable)"
            elif isinstance(trained, str):
                detail = f" (trained {trained[:16].replace('T', ' ')})"
            else:
                detail = ""
            candidate = (f"{self.shown(files.candidate)}{detail}", "value")
        else:
            candidate = ("none", "dim")
        installed = self.installed()
        leftover = self.leftover()
        if installed is not None:
            threshold = self.app.config.wake_phrases[self.index].threshold
            model = (f"{self.shown(installed)}  (threshold {threshold:g})", "value")
        elif leftover is not None:
            model = (
                f"none; {leftover.name} is a leftover: kept, not compared",
                "warning",
            )
        else:
            model = ("none", "dim")
        if self.previous() is not None:
            previous = (
                f"{self.shown(files.previous)}: Revert goes back to it",
                "value",
            )
        elif files.previous.is_file():
            previous = (f"{files.previous.name}, from an earlier install", "dim")
        else:
            previous = ("none", "dim")
        return [
            ("Candidate", *candidate),
            ("Installed", *model),
            ("Previous", *previous),
        ]

    def figures(self, width: int) -> list[tuple[str, str]]:
        """The panel's lines, folded to `width`, each with its theme role."""
        lines: list[tuple[str, str]] = []

        def add(text: str, role: str = "normal") -> None:
            lines.extend((line, role) for line in wrap([text], width))

        if self.last:
            add(self.last, "ok")
        if not self.files.candidate.is_file():
            if not self.last:
                add(NO_CANDIDATE, "warning")
            return lines
        if self.problem:
            add(self.problem, "error")
        comparison = self.comparison
        if comparison is None:
            if not self.problem:
                add("not scored yet: Score again", "dim")
            return lines
        offer = comparison.offer
        role = {OFFER_INSTALL: "ok", OFFER_JUDGMENT: "warning", OFFER_NONE: "error"}
        if offer == OFFER_JUDGMENT and TOO_SMALL not in comparison.summary:
            add(TOO_SMALL, "warning")
        add(comparison.summary, role[offer])
        scores = self.candidate_scores
        if scores is not None and scores.empty:
            add(NO_REAL_RECORDINGS, "warning")
            for line in synthetic_lines(read_evaluation(self.files.candidate_json)):
                add(line)
            return lines
        for line in comparison.lines:
            add(line)
        if self.baseline_problem:
            add(self.baseline_problem, "dim")
        return lines

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        theme = canvas.theme
        self.refresh_menu()
        lines = self.figures(rect.width - 2)
        count = len(self.menu.items)
        row = self.draw_facts(canvas, rect, count + 1 + len(lines))
        row += self.draw_menu(
            canvas, Rect(rect.y + row, rect.x, rect.height - row, rect.width)
        )
        if count and row < rect.height:
            canvas.rule(rect, row, theme.frame)
            row += 1
        room = rect.height - row
        if room <= 0:
            return
        # When the figures do not fit, the last row is the scroll note's own:
        # it never covers a figure.
        if len(lines) > room:
            room -= 1
        if room <= 0:
            return
        self.page = max(1, room - 1)
        self.scroll = max(0, min(self.scroll, len(lines) - room))
        for offset, (text, role) in enumerate(lines[self.scroll : self.scroll + room]):
            canvas.put_in(
                rect, row + offset, 1, fit(text, rect.width - 2), getattr(theme, role)
            )
        hidden = len(lines) - room - self.scroll
        if hidden > 0 or self.scroll:
            note = " PgDn: more " if hidden > 0 else " PgUp: back "
            if hidden > 0 and self.scroll:
                note = " PgUp, PgDn: more "
            canvas.put_in(
                rect,
                rect.height - 1,
                max(1, rect.width - len(note) - 1),
                note,
                theme.warning,
            )
