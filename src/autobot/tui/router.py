"""The router: the session the face runs on a wake, from the wake to its end. No drawing.

The wake phrase only wakes autobot. The router then walks the agent tree
(AG-2) with what the owner says, and publishes where the session stands as
a `Position` each frame, which the game screen follows (AG-3):

1. **The agents' level.** The summary says which commands autobot knows,
   and the eyes play *listening*. The words said after the wake phrase in
   the transcript that woke the listener count, so "autobot, echo" routes
   at once; otherwise each transcript the listener publishes is tried in
   turn (`FaceContext.heard`). A global action keyword is tried before the
   rules (AG-14): `cancel` here ends the session, since one level up is the
   root. A transcript no rule matches is one detail line, and the router
   keeps listening.
2. **Command.** The first rule that matches is named in the detail panel
   with its mode, and the session stands on the agent's node. When the
   agent's definition asks for a confirmation on the way in (AG-13), its
   question is asked there: a yes goes on; a no, or ten seconds without an
   answer, returns to the agents' level, listening again.
3. **Running.** The agent is handed a `Launch` (its definition, the rule's
   mode, the transcript) and makes its experience, which gets every frame
   from then on, with its reactions. While the agent's `listening` is on,
   each transcript is first tried for a global action keyword: `cancel`
   ends the experience and returns to the agents' level. While it is off
   (AG-15), what is heard is dropped unread, except while the agent asks a
   confirmation of its own (`FaceContext.confirm`), which is always heard.
4. **Done.** The agent's experience ends: the session returns to the root
   and ends.
5. **Nothing heard.** When no command came within `LISTEN_SECONDS`, the
   summary says so and the session ends.

The router hears only what the listener transcribes, so a command needs a
`text_match` wake phrase: no other kind publishes words. Without one the
router says so at once and ends. It makes no sound of its own except a
confirmation's question, in the face's local voice: the listener's
acknowledgement ("Yes?") is the reply to the wake. Everything it reads is
the listener's local transcript (`CN-1`).
"""

from __future__ import annotations

from collections.abc import Mapping

from autobot.agents import confirm
from autobot.agents.base import Agent, Launch
from autobot.agents.registry import AGENTS
from autobot.agents.tree import LEVEL_AGENTS, LEVEL_ROOT
from autobot.agents.words import action_keyword
from autobot.config import KIND_TEXT_MATCH
from autobot.routing import Match, commands_text, match_route
from autobot.tui.face import Experience, FaceContext
from autobot.tui.face_reactions import ReactionSet
from autobot.tui.tree_game import (
    PHASE_COMMAND,
    PHASE_CONFIRMING,
    PHASE_DONE,
    PHASE_RUNNING,
    PHASE_WAKE,
    Position,
)

# How long the router waits for a command after the wake, in seconds; a
# confirmation waits as long for its answer.
LISTEN_SECONDS = 10.0
LISTENING = "Listening for a command: say {commands}."
WAITING = "waiting up to {seconds:g} s for a command"
NO_MATCH = 'heard "{text}": no rule matched'
ROUTED = "Routing to the {agent} agent."
NOTHING_HEARD = "No command heard. Say the wake phrase to try again."
NO_RULES = "no routing rules are configured: add a [[routes]] entry"
NO_WORDS = "no command can be heard: only a text_match wake phrase transcribes speech"
CANCELLED = "Cancelled."
# A confirmation on the way in that was not answered yes.
NOT_CONFIRMED = "Not confirmed: the {agent} agent was not started."
NO_ANSWER = "No answer: the {agent} agent was not started."
KEYWORD = 'heard "{text}": the keyword {keyword}'


class Router:
    """Walk the agent tree with what is said after the wake, then run the agent."""

    def __init__(self, agents: Mapping[str, Agent] | None = None) -> None:
        self._agents = AGENTS if agents is None else agents
        self._since = 0.0
        self._done = False
        # Where the session stands on the tree: the game screen follows it.
        self.position = Position(LEVEL_AGENTS, "", PHASE_WAKE)
        # The rule that matched, and the agent's experience it started.
        self.match: Match | None = None
        self.agent: Experience | None = None
        # The confirmation on the way in, while it is asked.
        self._confirmation: confirm.Confirmation | None = None

    @property
    def reactions(self) -> ReactionSet | None:
        """The agent's own set once it runs; the built-in set before."""
        return self.agent.reactions if self.agent is not None else None

    @property
    def done(self) -> bool:
        return self._done

    def start(self, face: FaceContext) -> None:
        config = face.config
        if not config.routes:
            self._end(face, NO_RULES, trouble=True)
            return
        if not any(phrase.kind == KIND_TEXT_MATCH for phrase in config.wake_phrases):
            self._end(face, NO_WORDS, trouble=True)
            return
        self._agents_level(face)
        face.detail.append(WAITING.format(seconds=LISTEN_SECONDS))
        self._listen(face)

    def tick(self, face: FaceContext) -> None:
        if self._done:
            return
        if self._confirmation is not None:
            self._confirming(face)
        elif self.agent is not None:
            self._running(face)
        else:
            self._listen(face)
            waiting = (
                self.agent is None and self._confirmation is None and not self._done
            )
            if waiting and face.clock() - self._since >= LISTEN_SECONDS:
                self._end(face, NOTHING_HEARD)

    # --- the agents' level --------------------------------------------------------

    def _agents_level(self, face: FaceContext) -> None:
        """Stand on the agents' level and listen for a command, the ten seconds anew."""
        self.match = None
        self.agent = None
        self._confirmation = None
        self._since = face.clock()
        self.position = Position(LEVEL_AGENTS, "", PHASE_WAKE)
        face.reactions = None
        face.summary.add(LISTENING.format(commands=commands_text(face.config.routes)))
        face.react("listening")

    def _listen(self, face: FaceContext) -> None:
        """Try each transcript heard since the last frame: keywords first, then the rules."""
        while face.heard and self._listening_for_a_command:
            text = face.heard.popleft()
            keyword = action_keyword(text, face.config)
            if keyword is not None:
                # One level up from here is the root: the session ends.
                face.detail.append(KEYWORD.format(text=text, keyword=keyword))
                self._end(face, CANCELLED)
                return
            match = match_route(text, face.config.routes)
            if match is None:
                face.detail.append(NO_MATCH.format(text=text))
                continue
            self._route(face, match)

    @property
    def _listening_for_a_command(self) -> bool:
        return self.agent is None and self._confirmation is None and not self._done

    # --- the command, and the confirmation on the way in --------------------------

    def _route(self, face: FaceContext, match: Match) -> None:
        name = match.route.agent
        agent = self._agents.get(name)
        if agent is None:
            self._end(face, f"the {name} agent is not built", trouble=True)
            return
        self.match = match
        self.position = Position(LEVEL_AGENTS, name, PHASE_COMMAND)
        face.detail.append(f'{match.describe()}, heard "{match.transcript}"')
        face.heard.clear()
        definition = face.config.agent(name)
        if definition.confirm_entry:
            self.position = Position(LEVEL_AGENTS, name, PHASE_CONFIRMING)
            self._confirmation = confirm.Confirmation(
                definition.confirm_entry_question, face.config.words
            )
            self._confirming(face)
            return
        self._start_agent(face)

    def _confirming(self, face: FaceContext) -> None:
        confirmation = self._confirmation
        assert confirmation is not None and self.match is not None
        confirmation.tick(face)
        if not confirmation.done:
            return
        self._confirmation = None
        if confirmation.outcome == "yes":
            self._start_agent(face)
            return
        name = self.match.route.agent
        said_no = confirmation.outcome == "no"
        face.summary.add((NOT_CONFIRMED if said_no else NO_ANSWER).format(agent=name))
        face.heard.clear()
        self._agents_level(face)

    def _start_agent(self, face: FaceContext) -> None:
        assert self.match is not None
        match = self.match
        name = match.route.agent
        face.summary.add(ROUTED.format(agent=name))
        face.heard.clear()
        launch = Launch(face.config.agent(name), match.route.mode, match.transcript)
        experience = self._agents[name].make(launch)
        self.agent = experience
        self.position = Position(LEVEL_AGENTS, name, PHASE_RUNNING)
        face.reactions = experience.reactions
        experience.start(face)
        self._finish_if_done(face)

    # --- the agent runs -----------------------------------------------------------

    def _running(self, face: FaceContext) -> None:
        assert self.agent is not None and self.match is not None
        if not face.confirming:
            # A confirmation the agent asks hears its own answer, and takes
            # `cancel` for a no. Otherwise the listening setting decides.
            if face.config.agent(self.match.route.agent).listening:
                if self._cancelled(face):
                    return
            else:
                face.heard.clear()
        self.agent.tick(face)
        self._finish_if_done(face)

    def _cancelled(self, face: FaceContext) -> bool:
        """Whether a global action keyword was heard: the agent ends, one level back."""
        for text in face.heard:
            keyword = action_keyword(text, face.config)
            if keyword is None:
                continue
            face.detail.append(KEYWORD.format(text=text, keyword=keyword))
            experience = self.agent
            cancel = getattr(experience, "cancel", None)
            if callable(cancel):
                cancel()
            # Whatever it was saying or playing stops, and the face hears again.
            face.stop()
            face.release()
            face.speech.clear()
            face.heard.clear()
            face.summary.add(CANCELLED)
            self._agents_level(face)
            return True
        return False

    def _finish_if_done(self, face: FaceContext) -> None:
        if self.agent is not None and self.agent.done:
            self.position = Position(LEVEL_ROOT, "", PHASE_DONE)
            self._done = True

    def _end(self, face: FaceContext, text: str, *, trouble: bool = False) -> None:
        if trouble:
            face.summary.trouble(text)
            face.react("trouble")
        else:
            face.summary.add(text)
            face.react("done")
        self.position = Position(LEVEL_ROOT, "", PHASE_DONE)
        self._done = True
