"""Which model answers for an agent: its own, then its fallback, in order (IS-18). No drawing.

An agent's definition names its `model`, `none`, `local` or `gemini`, and a
`fallback`: the models to try in order when its own cannot answer, and,
last only, a line the agent says instead. This module walks that chain:

- `local` is the inference server's language model
  (`autobot.agents.local_model`). It is passed over while the watcher says
  the server's `chat` job is not ready (IS-4, IS-23).
- `gemini` is Gemini through the ADK, as the service account (AS-1). It is
  passed over where the face was given no Gemini model. With no model
  chosen on the Google Cloud screen it says so itself when it is asked,
  which passes it over in `first_answer`.
- `none` stands for no model and is passed over.
- Anything else is the spoken line, and ends the walk.

`choose` gives the first model that can be asked now, or the line.
`first_answer` goes further for an agent that asks one question: it asks
each model in turn, and a model that fails with a `ModelError` (the server
timed out, Gemini refused) is passed over for the next, without a second
try of the same one (IS-4).

The models are made through `Seams.models`, keyed `local` and `gemini`,
which the tests give fakes for. Nothing here opens a connection itself.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from autobot.agents.gemini import Model, ModelError
from autobot.config import (
    AGENT_MODELS,
    MODEL_GEMINI,
    MODEL_LOCAL,
    MODEL_NONE,
    AgentConfig,
    Config,
)
from autobot.tui.face import Seams

# A choice that is the spoken line, or nothing at all.
KIND_LINE = "line"
KIND_NOTHING = ""

SERVER_AWAY = "the inference server's chat is not ready"
NOT_GIVEN = "no {kind} model is set up here"
NO_MODEL_NAMED = "the {agent} agent names no model"
NO_ANSWER = "the language model gave no answer"


@dataclass(frozen=True)
class Choice:
    """One step of an agent's chain: a model to ask, or the line to say."""

    # `local`, `gemini`, `KIND_LINE` or `KIND_NOTHING`.
    kind: str
    model: Model | None = None
    # The spoken line of a `KIND_LINE` choice.
    line: str = ""
    # Why the models before this one were passed over, a line each.
    passed: tuple[str, ...] = ()

    @property
    def why_not(self) -> str:
        """The last reason a model was passed over; empty when none was."""
        return self.passed[-1] if self.passed else ""


def chain(definition: AgentConfig) -> tuple[str, ...]:
    """The agent's model and its fallback, in the order they are tried."""
    return (definition.model, *definition.fallback)


def choices(
    definition: AgentConfig, seams: Seams, config: Config, repo: Path
) -> Iterator[Choice]:
    """Each model of the chain that can be asked now, in order; then the line, if any."""
    passed: list[str] = []
    for entry in chain(definition):
        if entry == MODEL_NONE:
            continue
        if entry not in AGENT_MODELS:
            yield Choice(KIND_LINE, line=entry, passed=tuple(passed))
            return
        make = seams.models.get(entry)
        if make is None:
            passed.append(NOT_GIVEN.format(kind=entry))
            continue
        if entry == MODEL_LOCAL:
            watch = seams.watch
            if watch is None or not watch.ready("chat"):
                reason = watch.reason if watch is not None else ""
                passed.append(f"{SERVER_AWAY}{': ' + reason if reason else ''}")
                continue
        yield Choice(entry, make(config, repo), passed=tuple(passed))
    named = any(entry in (MODEL_LOCAL, MODEL_GEMINI) for entry in chain(definition))
    if not passed and not named:
        passed.append(NO_MODEL_NAMED.format(agent=definition.name))
    yield Choice(KIND_NOTHING, passed=tuple(passed))


def choose(definition: AgentConfig, seams: Seams, config: Config, repo: Path) -> Choice:
    """The first model of the agent's chain that can be asked now, or its line.

    A `KIND_NOTHING` choice means neither: `why_not` says why.
    """
    return next(choices(definition, seams, config, repo))


@dataclass(frozen=True)
class Answered:
    """What an agent's chain gave for one question."""

    # The model's answer; empty when the chain ended in its line or in nothing.
    text: str
    # The choice that answered: a model, the line, or nothing.
    choice: Choice
    # Why each model before it failed or was passed over, a line each.
    troubles: tuple[str, ...] = ()

    @property
    def trouble(self) -> str:
        """The last trouble; empty when there was none."""
        return self.troubles[-1] if self.troubles else ""


def first_answer(
    definition: AgentConfig,
    seams: Seams,
    config: Config,
    repo: Path,
    ask: Callable[[Model], str],
) -> Answered:
    """Ask each model of the agent's chain in turn until one answers.

    `ask` puts the question to a model and may raise `ModelError`, which
    passes that model over. It blocks, so an agent calls it on a worker
    thread.
    """
    troubles: list[str] = []
    last = Choice(KIND_NOTHING)
    # How many of the chain's passed-over models have been noted so far.
    noted = 0
    for choice in choices(definition, seams, config, repo):
        last = choice
        troubles.extend(choice.passed[noted:])
        noted = len(choice.passed)
        if choice.model is None:
            break
        try:
            text = ask(choice.model)
        except ModelError as error:
            troubles.append(str(error))
            continue
        if text.strip():
            return Answered(text, choice, tuple(troubles))
        troubles.append(NO_ANSWER)
    return Answered("", last, tuple(troubles))
