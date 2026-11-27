"""A confirmation: autobot asks, and a yes or a no answers (AG-13). No drawing.

An agent's definition can ask for two confirmations, one on the way in and
one before a request goes out; both run a `Confirmation`. It never blocks:
each step runs in `tick`.

1. **Asking.** The question is spoken through `Speaker`, which holds the
   listener's ears for the clip's length (FS-12). A question that cannot be
   spoken is still shown in the summary, with its one-line reason in the
   detail panel, and the confirmation listens as usual.
2. **Listening.** The eyes play *listening*, and each transcript heard after
   the question is answered (`answer`): a no word, or a global action
   keyword such as `cancel`, ends it `no`; a yes word ends it `yes`; a word
   that is neither counts for nothing and is one detail line.
3. **Timed out.** When no yes and no no came within the router's
   `LISTEN_SECONDS` of the question being spoken, it ends `timed out`.

What the outcome leads to is the caller's: the router for the confirmation
on the way in, the agent for the one before its request. Everything heard
is the listener's local transcript, and the question is spoken in the
face's local voice (`CN-1`, `CN-2`).
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from autobot.agents.speech import Speaker
from autobot.agents.words import match_word
from autobot.config import WordsConfig
from autobot.tui import router
from autobot.tui.face import FaceContext

type Outcome = Literal["yes", "no", "timed out"]

NEITHER = 'heard "{text}": neither yes nor no'
NOT_SPOKEN = "question: {reason}"


class Answer(Enum):
    """What one transcript says to a confirmation's question."""

    YES = "yes"
    NO = "no"
    NOTHING = "nothing"


def answer(transcript: str, words: WordsConfig) -> Answer:
    """`NO` for a no word or an action keyword, else `YES` for a yes word, else `NOTHING`."""
    if match_word(transcript, (*words.no, *words.actions)) is not None:
        return Answer.NO
    if match_word(transcript, words.yes) is not None:
        return Answer.YES
    return Answer.NOTHING


class Confirmation:
    """One question asked aloud, and the yes, the no or the silence that answers it."""

    def __init__(self, question: str, words: WordsConfig) -> None:
        self.question = question
        self.words = words
        # `yes`, `no` or `timed out` once it has ended; None before.
        self.outcome: Outcome | None = None
        self._speaker: Speaker | None = None
        # When the question was spoken, so the wait began; None while asking.
        self._since: float | None = None

    @property
    def done(self) -> bool:
        return self.outcome is not None

    @property
    def asking(self) -> bool:
        """Whether the question is still being spoken."""
        return not self.done and self._since is None

    def tick(self, face: FaceContext) -> None:
        if self.done:
            return
        if self._since is None:
            self._ask(face)
            if self._since is None:
                return
        self._listen(face)
        if self.done:
            return
        if face.clock() - self._since >= router.LISTEN_SECONDS:
            self.outcome = "timed out"

    def _ask(self, face: FaceContext) -> None:
        """Speak the question; once it has been spoken, start listening."""
        speaker = self._speaker
        if speaker is None:
            speaker = self._speaker = Speaker(self.question)
            speaker.start(face)
        else:
            speaker.tick(face)
        if not speaker.done:
            return
        if speaker.error:
            face.summary.add(self.question)
            face.detail.append(NOT_SPOKEN.format(reason=speaker.error))
        self._speaker = None
        # Only what is said after the question answers it.
        face.heard.clear()
        face.react("listening")
        self._since = face.clock()

    def _listen(self, face: FaceContext) -> None:
        while face.heard and not self.done:
            text = face.heard.popleft()
            said = answer(text, self.words)
            if said is Answer.YES:
                self.outcome = "yes"
            elif said is Answer.NO:
                self.outcome = "no"
            else:
                face.detail.append(NEITHER.format(text=text))
