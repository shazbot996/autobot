"""The `introduce` agent: autobot says who he is, from a written script (AG-6). No drawing.

The script is the definition's `prompt`, written by the owner in the Agent
editor (AG-10). It is read aloud as it stands, in the face's local voice,
and its lines are shown in the detail panel. No language model is called
and nothing leaves this machine.
"""

from __future__ import annotations

from autobot.agents.base import Launch
from autobot.agents.response import Response
from autobot.agents.speech import Reply
from autobot.tui.face import FaceContext
from autobot.tui.face_reactions import ReactionSet

NO_SCRIPT = "the introduce agent has no script: write one in the Agent editor"


def script_response(script: str) -> Response | None:
    """The script as a response: spoken as one text, shown line by line; None when empty."""
    lines = tuple(line.strip() for line in script.strip().splitlines())
    spoken = " ".join(line for line in lines if line)
    return Response(spoken, lines) if spoken else None


class Introduce:
    """Read the script aloud once, then end."""

    def __init__(self, launch: Launch) -> None:
        self._script = launch.definition.prompt
        self._reply: Reply | None = None
        self._done = False

    @property
    def reactions(self) -> ReactionSet | None:
        return None

    @property
    def done(self) -> bool:
        return self._done

    def start(self, face: FaceContext) -> None:
        response = script_response(self._script)
        if response is None:
            face.summary.trouble(NO_SCRIPT)
            face.react("trouble")
            self._done = True
            return
        self._reply = Reply(face, response)
        self.tick(face)

    def tick(self, face: FaceContext) -> None:
        if self._done or self._reply is None:
            return
        self._reply.tick(face)
        if self._reply.done:
            face.react("done")
            self._done = True
