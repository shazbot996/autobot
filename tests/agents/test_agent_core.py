"""The core agent on the rig: turns, streamed speech, the handoff and tools (IS-19 to IS-21).

Every model is a fake and the watcher is a fake: nothing is asked of the
inference server or of Gemini, and no tool does anything.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from agents.agent_fakes import (
    FRAME,
    AgentRig,
    FakeModel,
    FakeTool,
    fake_models,
)
from autobot.agents.core import (
    ENDED,
    FIRST_TOKEN,
    HANDOFF_CUE,
    LISTENING,
    NOT_CONFIRMED,
    THINKING,
    TOO_MANY_ROUNDS,
    TURN_QUIET_SECONDS,
    Core,
    SummaryStream,
    estimate_tokens,
    handoff_agents,
    read_handoff,
    response_schema,
    summary_so_far,
    system_prompt,
    trim,
)
from autobot.agents.gemini import ChatAnswer, ModelError
from autobot.agents.registry import AGENTS
from autobot.config import AGENT_CORE, CORE_PROMPT, QUESTION_PROMPT
from autobot.server.contract import Message, ToolCall
from autobot.server.watch import DOWN
from autobot.tui.face import Seams
from autobot.tui.runner import FunctionTask
from server.server_fakes import FakeWatch, example

SAID = "how are you today"


def reply(summary: str, *detail: str, **more: object) -> ChatAnswer:
    """A model's answer in the shape the core agent asks for."""
    return ChatAnswer(
        json.dumps({"summary": summary, "detail": list(detail), **more}),
        model="qwen3:8b",
    )


def rig_with(
    tmp_path: Path,
    *,
    local: FakeModel | None = None,
    gemini: FakeModel | None = None,
    watch: FakeWatch | None = None,
    extra: str = "",
    tools: dict[str, FakeTool] | None = None,
) -> AgentRig:
    seams = Seams(
        models=fake_models(gemini=gemini, local=local),
        watch=watch or FakeWatch(),
        tools=dict(tools or {}),
    )
    return AgentRig(tmp_path, extra=extra, seams=seams)


def start(rig: AgentRig, said: str = SAID) -> Core:
    agent = rig.run(AGENT_CORE, said)
    assert isinstance(agent, Core)
    return agent


def until(rig: AgentRig, agent: Core, stage: str, limit: float = 30.0) -> None:
    """Run frames until the agent reaches `stage`; fail when it does not."""
    waited = 0.0
    while agent.stage != stage:
        assert waited < limit, f"not at {stage} after {limit:g} s: {rig.summary}"
        rig.frames(FRAME)
        waited += FRAME


# --- one turn ---------------------------------------------------------------------


def test_the_core_agent_is_registered_and_makes_a_core(tmp_path: Path) -> None:
    rig = rig_with(tmp_path, local=FakeModel(replies=[reply("Fine.")]))
    assert isinstance(AGENTS[AGENT_CORE].make(rig.launch(AGENT_CORE, SAID)), Core)
    assert AGENTS[AGENT_CORE].uses_model and AGENTS[AGENT_CORE].sends_requests


def test_a_turn_is_answered_by_the_local_model_and_spoken(tmp_path: Path) -> None:
    local = FakeModel(replies=[reply("I am well. And you?", "mood: good")])
    rig = rig_with(tmp_path, local=local)
    agent = start(rig)
    until(rig, agent, "listening")
    assert rig.voice.said == ["I am well.", "And you?"]
    assert rig.summary[-4:] == [THINKING, "I am well.", "And you?", LISTENING]
    assert rig.detail[0] == "mood: good"
    assert rig.detail[1] == "answered by the local model (qwen3:8b)"
    assert rig.detail[2].startswith("first token after ")
    (request,) = local.chats
    assert request.stream is True
    assert request.system.startswith(CORE_PROMPT.rstrip())
    assert '{"summary": "...", "detail": ["...", "..."]}' in request.system
    assert request.messages == (Message(role="user", content=SAID),)
    assert request.response_schema == response_schema(["question"])
    assert agent.first_token_seconds is not None


def test_the_first_sentence_is_spoken_before_the_stream_ends(tmp_path: Path) -> None:
    release = threading.Event()
    reached = threading.Event()

    def gate(number: int) -> None:
        # After the fifth token the first sentence is whole: hold the stream there.
        if number == 4:
            reached.set()
            assert release.wait(timeout=10.0)

    local = FakeModel(replies=[reply("It is three. Anything else?")], on_token=gate)
    rig = rig_with(tmp_path, local=local)
    # A real thread, so that the stream and the frames run side by side.
    rig.face.spawn = FunctionTask
    agent = start(rig)

    def wait_for(done: Callable[[], bool]) -> None:
        deadline = time.monotonic() + 10.0
        while not done():
            assert time.monotonic() < deadline, rig.summary
            rig.frames(FRAME)
            time.sleep(0.002)

    assert reached.wait(timeout=10.0)
    wait_for(lambda: rig.voice.said == ["It is three."])
    # The model is still held in the middle of its answer.
    assert not release.is_set() and agent.stage == "thinking"
    assert "It is three." in rig.summary and "Anything else?" not in rig.summary
    assert any(line.startswith("first token after ") for line in rig.detail)
    release.set()
    wait_for(lambda: agent.stage == "listening")
    assert rig.voice.said == ["It is three.", "Anything else?"]


def test_the_time_to_the_first_token_is_shown(tmp_path: Path) -> None:
    rig = rig_with(tmp_path, local=FakeModel(replies=[reply("Fine.")]))
    agent = start(rig)
    until(rig, agent, "listening")
    assert agent.first_token_seconds == 0.0
    assert FIRST_TOKEN.format(seconds=0.0) in rig.detail


# --- the conversation -------------------------------------------------------------


def test_the_previous_turn_is_in_the_next_request(tmp_path: Path) -> None:
    first, second = reply("It is sunny."), reply("Tomorrow it rains.")
    local = FakeModel(replies=[first, second])
    rig = rig_with(tmp_path, local=local)
    agent = start(rig, "what is the weather like")
    until(rig, agent, "listening")
    # What is heard is gathered until the owner has paused.
    rig.hear("and what")
    rig.frames(0.5)
    rig.hear("about tomorrow")
    assert len(local.chats) == 1
    rig.frames(TURN_QUIET_SECONDS + 0.2)
    until(rig, agent, "listening")
    assert local.chats[1].messages == (
        Message(role="user", content="what is the weather like"),
        Message(role="assistant", content=first.content),
        Message(role="user", content="and what about tomorrow"),
    )
    assert agent.messages[-1] == Message(role="assistant", content=second.content)
    assert rig.voice.said == ["It is sunny.", "Tomorrow it rains."]
    assert not agent.done


def test_the_session_ends_when_the_listening_time_passes_with_no_speech(
    tmp_path: Path,
) -> None:
    rig = rig_with(tmp_path, local=FakeModel(replies=[reply("Fine.")]))
    agent = start(rig)
    until(rig, agent, "listening")
    seconds = rig.config.server.core_listen_seconds
    assert seconds == 10.0
    rig.frames(seconds - 0.5)
    assert not agent.done
    rig.frames(1.0)
    assert agent.done and agent.stage == "done"
    assert rig.summary[-1] == ENDED


def test_the_listening_time_is_its_own_setting(tmp_path: Path) -> None:
    rig = rig_with(tmp_path, local=FakeModel(replies=[reply("Fine.")]))
    agent = start(rig)
    until(rig, agent, "listening")
    # What was heard while autobot thought and spoke is not a turn.
    assert not rig.face.heard
    short = AgentRig(
        tmp_path,
        extra="[server]\ncore_listen_seconds = 2\n",
        seams=Seams(
            models=fake_models(local=FakeModel(replies=[reply("Fine.")])),
            watch=FakeWatch(),
        ),
    )
    other = start(short)
    until(short, other, "listening")
    short.frames(2.5)
    assert other.done and not agent.done


def test_cancel_ends_the_session_at_once(tmp_path: Path) -> None:
    rig = rig_with(tmp_path, local=FakeModel(replies=[reply("Fine.")]))
    agent = start(rig)
    until(rig, agent, "listening")
    agent.cancel()
    assert agent.done


def test_old_turns_are_dropped_until_the_conversation_fits_the_context() -> None:
    turns = []
    for number in range(6):
        turns.append(Message(role="user", content=f"question {number} " + "x" * 400))
        turns.append(Message(role="assistant", content=f"answer {number} " + "y" * 400))
    turns.append(Message(role="user", content="the newest question"))
    everything = sum(estimate_tokens(turn.content) + 4 for turn in turns)
    assert trim(turns, everything) == tuple(turns)
    kept = trim(turns, 400)
    # The oldest go first, and what is kept starts at one of the owner's turns.
    assert kept == tuple(turns[-len(kept) :])
    assert kept[0].role == "user" and kept[-1].content == "the newest question"
    assert 1 < len(kept) < len(turns)
    # The newest turn is kept whatever its size.
    assert trim(turns, 1) == (turns[-1],)
    assert estimate_tokens("") == 0 and estimate_tokens("abcde") == 2


def test_a_long_conversation_is_trimmed_in_the_request(tmp_path: Path) -> None:
    rig = AgentRig(
        tmp_path,
        extra="[server]\nllm_context = 2048\n",
        seams=Seams(models=fake_models(local=FakeModel()), watch=FakeWatch()),
    )
    agent = Core(rig.launch(AGENT_CORE, "hello"))
    for number in range(40):
        agent.messages.append(
            Message(role="user", content=f"turn {number} " + "x" * 300)
        )
        agent.messages.append(Message(role="assistant", content="y" * 300))
    agent.messages.append(Message(role="user", content="the newest"))
    request = agent.request(rig.config)
    assert request.messages[-1].content == "the newest"
    assert request.messages[0].role == "user"
    assert 1 < len(request.messages) < len(agent.messages)
    # The conversation itself is kept whole on the NUC.
    assert len(agent.messages) == 81


# --- the fallback -----------------------------------------------------------------


def test_with_the_server_away_and_no_gemini_it_says_the_server_is_away(
    tmp_path: Path,
) -> None:
    local = FakeModel(replies=[reply("Fine.")])
    rig = rig_with(tmp_path, local=local, watch=FakeWatch(DOWN))
    agent = start(rig)
    rig.finish(agent)
    assert rig.voice.said == ["the server is away"]
    assert rig.summary[-1] == "the server is away"
    assert local.chats == [] and rig.troubles == []


def test_with_the_server_away_gemini_answers_whole(tmp_path: Path) -> None:
    local = FakeModel(replies=[reply("Fine.")])
    gemini = FakeModel(replies=[ChatAnswer('{"summary": "Gemini here. Hello!"}')])
    rig = rig_with(tmp_path, local=local, gemini=gemini, watch=FakeWatch(DOWN))
    agent = start(rig)
    until(rig, agent, "listening")
    assert rig.voice.said == ["Gemini here.", "Hello!"]
    assert local.chats == [] and len(gemini.chats) == 1
    assert "answered by the gemini model" in rig.detail


def test_a_local_model_that_fails_before_its_first_token_falls_to_gemini(
    tmp_path: Path,
) -> None:
    local = FakeModel(replies=[ModelError("the chat stopped: timed out")])
    gemini = FakeModel(replies=[ChatAnswer('{"summary": "Gemini here."}')])
    rig = rig_with(tmp_path, local=local, gemini=gemini)
    agent = start(rig)
    until(rig, agent, "listening")
    assert rig.voice.said == ["Gemini here."]
    # Each was asked once: the request is not tried again on the same model.
    assert len(local.chats) == 1 and len(gemini.chats) == 1


def test_a_stream_cut_after_it_began_is_trouble_and_is_not_asked_again(
    tmp_path: Path,
) -> None:
    local = FakeModel(
        replies=[reply("It is three. Anything else?")],
        stream_error=ModelError("the chat ended before it was done"),
        stream_error_after=5,
    )
    gemini = FakeModel(replies=[ChatAnswer('{"summary": "Gemini here."}')])
    rig = rig_with(tmp_path, local=local, gemini=gemini)
    agent = start(rig)
    rig.finish(agent)
    assert rig.troubles == ["the chat ended before it was done"]
    assert gemini.chats == []


# --- the handoff (IS-20) ------------------------------------------------------------

HANDOFF = {"agent": "question", "query": "how far away is the moon"}
ASK_FIRST = (
    '[[agents]]\nname = "question"\nmodel = "gemini"\nconfirm_request = true\n'
    'confirm_request_question = "This may cost a few cents. Ask?"\n'
)


def test_a_handoff_goes_to_the_question_agents_model_after_a_yes(
    tmp_path: Path,
) -> None:
    local = FakeModel(replies=[reply("Let me ask about that.", handoff=HANDOFF)])
    gemini = FakeModel(answer='{"summary": "About 384,400 km.", "detail": ["far"]}')
    rig = rig_with(tmp_path, local=local, gemini=gemini, extra=ASK_FIRST)
    agent = start(rig, "how far is the moon")
    until(rig, agent, "confirming")
    # The cue is shown before anything is asked of anyone.
    assert HANDOFF_CUE.format(agent="question") in rig.summary
    assert gemini.calls == []
    rig.frames(2.0)
    assert "This may cost a few cents. Ask?" in rig.voice.said
    rig.hear("yes")
    until(rig, agent, "listening")
    # The question agent's own prompt, and the query the core agent gave.
    assert gemini.calls == [(QUESTION_PROMPT, "how far away is the moon")]
    assert rig.voice.said[-1] == "About 384,400 km."
    assert "far" in rig.detail
    assert agent.messages[-1].content == gemini.answer
    # The local model was asked once, and never to call a cloud service.
    assert len(local.chats) == 1 and gemini.chats == []


def test_a_no_to_the_handoff_sends_nothing(tmp_path: Path) -> None:
    local = FakeModel(replies=[reply("Let me ask about that.", handoff=HANDOFF)])
    gemini = FakeModel()
    rig = rig_with(tmp_path, local=local, gemini=gemini, extra=ASK_FIRST)
    agent = start(rig, "how far is the moon")
    until(rig, agent, "confirming")
    rig.frames(2.0)
    rig.hear("no")
    until(rig, agent, "listening")
    assert gemini.calls == []
    assert NOT_CONFIRMED.format(agent="question") in rig.summary


def test_a_handoff_without_a_confirmation_is_sent_at_once(tmp_path: Path) -> None:
    local = FakeModel(replies=[reply("Let me ask.", handoff=HANDOFF)])
    gemini = FakeModel(answer='{"summary": "About 384,400 km.", "detail": []}')
    rig = rig_with(tmp_path, local=local, gemini=gemini)
    agent = start(rig, "how far is the moon")
    until(rig, agent, "listening")
    assert len(gemini.calls) == 1
    assert rig.voice.said == ["Let me ask.", "About 384,400 km."]


def test_a_handoff_outside_the_closed_list_is_not_followed(tmp_path: Path) -> None:
    outside = {"agent": "weather", "query": "is it raining"}
    local = FakeModel(replies=[reply("I will check.", handoff=outside)])
    gemini = FakeModel()
    rig = rig_with(tmp_path, local=local, gemini=gemini)
    agent = start(rig)
    until(rig, agent, "listening")
    assert gemini.calls == []
    assert HANDOFF_CUE.format(agent="weather") not in rig.summary


def test_a_handoff_whose_agent_cannot_answer_is_one_line_and_the_session_goes_on(
    tmp_path: Path,
) -> None:
    local = FakeModel(replies=[reply("Let me ask.", handoff=HANDOFF)])
    gemini = FakeModel(error=ModelError("Gemini refused: quota"))
    rig = rig_with(tmp_path, local=local, gemini=gemini)
    agent = start(rig)
    until(rig, agent, "listening")
    assert rig.troubles == [
        "the question agent could not answer: Gemini refused: quota"
    ]
    assert not agent.done


def test_the_closed_list_is_the_agents_whose_model_is_gemini(tmp_path: Path) -> None:
    rig = rig_with(tmp_path)
    assert [agent.name for agent in handoff_agents(rig.config)] == ["question"]
    schema = response_schema(["question"])
    assert schema == example("chat-request.json")["response_schema"]
    assert "handoff" not in response_schema([])["properties"]
    prompt = system_prompt(rig.config.agent(AGENT_CORE), handoff_agents(rig.config))
    assert "- question: Asks a language model your question" in prompt
    content = json.dumps({"summary": "x", "detail": [], "handoff": HANDOFF})
    assert read_handoff(content, ["question"]) == (
        "question",
        "how far away is the moon",
    )
    assert read_handoff(content, []) is None
    assert read_handoff("not json", ["question"]) is None
    assert (
        read_handoff('{"summary": "x", "handoff": {"agent": "question"}}', ["question"])
        is None
    )


# --- tools (IS-21) -------------------------------------------------------------------

WITH_TOOL = f'''
[[agents]]
name = "core"
model = "local"
fallback = ["gemini", "the server is away"]
prompt = """
{CORE_PROMPT}"""

[[agents.tools]]
name = "set_timer"
description = "Starts a timer on the NUC that rings when it ends."

[[agents.tools.parameters]]
name = "minutes"
type = "integer"
required = true
'''
CALL = ToolCall(id="call-1", name="set_timer", arguments={"minutes": 9})


def test_a_tool_call_is_run_on_the_nuc_for_one_round(tmp_path: Path) -> None:
    local = FakeModel(
        replies=[ChatAnswer("", (CALL,)), reply("Your nine minute timer is running.")]
    )
    tool = FakeTool(result='{"set": true, "minutes": 9}')
    rig = rig_with(tmp_path, local=local, extra=WITH_TOOL, tools={"set_timer": tool})
    agent = start(rig, "set a timer for nine minutes")
    until(rig, agent, "listening")
    assert tool.calls == [{"minutes": 9}]
    first, second = local.chats
    # The tool goes out as its schema, and its result comes back as a message.
    assert [sent.name for sent in first.tools] == ["set_timer"]
    assert first.tools[0].parameters[0].required
    assert second.messages == (
        Message(role="user", content="set a timer for nine minutes"),
        Message(role="assistant", content="", tool_calls=(CALL,)),
        Message(
            role="tool", content='{"set": true, "minutes": 9}', tool_call_id="call-1"
        ),
    )
    assert rig.voice.said == ["Your nine minute timer is running."]


def test_a_tool_that_is_missing_or_fails_is_told_to_the_model(tmp_path: Path) -> None:
    other = ToolCall(id="call-2", name="open_door", arguments={})
    local = FakeModel(replies=[ChatAnswer("", (CALL, other)), reply("I could not.")])
    tool = FakeTool(error=RuntimeError("the timer is broken\nbadly"))
    rig = rig_with(tmp_path, local=local, extra=WITH_TOOL, tools={"set_timer": tool})
    agent = start(rig)
    until(rig, agent, "listening")
    results = [message.content for message in local.chats[1].messages[-2:]]
    assert results == [
        "error: the timer is broken badly",
        "error: there is no tool named open_door",
    ]


def test_tool_calls_stop_after_the_configured_rounds_with_a_trouble_line(
    tmp_path: Path,
) -> None:
    local = FakeModel(replies=[ChatAnswer("", (CALL,))])
    tool = FakeTool()
    rig = rig_with(tmp_path, local=local, extra=WITH_TOOL, tools={"set_timer": tool})
    rounds = rig.config.server.tool_rounds
    assert rounds == 3
    agent = start(rig)
    until(rig, agent, "listening")
    assert len(tool.calls) == rounds
    assert rig.troubles == [TOO_MANY_ROUNDS.format(rounds=rounds)]
    assert len(local.chats) == rounds + 1


# --- the summary as it streams -----------------------------------------------------------


def test_the_summary_is_read_out_of_json_that_is_still_arriving() -> None:
    assert summary_so_far('{"summ') == ""
    assert summary_so_far('{"summary": "It is th') == "It is th"
    assert summary_so_far('{"summary": "A \\"quoted\\" word\\n') == 'A "quoted" word\n'
    # An escape cut in two waits for its other half.
    assert summary_so_far('{"summary": "caf\\u00') == "caf"
    assert summary_so_far('{"summary": "caf\\u00e9", "detail": []}') == "café"
    assert summary_so_far('{"detail": [], "summary": "Last."}') == "Last."
    assert summary_so_far("Plain words. More.") == "Plain words. More."
    assert summary_so_far("```json\n{") == ""


def test_sentences_are_cut_at_their_ends_as_they_arrive() -> None:
    stream = SummaryStream()
    assert stream.feed('{"summary": "It is 3.5 degrees') == []
    assert stream.feed(" out. Do you") == ["It is 3.5 degrees out."]
    assert stream.feed(' want more?" ') == []
    assert stream.feed(', "detail": []}') == []
    assert stream.finish("It is 3.5 degrees out. Do you want more?") == [
        "Do you want more?"
    ]
    assert stream.finish() == []


def test_a_sentence_ends_at_punctuation_followed_by_a_space() -> None:
    stream = SummaryStream()
    assert stream.feed("Really?! Yes. ") == ["Really?!", "Yes."]
    assert stream.feed('He said "go." Then') == ['He said "go."']
    assert stream.finish() == ["Then"]


def test_an_answer_that_could_not_be_read_as_it_came_is_said_whole_at_the_end() -> None:
    stream = SummaryStream()
    assert stream.feed('```json\n{"summary": "Fenced. Twice."}\n```') == []
    assert stream.finish("Fenced. Twice.") == ["Fenced.", "Twice."]


def test_a_plain_answer_is_spoken_sentence_by_sentence(tmp_path: Path) -> None:
    local = FakeModel(replies=[ChatAnswer("I am well. Thanks for asking!")])
    rig = rig_with(tmp_path, local=local)
    agent = start(rig)
    until(rig, agent, "listening")
    assert rig.voice.said == ["I am well.", "Thanks for asking!"]


def test_an_empty_answer_is_one_line_of_trouble(tmp_path: Path) -> None:
    rig = rig_with(tmp_path, local=FakeModel(replies=[ChatAnswer("  ")]))
    agent = start(rig)
    rig.finish(agent)
    assert rig.troubles == ["the language model gave no answer"]


def test_with_no_model_at_all_it_says_why(tmp_path: Path) -> None:
    rig = AgentRig(
        tmp_path,
        extra='[[agents]]\nname = "core"\nmodel = "local"\nfallback = []\n',
        seams=Seams(watch=FakeWatch()),
    )
    agent = start(rig)
    rig.finish(agent)
    assert rig.troubles == ["no local model is set up here"]


@pytest.mark.parametrize("said", ["  how   are you ", "how are you"])
def test_the_owners_words_are_squeezed(tmp_path: Path, said: str) -> None:
    local = FakeModel(replies=[reply("Fine.")])
    rig = rig_with(tmp_path, local=local)
    agent = start(rig, said)
    until(rig, agent, "listening")
    assert local.chats[0].messages[0].content == "how are you"
    assert agent.messages[0] == Message(role="user", content="how are you")
