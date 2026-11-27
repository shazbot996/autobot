"""The `camera` agent: it opens the camera application on the NUC (AG-6). No drawing.

The camera application is the peer project's: a proxy server and a page in
Firefox showing the four outside cameras. The agent starts the definition's
`commands`, one a line, in order, in the definition's `folder` under the
repository root, through the face's one way of starting a local command
(`Seams.launch`), detached: it waits for neither to end, and it stops
nothing and closes nothing. What the commands print goes to
`run/agents.log`.

It runs on the NUC only and refuses anywhere else with the usual line
(TS-11). A folder that does not exist, a command that cannot be read or a
start that fails is one *trouble* line. Nothing leaves this machine.
"""

from __future__ import annotations

import shlex

from autobot.agents.base import Launch
from autobot.agents.response import Response
from autobot.agents.speech import Reply
from autobot.tui.face import FaceContext
from autobot.tui.face_reactions import ReactionSet
from autobot.tui.runner import AGENTS_LOG

# What the off-NUC refusal names.
WHAT = "the camera agent"
OPENING = "Opening the cameras in Firefox."
NO_COMMANDS = "the camera agent has no commands: set them in the Agent editor"
NO_FOLDER = "the camera agent's folder {folder} does not exist"
BAD_COMMAND = "the camera agent cannot read the command {line!r}: {reason}"


def command_lines(commands: str) -> list[str]:
    """The definition's commands, one a line, without the blank lines."""
    return [line.strip() for line in commands.splitlines() if line.strip()]


class Camera:
    """Start the camera application's commands, say so, then end."""

    def __init__(self, launch: Launch) -> None:
        self._definition = launch.definition
        self._reply: Reply | None = None
        self._done = False

    @property
    def reactions(self) -> ReactionSet | None:
        return None

    @property
    def done(self) -> bool:
        return self._done

    def start(self, face: FaceContext) -> None:
        refusal = face.refusal(WHAT)
        if refusal:
            self._trouble(face, refusal)
            return
        definition = self._definition
        lines = command_lines(definition.commands)
        if not lines:
            self._trouble(face, NO_COMMANDS)
            return
        folder = face.repo / definition.folder
        if not folder.is_dir():
            self._trouble(face, NO_FOLDER.format(folder=definition.folder))
            return
        commands: list[list[str]] = []
        for line in lines:
            try:
                commands.append(shlex.split(line))
            except ValueError as error:
                self._trouble(face, BAD_COMMAND.format(line=line, reason=error))
                return
        # Started in order; none is waited on.
        for argv in commands:
            failure = face.seams.launch(argv, folder)
            if failure:
                self._trouble(face, f"{WHAT}: {failure}")
                return
        detail = (
            f"Started in {definition.folder}/, not waited on:",
            *(f"  {line}" for line in lines),
            f"Their output goes to {AGENTS_LOG}.",
        )
        self._reply = Reply(face, Response(OPENING, detail))
        self.tick(face)

    def tick(self, face: FaceContext) -> None:
        if self._done or self._reply is None:
            return
        self._reply.tick(face)
        if self._reply.done:
            face.react("done")
            self._done = True

    def _trouble(self, face: FaceContext, reason: str) -> None:
        face.summary.trouble(reason)
        face.react("trouble")
        self._done = True
