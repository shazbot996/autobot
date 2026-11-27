"""The `question` agent: a question answered by a language model (AG-5). No drawing.

The owner says the command and then the question. Each step runs in `tick`,
which never blocks:

1. **The query.** What was said after the command word is the question:
   the command word and what precedes it are left out, so "I have a
   question, how far is the moon" asks `how far is the moon`. With nothing
   after the command, the agent says so and ends.
2. **The confirmation.** With `confirm_request` on, the definition's
   question is asked first (AG-13): anything but a yes ends the agent with
   nothing sent.
3. **Asking.** The definition's prompt and the query go to the language
   model on a thread, with the eyes *working* (`Seams.model`: Gemini,
   through the ADK on Vertex AI, as autobot's service account, in the
   interface). A failure is one *trouble* line.
4. **The answer.** The model's text is taken apart into the two parts of
   AG-4 (`response.parse_model_response`): the summary is spoken in the
   local voice and the detail is laid out in the panel.

The text sent is the listener's local transcript and the prompt, and
nothing else: no audio leaves the machine, and the answer is spoken here
(`CN-1`, `CN-2`). Nothing is remembered between questions (AG-11).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from autobot.agents.base import Launch
from autobot.agents.confirm import Confirmation
from autobot.agents.response import Response, parse_model_response
from autobot.agents.speech import Reply
from autobot.routing import normalise
from autobot.tui.face import FaceContext
from autobot.tui.face_reactions import ReactionSet
from autobot.tui.runner import FunctionTask

type Stage = Literal["confirming", "asking", "answer", "done"]

THINKING = "Thinking..."
ASKED = 'You asked: "{query}"'
NO_QUESTION = "I didn't hear a question. Say question, and then what you want to know."
NO_MODEL = "the question agent has no language model here"
NO_ANSWER = "the language model gave no answer"
NOT_CONFIRMED = "Not confirmed: nothing was sent."


def strip_command(transcript: str, commands: Iterable[str]) -> str:
    """What was said after the command word: the question itself.

    The first of `commands` that stands in `transcript` is found by the
    rules' matching (lower-cased, punctuation and spaces ignored, from a
    word's start), and it and everything before it are left out. A
    transcript in which none stands is returned whole.
    """
    words = transcript.split()
    parts = [normalise(word).replace(" ", "") for word in words]
    for command in commands:
        wanted = normalise(command).replace(" ", "")
        if not wanted:
            continue
        for start in range(len(words)):
            if not parts[start]:
                continue
            taken = ""
            end = start
            while len(taken) < len(wanted) and end < len(words):
                taken += parts[end]
                end += 1
            if taken.startswith(wanted):
                return " ".join(words[end:]).lstrip(" ,;:.!?-").strip()
    return transcript.strip()


class Question:
    """Ask the model one question and answer in two parts, or end with one line."""

    def __init__(self, launch: Launch) -> None:
        self._definition = launch.definition
        self._transcript = launch.transcript
        self.query = ""
        self.stage: Stage = "asking"
        self._task: FunctionTask | None = None
        self._confirmation: Confirmation | None = None
        self._reply: Reply | None = None
        self._done = False

    @property
    def reactions(self) -> ReactionSet | None:
        return None

    @property
    def done(self) -> bool:
        return self._done

    def start(self, face: FaceContext) -> None:
        name = self._definition.name
        commands = [
            route.command for route in face.config.routes if route.agent == name
        ]
        self.query = strip_command(self._transcript, commands)
        if not self.query:
            self.stage = "answer"
            self._reply = Reply(face, Response(NO_QUESTION))
            self._answering(face)
            return
        if face.seams.model is None:
            self._trouble(face, NO_MODEL)
            return
        face.detail.append(ASKED.format(query=self.query))
        if self._definition.confirm_request:
            self.stage = "confirming"
            self._confirmation = face.confirm(self._definition.confirm_request_question)
            self._confirming(face)
            return
        self._ask(face)

    def tick(self, face: FaceContext) -> None:
        if self._done:
            return
        if self.stage == "confirming":
            self._confirming(face)
        elif self.stage == "asking":
            self._asked(face)
        elif self.stage == "answer":
            self._answering(face)

    def cancel(self) -> None:
        """The owner said `cancel`: an answer still on its way is dropped unread."""
        self._task = None
        self._done = True

    # --- the steps ----------------------------------------------------------------

    def _confirming(self, face: FaceContext) -> None:
        confirmation = self._confirmation
        assert confirmation is not None
        confirmation.tick(face)
        if not confirmation.done:
            return
        self._confirmation = None
        if confirmation.outcome != "yes":
            face.summary.add(NOT_CONFIRMED)
            face.react("done")
            self._done = True
            return
        self._ask(face)

    def _ask(self, face: FaceContext) -> None:
        make = face.seams.model
        assert make is not None
        config, repo = face.config, face.repo
        prompt, query = self._definition.prompt, self.query

        def ask() -> str:
            return make(config, repo).ask(prompt, query)

        self.stage = "asking"
        face.summary.add(THINKING)
        face.react("working")
        self._task = face.spawn(ask)
        self._asked(face)

    def _asked(self, face: FaceContext) -> None:
        task = self._task
        if task is None:
            return
        result = task.poll()
        if result is None:
            return
        self._task = None
        if not result.ok:
            self._trouble(face, result.summary())
            return
        text = task.value if isinstance(task.value, str) else ""
        if not text.strip():
            self._trouble(face, NO_ANSWER)
            return
        self.stage = "answer"
        self._reply = Reply(face, parse_model_response(text))
        self._answering(face)

    def _answering(self, face: FaceContext) -> None:
        assert self._reply is not None
        self._reply.tick(face)
        if self._reply.done:
            self.stage = "done"
            face.react("done")
            self._done = True

    def _trouble(self, face: FaceContext, reason: str) -> None:
        self.stage = "done"
        face.summary.trouble(reason)
        face.react("trouble")
        self._done = True
