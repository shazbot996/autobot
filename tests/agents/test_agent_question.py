"""The `question` agent: the prompt and the question go to the model; two parts come back."""

from pathlib import Path

import pytest

from agents.agent_fakes import AgentRig, FakeModel
from autobot.agents.gemini import Model, ModelError
from autobot.agents.question import (
    NO_ANSWER,
    NO_MODEL,
    NO_QUESTION,
    NOT_CONFIRMED,
    THINKING,
    Question,
    strip_command,
)
from autobot.agents.registry import AGENTS
from autobot.config import AGENT_QUESTION, QUESTION_PROMPT, ROUTE_AGENTS, Config
from autobot.tui.face import Seams

ASKED = "I have a question, how far is the moon"


class Models:
    """`Seams.model`: makes the one fake model, and counts how often it was made."""

    def __init__(self, model: FakeModel) -> None:
        self.model = model
        self.made: list[Path] = []

    def __call__(self, config: Config, repo: Path) -> Model:
        self.made.append(repo)
        return self.model


def rig_with(tmp_path: Path, model: FakeModel, extra: str = "") -> AgentRig:
    return AgentRig(tmp_path, extra=extra, seams=Seams(model=Models(model)))


@pytest.mark.parametrize(
    ("transcript", "query"),
    [
        ("I have a question, how far is the moon", "how far is the moon"),
        ("question how far is the moon", "how far is the moon"),
        ("Question: what's 2 + 2?", "what's 2 + 2?"),
        ("a quick question. Who wrote Hamlet", "Who wrote Hamlet"),
        ("question", ""),
        ("I have a question.", ""),
        ("how far is the moon", "how far is the moon"),
    ],
)
def test_the_query_is_what_was_said_after_the_command(
    transcript: str, query: str
) -> None:
    assert strip_command(transcript, ["question"]) == query


def test_a_command_of_several_words_is_left_out_whole() -> None:
    assert strip_command(
        "hey, ask the model why is the sky blue", ["ask the model"]
    ) == ("why is the sky blue")
    # The first of the agent's commands that stands in the transcript counts.
    assert strip_command("tell me, why", ["question", "tell me"]) == "why"


def test_the_model_gets_the_definitions_prompt_and_the_question(tmp_path: Path) -> None:
    model = FakeModel()
    rig = rig_with(tmp_path, model)
    agent = rig.run(AGENT_QUESTION, ASKED)
    assert isinstance(agent, Question)
    rig.finish(agent)
    assert model.calls == [(QUESTION_PROMPT, "how far is the moon")]
    # A JSON answer: the summary is spoken, the detail laid out.
    assert rig.summary == [THINKING, "It is far."]
    assert rig.voice.said == ["It is far."]
    assert rig.detail == ["384,400 km"]
    assert rig.troubles == []


def test_only_the_transcript_and_the_prompt_are_sent(tmp_path: Path) -> None:
    model = FakeModel()
    extra = '[[agents]]\nname = "question"\nprompt = "Answer briefly."\n'
    rig = rig_with(tmp_path, model, extra)
    rig.finish(rig.run(AGENT_QUESTION, "question who are you"))
    assert model.calls == [("Answer briefly.", "who are you")]


def test_a_plain_answer_still_gives_both_parts(tmp_path: Path) -> None:
    text = "The moon is about 384,400 km away. That is thirty Earths side by side."
    rig = rig_with(tmp_path, FakeModel(answer=text))
    rig.finish(rig.run(AGENT_QUESTION, ASKED))
    assert rig.summary[-1] == "The moon is about 384,400 km away."
    assert rig.voice.said == ["The moon is about 384,400 km away."]
    assert " ".join(rig.detail) == text


def test_a_long_detail_is_laid_out_to_the_panel_not_scrolled(tmp_path: Path) -> None:
    lines = ", ".join(f'"line {number} of the detail"' for number in range(1, 41))
    rig = rig_with(
        tmp_path, FakeModel(answer=f'{{"summary": "Here.", "detail": [{lines}]}}')
    )
    rig.face.detail.page = 5
    rig.face.detail.cols = 12
    rig.finish(rig.run(AGENT_QUESTION, ASKED))
    assert len(rig.detail) == 5
    assert all(len(line) <= 12 for line in rig.detail)
    assert rig.detail[0] == "line 1 of"


def test_a_model_error_is_one_line_of_trouble(tmp_path: Path) -> None:
    error = ModelError(
        "refused: no key at secrets/service-account-autobot-agent.json yet"
    )
    rig = rig_with(tmp_path, FakeModel(error=error))
    agent = rig.run(AGENT_QUESTION, ASKED)
    rig.finish(agent)
    assert rig.troubles == [str(error)]
    assert rig.voice.said == []


def test_an_empty_answer_is_one_line(tmp_path: Path) -> None:
    rig = rig_with(tmp_path, FakeModel(answer="  \n"))
    rig.finish(rig.run(AGENT_QUESTION, ASKED))
    assert rig.troubles == [NO_ANSWER]


def test_nothing_after_the_command_says_so_and_asks_nothing(tmp_path: Path) -> None:
    model = FakeModel()
    rig = rig_with(tmp_path, model)
    rig.finish(rig.run(AGENT_QUESTION, "I have a question"))
    assert rig.summary == [NO_QUESTION]
    assert rig.voice.said == [NO_QUESTION]
    assert model.calls == []


def test_with_the_request_confirmation_on_a_no_sends_nothing(tmp_path: Path) -> None:
    model = FakeModel()
    extra = '[[agents]]\nname = "question"\nconfirm_request = true\n'
    rig = rig_with(tmp_path, model, extra)
    agent = rig.run(AGENT_QUESTION, ASKED)
    rig.frames(2.0)
    question = "This may cost a few dollars. Are you sure you want to run this?"
    assert rig.voice.said == [question]
    assert model.calls == []
    rig.hear("no way")
    rig.finish(agent)
    assert model.calls == []
    assert rig.summary[-1] == NOT_CONFIRMED


def test_a_yes_sends_the_question(tmp_path: Path) -> None:
    model = FakeModel()
    extra = '[[agents]]\nname = "question"\nconfirm_request = true\n'
    rig = rig_with(tmp_path, model, extra)
    agent = rig.run(AGENT_QUESTION, ASKED)
    rig.frames(2.0)
    rig.hear("absolutely")
    rig.finish(agent)
    assert len(model.calls) == 1
    assert rig.summary[-1] == "It is far."


def test_without_a_model_it_says_so(tmp_path: Path) -> None:
    rig = AgentRig(tmp_path, seams=Seams())
    assert rig.run(AGENT_QUESTION, ASKED).done
    assert rig.troubles == [NO_MODEL]


def test_cancel_drops_an_answer_on_its_way(tmp_path: Path) -> None:
    rig = rig_with(tmp_path, FakeModel())
    agent = Question(rig.launch(AGENT_QUESTION, ASKED))
    agent.cancel()
    assert agent.done


def test_the_question_agent_reasons_and_sends_requests() -> None:
    agent = AGENTS[AGENT_QUESTION]
    assert agent.uses_model and agent.sends_requests


def test_all_six_agents_are_registered_in_the_one_shape() -> None:
    assert tuple(AGENTS) == ROUTE_AGENTS
    assert [name for name, agent in AGENTS.items() if agent.uses_model] == ["question"]
    assert [name for name, agent in AGENTS.items() if agent.sends_requests] == [
        "question",
        "weather",
    ]
