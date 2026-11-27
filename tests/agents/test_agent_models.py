"""Which model answers for an agent, and the local model over the server (IS-18).

Every model here is a fake, and the server is a fake transport: nothing is
asked of Gemini or of `GAMEON1`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.agent_fakes import AgentRig, FakeModel, fake_models
from autobot.agents.gemini import (
    LAST_MESSAGE,
    SCHEMA_INSTRUCTION,
    AdkModel,
    ChatAnswer,
    ModelError,
    Token,
    one_turn,
    whole,
)
from autobot.agents.local_model import ServerModel
from autobot.agents.models import (
    KIND_LINE,
    KIND_NOTHING,
    NO_ANSWER,
    SERVER_AWAY,
    chain,
    choose,
    first_answer,
)
from autobot.agents.question import Question
from autobot.config import AGENT_QUESTION, AgentConfig, Config
from autobot.server.contract import (
    ChatRequest,
    Message,
    Tokens,
    ToolCall,
)
from autobot.server.watch import DOWN
from autobot.tui.face import Seams
from server.server_fakes import (
    FakeTransport,
    FakeWatch,
    client_on,
    example,
)

ASKED = "I have a question, how far is the moon"
LOCAL = FakeModel(answer='{"summary": "The local model says far.", "detail": []}')
GEMINI = FakeModel(answer='{"summary": "Gemini says far.", "detail": []}')


def question(model: str, *fallback: str) -> str:
    listed = ", ".join(f'"{entry}"' for entry in fallback)
    return f'[[agents]]\nname = "question"\nmodel = "{model}"\nfallback = [{listed}]\n'


def fresh() -> tuple[FakeModel, FakeModel]:
    """A local and a gemini fake of the test's own."""
    return (FakeModel(answer=LOCAL.answer), FakeModel(answer=GEMINI.answer))


def rig_on(
    tmp_path: Path, extra: str, watch: FakeWatch, local: FakeModel, gemini: FakeModel
) -> AgentRig:
    seams = Seams(models=fake_models(gemini=gemini, local=local), watch=watch)
    return AgentRig(tmp_path, extra=extra, seams=seams)


def definition(rig: AgentRig) -> AgentConfig:
    return rig.config.agent(AGENT_QUESTION)


# --- the chain --------------------------------------------------------------------


def test_the_chain_is_the_model_then_the_fallback() -> None:
    core = AgentConfig(
        name="core", model="local", fallback=("gemini", "the server is away")
    )
    assert chain(core) == ("local", "gemini", "the server is away")
    assert chain(AgentConfig(name="time")) == ("none",)


def test_local_is_chosen_while_the_servers_chat_is_ready(tmp_path: Path) -> None:
    local, gemini = fresh()
    rig = rig_on(tmp_path, question("local", "gemini"), FakeWatch(), local, gemini)
    choice = choose(definition(rig), rig.face.seams, rig.config, rig.repo)
    assert (choice.kind, choice.model, choice.passed) == ("local", local, ())


def test_local_is_passed_over_while_the_server_is_away(tmp_path: Path) -> None:
    local, gemini = fresh()
    rig = rig_on(tmp_path, question("local", "gemini"), FakeWatch(DOWN), local, gemini)
    choice = choose(definition(rig), rig.face.seams, rig.config, rig.repo)
    assert (choice.kind, choice.model) == ("gemini", gemini)
    assert choice.why_not == f"{SERVER_AWAY}: the server is away: timed out"


def test_a_chain_that_ends_in_a_line_gives_the_line(tmp_path: Path) -> None:
    local, _gemini = fresh()
    seams = Seams(models=fake_models(local=local), watch=FakeWatch(DOWN))
    rig = AgentRig(
        tmp_path,
        extra=question("local", "gemini", "the server is away"),
        seams=seams,
    )
    choice = choose(definition(rig), seams, rig.config, rig.repo)
    assert (choice.kind, choice.line) == (KIND_LINE, "the server is away")
    # Both models were passed over: the server is away, and no Gemini is set up.
    assert len(choice.passed) == 2
    assert choice.passed[1] == "no gemini model is set up here"


def test_a_chain_with_nothing_to_ask_says_why(tmp_path: Path) -> None:
    rig = AgentRig(tmp_path, extra=question("none"), seams=Seams())
    choice = choose(definition(rig), rig.face.seams, rig.config, rig.repo)
    assert (choice.kind, choice.model) == (KIND_NOTHING, None)
    assert choice.why_not == "the question agent names no model"


def test_a_model_that_fails_is_passed_over_for_the_next(tmp_path: Path) -> None:
    local, gemini = fresh()
    local.error = ModelError("the server is away: timed out")
    rig = rig_on(tmp_path, question("local", "gemini"), FakeWatch(), local, gemini)
    answered = first_answer(
        definition(rig),
        rig.face.seams,
        rig.config,
        rig.repo,
        lambda model: model.ask("prompt", "query"),
    )
    assert answered.choice.kind == "gemini"
    assert answered.text == GEMINI.answer
    assert answered.troubles == ("the server is away: timed out",)
    # Each was asked once: a model that failed is not tried again.
    assert len(local.calls) == 1 and len(gemini.calls) == 1


def test_when_every_model_fails_the_last_trouble_is_kept(tmp_path: Path) -> None:
    local, gemini = fresh()
    local.error = ModelError("the server is away: timed out")
    gemini.answer = "   "
    rig = rig_on(tmp_path, question("local", "gemini"), FakeWatch(), local, gemini)
    answered = first_answer(
        definition(rig),
        rig.face.seams,
        rig.config,
        rig.repo,
        lambda model: model.ask("prompt", "query"),
    )
    assert (answered.text, answered.choice.kind) == ("", KIND_NOTHING)
    assert answered.troubles == ("the server is away: timed out", NO_ANSWER)
    assert answered.trouble == NO_ANSWER


# --- the question agent on its chain (the step's done-when) ----------------------------


def run_question(rig: AgentRig) -> Question:
    agent = rig.run(AGENT_QUESTION, ASKED)
    assert isinstance(agent, Question)
    rig.finish(agent)
    return agent


def test_a_local_question_answers_from_the_local_model_while_the_server_is_up(
    tmp_path: Path,
) -> None:
    local, gemini = fresh()
    rig = rig_on(tmp_path, question("local", "gemini"), FakeWatch(), local, gemini)
    run_question(rig)
    assert rig.voice.said[-1] == "The local model says far."
    assert len(local.calls) == 1 and gemini.calls == []
    assert local.calls[0][1] == "how far is the moon"


def test_with_the_server_down_it_answers_from_gemini(tmp_path: Path) -> None:
    local, gemini = fresh()
    rig = rig_on(tmp_path, question("local", "gemini"), FakeWatch(DOWN), local, gemini)
    run_question(rig)
    assert rig.voice.said[-1] == "Gemini says far."
    assert local.calls == [] and len(gemini.calls) == 1


def test_a_fallback_that_ends_in_a_spoken_line_is_spoken(tmp_path: Path) -> None:
    local, _gemini = fresh()
    seams = Seams(models=fake_models(local=local), watch=FakeWatch(DOWN))
    rig = AgentRig(
        tmp_path,
        extra=question("local", "the server is away"),
        seams=seams,
    )
    run_question(rig)
    assert rig.voice.said[-1] == "the server is away"
    assert rig.summary[-1] == "the server is away"
    assert rig.troubles == [] and local.calls == []


def test_a_question_whose_models_all_fail_says_the_last_trouble(tmp_path: Path) -> None:
    local, gemini = fresh()
    gemini.error = ModelError("Gemini refused: quota")
    rig = rig_on(tmp_path, question("local", "gemini"), FakeWatch(DOWN), local, gemini)
    run_question(rig)
    assert rig.troubles == ["Gemini refused: quota"]


def test_the_question_agent_is_unchanged_by_default(tmp_path: Path) -> None:
    local, gemini = fresh()
    rig = rig_on(tmp_path, "", FakeWatch(), local, gemini)
    assert definition(rig).model == "gemini" and definition(rig).fallback == ()
    run_question(rig)
    assert rig.voice.said[-1] == "Gemini says far."
    assert local.calls == []


# --- the local model over the client ---------------------------------------------------


def server_model(tmp_path: Path, transport: FakeTransport) -> ServerModel:
    return ServerModel(client_on(tmp_path, transport))


def test_ask_is_one_chat_request_that_does_not_stream(tmp_path: Path) -> None:
    transport = FakeTransport()
    model = server_model(tmp_path, transport)
    answer = model.ask("You are autobot.", "how far is the moon")
    assert answer == example("chat-response.json")["message"]["content"]
    (sent,) = transport.sent
    assert sent.path == "/v1/chat"
    assert sent.body == {
        "system": "You are autobot.",
        "messages": [{"role": "user", "content": "how far is the moon"}],
        "stream": False,
    }


def test_chat_sends_the_conversation_whole_with_its_schema_and_tools(
    tmp_path: Path,
) -> None:
    transport = FakeTransport()
    request = ChatRequest.from_json(example("chat-request.json"))
    answer = server_model(tmp_path, transport).chat(request)
    assert answer == ChatAnswer(
        example("chat-response.json")["message"]["content"], model="qwen3:8b"
    )
    assert transport.sent[0].body == example("chat-request.json")


def test_tool_calls_come_back_on_the_answer(tmp_path: Path) -> None:
    response = example("chat-response.json")
    response["message"] = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": "call-1", "name": "set_timer", "arguments": {"minutes": 9}}
        ],
    }
    transport = FakeTransport(answers={"/v1/chat": (200, response)})
    request = ChatRequest(system="", messages=(Message(role="user", content="x"),))
    answer = server_model(tmp_path, transport).chat(request)
    assert isinstance(answer, ChatAnswer)
    assert answer.tool_calls == (
        ToolCall(id="call-1", name="set_timer", arguments={"minutes": 9}),
    )


def test_a_streamed_chat_yields_tokens_then_the_whole_answer(tmp_path: Path) -> None:
    transport = FakeTransport()
    request = ChatRequest(
        system="", messages=(Message(role="user", content="time"),), stream=True
    )
    tokens = server_model(tmp_path, transport).chat(request)
    assert not isinstance(tokens, ChatAnswer)
    got = list(tokens)
    assert [token.text for token in got] == [
        "It's",
        " a quarter",
        " past three.",
        " Anything",
        " else?",
        "",
    ]
    assert got[-1].final == ChatAnswer(
        "It's a quarter past three. Anything else?", model="qwen3:8b"
    )
    assert transport.sent[0].body["stream"] is True


def test_every_failure_of_the_server_is_a_model_error_in_one_line(
    tmp_path: Path,
) -> None:
    transport = FakeTransport(error=TimeoutError("timed out"))
    model = server_model(tmp_path, transport)
    with pytest.raises(ModelError) as away:
        model.ask("prompt", "query")
    assert str(away.value) == "the server is away: timed out"
    request = ChatRequest(
        system="", messages=(Message(role="user", content="x"),), stream=True
    )
    with pytest.raises(ModelError):
        list(model.chat(request))  # type: ignore[arg-type]
    # Each request was made once.
    assert len(transport.sent) == 2
    empty = example("chat-response.json")
    empty["message"]["content"] = "  "
    transport = FakeTransport(answers={"/v1/chat": (200, empty)})
    with pytest.raises(ModelError) as nothing:
        server_model(tmp_path, transport).ask("prompt", "query")
    assert str(nothing.value) == "the local model gave no answer"


# --- a conversation put to a one-turn model -----------------------------------------------


def test_one_message_is_the_query_and_the_system_prompt_the_prompt() -> None:
    request = ChatRequest(
        system="You are autobot.\n",
        messages=(Message(role="user", content="how far is the moon"),),
    )
    assert one_turn(request) == ("You are autobot.\n", "how far is the moon")


def test_a_conversation_is_laid_out_under_its_speakers_with_the_schema() -> None:
    request = ChatRequest.from_json(example("chat-request.json"))
    prompt, query = one_turn(request)
    assert prompt.startswith("You are autobot, a friendly voice assistant")
    assert SCHEMA_INSTRUCTION in prompt and '"summary"' in prompt
    lines = query.splitlines()
    assert lines[0] == "Owner: how long do eggs take to boil"
    assert lines[1].startswith('autobot: {"summary": "About nine minutes')
    assert lines[2] == "Owner: set a timer for that"
    # The assistant's message with only a tool call has no text and is left out.
    assert lines[3] == 'Tool result: {"set": true, "minutes": 9}'
    assert lines[-1] == LAST_MESSAGE


def test_gemini_answers_a_conversation_whole_through_ask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[tuple[str, str]] = []

    def ask(self: AdkModel, prompt: str, query: str) -> str:
        asked.append((prompt, query))
        return "Nine minutes."

    monkeypatch.setattr(AdkModel, "ask", ask)
    rig = AgentRig(tmp_path)
    config: Config = rig.config
    model = AdkModel(config, tmp_path)
    request = ChatRequest(
        system="Be brief.",
        messages=(Message(role="user", content="how long do eggs take"),),
        stream=True,
    )
    # Gemini does not stream here: the answer is whole even when asked to.
    assert model.chat(request) == ChatAnswer("Nine minutes.", model="")
    assert asked == [("Be brief.", "how long do eggs take")]


def test_whole_gathers_a_stream() -> None:
    final = ChatAnswer("a b")
    assert whole(iter((Token("a"), Token(" b"), Token("", final=final)))) == final
    assert whole(iter((Token("a"), Token(" b")))) == ChatAnswer("a b")
    assert whole(final) is final
    assert Tokens(prompt=1, completion=2).prompt == 1
