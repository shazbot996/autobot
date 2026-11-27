"""The local language model behind an agent: the inference server's (IS-18).

`ServerModel` is the second implementation of the model seam
(`autobot.agents.gemini.Model`), beside Gemini. It asks the language model
on `GAMEON1` through `autobot.server.client.ServerClient`, so an agent does
not change when its model does:

- `ask(prompt, query)` is one chat request that does not stream, with the
  prompt as the system prompt and the query as the one message.
- `chat(request)` sends the conversation whole, as the server keeps none of
  it (IS-5). With `request.stream` the tokens come back as they are
  generated, so the agent can speak sentence by sentence; the last token
  carries the whole answer, with any tool calls.

Every failure is a `ModelError` whose message is one line: the server is
away, it was too slow to its first token, the answer was cut short. The
request is not tried again (IS-4): the agent goes to its fallback
(`autobot.agents.models`).

Only text crosses the house LAN here, to the owner's own machine: no audio
and no cloud service (`CN-1`, `CN-2`, IS-7). Every call blocks, so an agent
makes it on a worker thread.
"""

from __future__ import annotations

from collections.abc import Iterator

from autobot.agents.gemini import NO_ANSWER, ChatAnswer, ModelError, Token
from autobot.server.client import ServerClient, ServerUnavailable
from autobot.server.contract import (
    ROLE_USER,
    ChatRequest,
    Message,
    StreamDone,
    StreamToken,
)


class ServerModel:
    """The inference server's language model, asked through the NUC's client."""

    def __init__(self, client: ServerClient) -> None:
        self._client = client

    def ask(self, prompt: str, query: str) -> str:
        request = ChatRequest(
            system=prompt, messages=(Message(role=ROLE_USER, content=query),)
        )
        answer = self.chat(request)
        assert isinstance(answer, ChatAnswer)
        if not answer.content.strip():
            raise ModelError(NO_ANSWER.replace("Gemini", "the local model"))
        return answer.content

    def chat(self, request: ChatRequest) -> ChatAnswer | Iterator[Token]:
        if request.stream:
            return self._stream(request)
        try:
            response = self._client.chat(request)
        except ServerUnavailable as error:
            raise ModelError(error) from None
        return ChatAnswer(
            response.message.content,
            response.message.tool_calls,
            model=response.model,
        )

    def _stream(self, request: ChatRequest) -> Iterator[Token]:
        text: list[str] = []
        try:
            for event in self._client.stream_chat(request):
                if isinstance(event, StreamToken):
                    text.append(event.text)
                    yield Token(event.text)
                elif isinstance(event, StreamDone):
                    yield Token(
                        "",
                        final=ChatAnswer(
                            "".join(text), event.tool_calls, model=event.model
                        ),
                    )
                    return
        except ServerUnavailable as error:
            raise ModelError(error) from None
