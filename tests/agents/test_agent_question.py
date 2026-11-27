"""The `question` agent: the prompt and the question go to the model; two parts come back."""

from pathlib import Path

import pytest

from agents.agent_fakes import FRAME, AgentRig, FakeHearing, FakeModel
from autobot.agents.gemini import Model, ModelError
from autobot.agents.question import (
    ASK_NOW,
    BEEP_SECONDS,
    CANCELLED,
    COUNT_WORDS,
    HOLD_SECONDS,
    INVITE,
    MIC_NOT_OPEN,
    NO_ANSWER,
    NO_HEARING,
    NO_MODEL,
    NO_QUESTION,
    NOT_CONFIRMED,
    THINKING,
    Question,
    beep,
    strip_command,
    track_line,
)
from autobot.agents.registry import AGENTS
from autobot.config import (
    AGENT_CORE,
    AGENT_QUESTION,
    QUESTION_PROMPT,
    ROUTE_AGENTS,
    Config,
)
from autobot.tui.face import Seams
from autobot.tui.runner import listener_hold_argv

ASKED = "I have a question, how far is the moon"


class Models:
    """`Seams.models["gemini"]`: makes the one fake model, and counts how often."""

    def __init__(self, model: FakeModel) -> None:
        self.model = model
        self.made: list[Path] = []

    def __call__(self, config: Config, repo: Path) -> Model:
        self.made.append(repo)
        return self.model


def rig_with(
    tmp_path: Path,
    model: FakeModel,
    extra: str = "",
    hearing: FakeHearing | None = None,
    hostname: str = "nuc",
) -> AgentRig:
    seams = Seams(models={"gemini": Models(model)}, hearing=hearing)
    return AgentRig(tmp_path, extra=extra, seams=seams, hostname=hostname)


def until(rig: AgentRig, agent: Question, stage: str, limit: float = 20.0) -> None:
    """Run frames until the agent reaches `stage`; fail when it does not."""
    waited = 0.0
    while agent.stage != stage:
        assert not agent.done, f"ended before {stage}: {rig.summary}"
        assert waited < limit, f"not at {stage} after {limit:g} s: {rig.summary}"
        rig.frames(FRAME)
        waited += FRAME


def invited(
    tmp_path: Path, hearing: FakeHearing | None = None, model: FakeModel | None = None
) -> tuple[AgentRig, Question, FakeModel, FakeHearing]:
    """A question agent started with no question after the command, at the beep's end."""
    model = model or FakeModel()
    hearing = hearing or FakeHearing()
    rig = rig_with(tmp_path, model, hearing=hearing)
    rig.open_mic()
    agent = rig.run(AGENT_QUESTION, "I have a question")
    assert isinstance(agent, Question)
    until(rig, agent, "recording")
    return rig, agent, model, hearing


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


def test_no_question_after_the_command_invites_one_with_a_countdown_and_a_beep(
    tmp_path: Path,
) -> None:
    rig, _agent, model, hearing = invited(tmp_path)
    # The invitation, then three, two, one, spoken; the model was loading meanwhile.
    assert rig.voice.said == [INVITE, *COUNT_WORDS]
    assert hearing.loads == 1
    assert model.calls == []
    # The listener held its ears for the count and the beep.
    assert listener_hold_argv(HOLD_SECONDS) in rig.runner.calls
    # The last sound played is the beep, and the summary says to ask now.
    tone = rig.audio.played[-1]
    assert tone.seconds == pytest.approx(BEEP_SECONDS)
    assert tone.peak > 0
    assert ASK_NOW in rig.summary
    assert any(line.startswith("Ask me in 3... 2... 1...") for line in rig.summary)
    # The running view shows where it stands: recording the question.
    assert rig.detail[0] == track_line("ASK")
    assert any("REC" in line for line in rig.detail)


def test_the_question_is_recorded_until_the_owner_stops_talking(tmp_path: Path) -> None:
    rig, agent, model, hearing = invited(tmp_path)
    rig.speak(2.0)
    assert agent.stage == "recording"
    # A second and a half of quiet after the speech ends the recording.
    rig.speak(2.0, peak=0)
    rig.finish(agent)
    assert len(hearing.heard) == 1
    assert 3.0 <= hearing.heard[0] <= 4.0
    assert model.calls == [(QUESTION_PROMPT, "how big is the sun")]
    assert rig.summary[-2:] == [THINKING, "It is far."]
    assert rig.detail == ["384,400 km"]
    assert rig.troubles == []
    # The recording is dropped once it is transcribed.
    assert not rig.face.capture.recording
    assert rig.face.capture.take_clip() is None


def test_the_countdown_shows_big_digits_one_beat_a_second(tmp_path: Path) -> None:
    rig = rig_with(tmp_path, FakeModel(), hearing=FakeHearing())
    rig.open_mic()
    agent = rig.run(AGENT_QUESTION, "I have a question")
    assert isinstance(agent, Question)
    until(rig, agent, "counting")
    shown: list[str] = []
    while agent.stage == "counting":
        if rig.detail and rig.detail[0] not in shown:
            shown.append(rig.detail[0])
        rig.frames(FRAME)
    assert shown == [track_line(step) for step in ("3", "2", "1", "BEEP")]


def test_silence_after_the_beep_asks_nothing(tmp_path: Path) -> None:
    rig, agent, model, hearing = invited(tmp_path)
    rig.speak(6.0, peak=0)
    rig.finish(agent)
    assert hearing.heard == []
    assert model.calls == []
    assert rig.summary[-1] == NO_QUESTION
    assert rig.voice.said[-1] == NO_QUESTION


def test_an_empty_transcript_asks_nothing(tmp_path: Path) -> None:
    rig, agent, model, _hearing = invited(tmp_path, FakeHearing(text="  "))
    rig.speak(1.0)
    rig.speak(2.0, peak=0)
    rig.finish(agent)
    assert model.calls == []
    assert rig.summary[-1] == NO_QUESTION


def test_cancel_said_as_the_question_sends_nothing(tmp_path: Path) -> None:
    rig, agent, model, _hearing = invited(tmp_path, FakeHearing(text="Cancel."))
    rig.speak(1.0)
    rig.speak(2.0, peak=0)
    rig.finish(agent)
    assert model.calls == []
    assert rig.summary[-1] == CANCELLED


def test_a_transcription_that_fails_is_one_line_of_trouble(tmp_path: Path) -> None:
    hearing = FakeHearing(error=RuntimeError("faster-whisper model file is missing"))
    rig, agent, model, _hearing = invited(tmp_path, hearing)
    rig.speak(1.0)
    rig.speak(2.0, peak=0)
    rig.finish(agent)
    assert model.calls == []
    assert len(rig.troubles) == 1
    assert "faster-whisper model file is missing" in rig.troubles[0]


def test_cancel_while_recording_drops_the_recording(tmp_path: Path) -> None:
    rig, agent, model, hearing = invited(tmp_path)
    rig.speak(1.0)
    agent.cancel()
    assert agent.done
    assert not rig.face.capture.recording
    assert rig.face.capture.take_clip() is None
    assert hearing.heard == [] and model.calls == []


def test_without_an_open_microphone_it_says_so_and_asks_nothing(
    tmp_path: Path,
) -> None:
    model = FakeModel()
    rig = rig_with(tmp_path, model, hearing=FakeHearing())
    rig.finish(rig.run(AGENT_QUESTION, "I have a question"))
    assert rig.troubles == [MIC_NOT_OPEN]
    assert model.calls == []
    assert rig.voice.said == []


def test_without_hearing_it_says_so(tmp_path: Path) -> None:
    rig = rig_with(tmp_path, FakeModel())
    rig.open_mic()
    rig.finish(rig.run(AGENT_QUESTION, "I have a question"))
    assert rig.troubles == [NO_HEARING]


def test_off_the_nuc_it_refuses_to_record(tmp_path: Path) -> None:
    rig = rig_with(tmp_path, FakeModel(), hearing=FakeHearing(), hostname="laptop")
    rig.open_mic()
    rig.finish(rig.run(AGENT_QUESTION, "I have a question"))
    assert len(rig.troubles) == 1
    assert "runs on the NUC only" in rig.troubles[0]


def test_the_beep_is_a_short_faded_tone() -> None:
    tone = beep(16000)
    assert tone.sample_rate == 16000
    assert tone.seconds == pytest.approx(BEEP_SECONDS)
    # Faded in and out, so it does not click.
    assert abs(int(tone.samples[0])) < 100 and abs(int(tone.samples[-1])) < 100
    assert tone.peak > 5000


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


def test_all_seven_agents_are_registered_in_the_one_shape() -> None:
    # Every agent a definition can name has its code, the core agent last.
    assert set(AGENTS) == set(ROUTE_AGENTS)
    assert tuple(AGENTS)[-1] == AGENT_CORE
    assert [name for name, agent in AGENTS.items() if agent.uses_model] == [
        "question",
        "core",
    ]
    assert [name for name, agent in AGENTS.items() if agent.sends_requests] == [
        "question",
        "weather",
        "core",
    ]
