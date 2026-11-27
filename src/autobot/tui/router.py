"""The router: what the face runs on a wake, before any agent. No drawing.

The wake phrase only wakes autobot. The router then listens for a command
and hands it to the agent its rule names (`autobot.routing`):

1. **Listening.** The summary says which commands autobot knows, and the
   eyes play *listening*. The words said after the wake phrase in the
   transcript that woke the listener count, so "autobot, echo" routes at
   once; otherwise each transcript the listener publishes is tried in turn
   (`FaceContext.heard`). A transcript no rule matches is one detail line,
   and the router keeps listening.
2. **Routed.** The first rule that matches is named in the detail panel and
   the summary says which agent has the command. That agent's experience is
   made and started, and from then on the router passes every frame to it,
   with its reactions: the session ends when the agent's does.
3. **Nothing heard.** When no command came within `LISTEN_SECONDS`, the
   summary says so and the session ends.

The router hears only what the listener transcribes, so a command needs a
`text_match` wake phrase: no other kind publishes words. Without one the
router says so at once and ends. It makes no sound of its own: the
listener's acknowledgement ("Yes?") is the reply to the wake. Everything it
reads is the listener's local transcript (`CN-1`).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from autobot.config import AGENT_ECHO, KIND_TEXT_MATCH
from autobot.routing import Match, commands_text, match_route
from autobot.tui.echo import Echo
from autobot.tui.face import Experience, FaceContext
from autobot.tui.face_reactions import ReactionSet

# How long the router waits for a command after the wake, in seconds.
LISTEN_SECONDS = 10.0
LISTENING = "Listening for a command: say {commands}."
WAITING = "waiting up to {seconds:g} s for a command"
NO_MATCH = 'heard "{text}": no rule matched'
ROUTED = "Routing to the {agent} agent."
NOTHING_HEARD = "No command heard. Say the wake phrase to try again."
NO_RULES = "no routing rules are configured: add a [[routes]] entry"
NO_WORDS = "no command can be heard: only a text_match wake phrase transcribes speech"

# The experience each agent runs on the face.
AGENTS: Mapping[str, Callable[[], Experience]] = {AGENT_ECHO: Echo}


class Router:
    """Listen for a command after the wake, then run the agent it routes to."""

    def __init__(
        self, agents: Mapping[str, Callable[[], Experience]] | None = None
    ) -> None:
        self._agents = AGENTS if agents is None else agents
        self._since = 0.0
        self._done = False
        # The rule that matched, and the agent's experience it started.
        self.match: Match | None = None
        self.agent: Experience | None = None

    @property
    def reactions(self) -> ReactionSet | None:
        """The agent's own set once it runs; the built-in set before."""
        return self.agent.reactions if self.agent is not None else None

    @property
    def done(self) -> bool:
        return self.agent.done if self.agent is not None else self._done

    def start(self, face: FaceContext) -> None:
        config = face.config
        if not config.routes:
            self._end(face, NO_RULES, trouble=True)
            return
        if not any(phrase.kind == KIND_TEXT_MATCH for phrase in config.wake_phrases):
            self._end(face, NO_WORDS, trouble=True)
            return
        self._since = face.clock()
        face.summary.add(LISTENING.format(commands=commands_text(config.routes)))
        face.detail.append(WAITING.format(seconds=LISTEN_SECONDS))
        face.react("listening")
        self._listen(face)

    def tick(self, face: FaceContext) -> None:
        if self.agent is not None:
            self.agent.tick(face)
            return
        if self._done:
            return
        self._listen(face)
        waiting = self.agent is None and not self._done
        if waiting and face.clock() - self._since >= LISTEN_SECONDS:
            self._end(face, NOTHING_HEARD)

    def _listen(self, face: FaceContext) -> None:
        """Try each transcript heard since the last frame against the rules."""
        while face.heard and self.agent is None:
            text = face.heard.popleft()
            match = match_route(text, face.config.routes)
            if match is None:
                face.detail.append(NO_MATCH.format(text=text))
                continue
            self._route(face, match)

    def _route(self, face: FaceContext, match: Match) -> None:
        agent = match.route.agent
        make = self._agents.get(agent)
        if make is None:
            self._end(face, f"the {agent} agent is not built", trouble=True)
            return
        self.match = match
        face.detail.append(f'{match.describe()}, heard "{match.transcript}"')
        face.summary.add(ROUTED.format(agent=agent))
        face.heard.clear()
        experience = make()
        self.agent = experience
        face.reactions = experience.reactions
        experience.start(face)

    def _end(self, face: FaceContext, text: str, *, trouble: bool = False) -> None:
        if trouble:
            face.summary.trouble(text)
            face.react("trouble")
        else:
            face.summary.add(text)
            face.react("done")
        self._done = True
