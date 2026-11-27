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
   root. Then the routing cascade decides (IS-17, `autobot.cascade`): the
   keyword rules at once, in the frame; when none matches, the semantic
   match on the NUC and then the inference server's model, on a worker
   thread, while the router waits for their answer and hears nothing else.
   What no tier is admitted for goes to the core agent, autobot himself,
   when the server's chat is ready; otherwise the summary says "I didn't
   catch that." and the router keeps listening.
2. **Command.** The decision is named in the detail panel with the tier
   that made it and its mode, and the session stands on the agent's node. When the
   agent's definition asks for a confirmation on the way in (AG-13), its
   question is asked there: a yes goes on; a no, or ten seconds without an
   answer, returns to the agents' level, listening again.
3. **Running.** The agent is handed a `Launch` (its definition, the
   decision's mode, the transcript and the parameters a tier filled) and
   makes its experience, which gets every frame
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
the listener's local transcript (`CN-1`); only that text, never audio, is
what tier 3 sends to the inference server on the house LAN.
"""

from __future__ import annotations

from collections.abc import Mapping

from autobot.agents import confirm
from autobot.agents.base import Agent, Launch
from autobot.agents.registry import AGENTS
from autobot.agents.tree import LEVEL_AGENTS, LEVEL_ROOT
from autobot.agents.words import action_keyword
from autobot.cascade import (
    Decision,
    Notes,
    ServerRoute,
    ServerRouter,
    decide_fast,
    decide_slow,
)
from autobot.config import AGENT_CORE, KIND_TEXT_MATCH
from autobot.routing import Match, commands_text
from autobot.tui.face import Experience, FaceContext
from autobot.tui.face_reactions import ReactionSet
from autobot.tui.runner import FunctionTask
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
# What the summary says when no tier is admitted and the core agent cannot
# take the request (IS-17).
DID_NOT_CATCH = "I didn't catch that."
# The detail line of a request handed to the core agent.
TO_CORE = "no tier matched -> core agent"
# The detail line of a decision that could not be made.
NOT_DECIDED = "the cascade failed: {reason}"


class Router:
    """Walk the agent tree with what is said after the wake, then run the agent."""

    def __init__(self, agents: Mapping[str, Agent] | None = None) -> None:
        self._agents = AGENTS if agents is None else agents
        self._since = 0.0
        self._done = False
        # Where the session stands on the tree: the game screen follows it.
        self.position = Position(LEVEL_AGENTS, "", PHASE_WAKE)
        # The cascade's decision, and the agent's experience it started.
        self.decision: Decision | None = None
        self.agent: Experience | None = None
        # Tiers 2 and 3 at work on a transcript, off the frame: the task,
        # the transcript and the notes it leaves.
        self._deciding: tuple[FunctionTask, str, Notes] | None = None
        # The confirmation on the way in, while it is asked.
        self._confirmation: confirm.Confirmation | None = None

    @property
    def reactions(self) -> ReactionSet | None:
        """The agent's own set once it runs; the built-in set before."""
        return self.agent.reactions if self.agent is not None else None

    @property
    def done(self) -> bool:
        return self._done

    @property
    def match(self) -> Match | None:
        """The keyword rule that matched, when tier 1 decided; None otherwise."""
        decision = self.decision
        if decision is None or decision.route is None or decision.tier != 1:
            return None
        return Match(decision.route, decision.number, decision.transcript)

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
            waiting = self._listening_for_a_command and self._deciding is None
            if waiting and face.clock() - self._since >= LISTEN_SECONDS:
                self._end(face, NOTHING_HEARD)

    # --- the agents' level --------------------------------------------------------

    def _agents_level(self, face: FaceContext) -> None:
        """Stand on the agents' level and listen for a command, the ten seconds anew."""
        self.decision = None
        self.agent = None
        self._confirmation = None
        self._deciding = None
        self._since = face.clock()
        self.position = Position(LEVEL_AGENTS, "", PHASE_WAKE)
        face.reactions = None
        face.summary.add(LISTENING.format(commands=commands_text(face.config.routes)))
        face.react("listening")

    def _listen(self, face: FaceContext) -> None:
        """Try each transcript heard since the last frame: keywords first, then the cascade."""
        if self._deciding is not None:
            self._decided(face)
        while face.heard and self._listening_for_a_command and self._deciding is None:
            text = face.heard.popleft()
            keyword = action_keyword(text, face.config)
            if keyword is not None:
                # One level up from here is the root: the session ends.
                face.detail.append(KEYWORD.format(text=text, keyword=keyword))
                self._end(face, CANCELLED)
                return
            notes = Notes()
            decision = decide_fast(text, face.config, notes)
            if decision is not None:
                self._route(face, decision)
                continue
            self._decide(face, text, notes)

    @property
    def _listening_for_a_command(self) -> bool:
        return self.agent is None and self._confirmation is None and not self._done

    # --- tiers 2 and 3, off the frame ----------------------------------------------

    def _decide(self, face: FaceContext, text: str, notes: Notes) -> None:
        """Hand `text` to tiers 2 and 3 on a worker thread, when either can be asked."""
        seams = face.seams
        embedder = seams.embedder
        server: ServerRoute | None = None
        if seams.server is not None and seams.watch is not None:
            server = ServerRouter(seams.server, seams.watch)
        if embedder is None and (server is None or not server.ready()):
            self._unmatched(face, text, notes)
            return
        config = face.config
        task = face.spawn(lambda: decide_slow(text, config, embedder, server, notes))
        self._deciding = (task, text, notes)
        # An answer that is already there is taken in this frame.
        self._decided(face)

    def _decided(self, face: FaceContext) -> None:
        assert self._deciding is not None
        task, text, notes = self._deciding
        result = task.poll()
        if result is None:
            return
        self._deciding = None
        if not result.ok:
            face.detail.append(NOT_DECIDED.format(reason=result.summary()))
        decision = task.value if isinstance(task.value, Decision) else None
        if decision is None:
            self._unmatched(face, text, notes)
            return
        for line in notes.lines:
            face.detail.append(line)
        self._route(face, decision)

    def _unmatched(self, face: FaceContext, text: str, notes: Notes) -> None:
        """No tier was admitted: the core agent takes it, or autobot did not catch it."""
        face.detail.append(NO_MATCH.format(text=text))
        for line in notes.lines:
            face.detail.append(line)
        watch = face.seams.watch
        if AGENT_CORE in self._agents and watch is not None and watch.ready("chat"):
            # autobot himself: no tier chose him, so his tier is 0 (IS-17, IS-19).
            self._route(
                face,
                Decision(
                    agent=AGENT_CORE,
                    mode="",
                    parameters={},
                    tier=0,
                    confidence=0.0,
                    transcript=" ".join(text.split()),
                ),
            )
            return
        face.summary.add(DID_NOT_CATCH)

    # --- the command, and the confirmation on the way in --------------------------

    def _route(self, face: FaceContext, decision: Decision) -> None:
        name = decision.agent
        agent = self._agents.get(name)
        if agent is None:
            self._end(face, f"the {name} agent is not built", trouble=True)
            return
        self.decision = decision
        tier = decision.tier
        self.position = Position(LEVEL_AGENTS, name, PHASE_COMMAND, tier)
        said = TO_CORE if name == AGENT_CORE and not tier else decision.describe()
        face.detail.append(f'{said}, heard "{decision.transcript}"')
        face.heard.clear()
        definition = face.config.agent(name)
        if definition.confirm_entry:
            self.position = Position(LEVEL_AGENTS, name, PHASE_CONFIRMING, tier)
            self._confirmation = confirm.Confirmation(
                definition.confirm_entry_question, face.config.words
            )
            self._confirming(face)
            return
        self._start_agent(face)

    def _confirming(self, face: FaceContext) -> None:
        confirmation = self._confirmation
        assert confirmation is not None and self.decision is not None
        confirmation.tick(face)
        if not confirmation.done:
            return
        self._confirmation = None
        if confirmation.outcome == "yes":
            self._start_agent(face)
            return
        name = self.decision.agent
        said_no = confirmation.outcome == "no"
        face.summary.add((NOT_CONFIRMED if said_no else NO_ANSWER).format(agent=name))
        face.heard.clear()
        self._agents_level(face)

    def _start_agent(self, face: FaceContext) -> None:
        assert self.decision is not None
        decision = self.decision
        name = decision.agent
        face.summary.add(ROUTED.format(agent=name))
        face.heard.clear()
        launch = Launch(
            face.config.agent(name),
            decision.mode,
            decision.transcript,
            dict(decision.parameters),
        )
        experience = self._agents[name].make(launch)
        self.agent = experience
        self.position = Position(LEVEL_AGENTS, name, PHASE_RUNNING, decision.tier)
        face.reactions = experience.reactions
        experience.start(face)
        self._finish_if_done(face)

    # --- the agent runs -----------------------------------------------------------

    def _running(self, face: FaceContext) -> None:
        assert self.agent is not None and self.decision is not None
        if not face.confirming:
            # A confirmation the agent asks hears its own answer, and takes
            # `cancel` for a no. Otherwise the listening setting decides.
            if face.config.agent(self.decision.agent).listening:
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
