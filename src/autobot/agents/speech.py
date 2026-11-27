"""How an agent speaks one line on the face. No drawing.

`Speaker` runs the steps every spoken line takes, each in `tick`, which
never blocks:

1. **Prepare.** The line is shown in the summary and queued to be spoken
   (`FaceContext.say`), and its render in autobot's voice is started.
2. **Render.** It waits for the render. A render that fails ends the line
   with its one-line reason in `error`.
3. **Hold.** The listener is asked to hold its ears for the clip's length
   (FS-12), so autobot does not wake itself and `cancel` is heard between
   utterances, not over them. A hold that fails because the listener is not
   running is not trouble: nothing is listening. Any other hold failure is
   one detail line, and the line is spoken anyway.
4. **Play.** The clip is played on the output device with the eyes
   *speaking* and the mouth following it; a playback that cannot start ends
   the line with its one-line reason.
5. **Done.** When playback ends the face shows what is heard again.

`Reply` is an agent's whole answer (AG-4): the response's summary spoken by
a `Speaker` and its detail laid out in the panel (`FaceContext.respond`). A
summary that cannot be spoken is still shown, with the reason as one detail
line, so the answer is never lost with the voice.

The voice is the face's local piper voice: no text leaves the machine to be
spoken (`CN-2`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from autobot.tui.audio import Clip
from autobot.tui.face import FaceContext, FaceError
from autobot.tui.runner import Task

if TYPE_CHECKING:
    from autobot.agents.response import Response

# What `control hold` says when there is no listener to hold.
LISTENER_NOT_RUNNING = "the listener is not running"


def held(face: FaceContext, hold: Task) -> bool:
    """Whether the hold asked for has been answered; a failure is a detail line.

    A listener that is not running is no failure: nothing hears the face.
    """
    result = hold.poll()
    if result is None:
        return False
    if not result.ok and result.summary() != LISTENER_NOT_RUNNING:
        face.detail.append(f"hold: {result.summary()}")
    return True


class Speaker:
    """One line said in autobot's voice, from its render to the end of its playback."""

    def __init__(self, text: str) -> None:
        self.text = text
        # The one-line reason the line could not be spoken; empty otherwise.
        self.error = ""
        self._started = False
        self._done = False
        self._clip: Clip | None = None
        self._hold: Task | None = None

    @property
    def done(self) -> bool:
        """Whether the line has been spoken, or could not be (`error`)."""
        return self._done

    def start(self, face: FaceContext) -> None:
        """Prepare the line and show it; a render already known to fail ends it unsaid."""
        if self._started:
            return
        self._started = True
        face.voice.prepare(self.text)
        error = face.voice.error(self.text)
        if error:
            self._fail(face, error)
            return
        face.say(self.text)
        self.tick(face)

    def tick(self, face: FaceContext) -> None:
        if self._done or not self._started:
            return
        if self._clip is None:
            clip = face.voice.clip(self.text)
            if clip is None:
                error = face.voice.error(self.text)
                if error:
                    self._fail(face, error)
                return
            # It is being spoken: off the queue.
            if face.speech and face.speech[0] == self.text:
                face.speech.popleft()
            self._clip = clip
            self._hold = face.hold(clip.seconds)
        if self._hold is not None:
            if not held(face, self._hold):
                return
            self._hold = None
            try:
                face.play(self._clip)
            except FaceError as error:
                self._fail(face, str(error))
                return
            face.react("speaking")
        if face.playing:
            return
        self._clip = None
        face.release()
        self._done = True

    def _fail(self, face: FaceContext, reason: str) -> None:
        self.error = reason
        self._clip = None
        self._hold = None
        face.release()
        self._done = True


# The detail line for a summary that was shown and could not be spoken.
NOT_SPOKEN = "voice: {reason}"


class Reply:
    """An agent's answer on the face, from the first word to the end of its playback."""

    def __init__(self, face: FaceContext, response: Response) -> None:
        self.response = response
        self._speaker = face.respond(response)
        self._done = False
        self.tick(face)

    @property
    def done(self) -> bool:
        return self._done

    def tick(self, face: FaceContext) -> None:
        if self._done:
            return
        speaker = self._speaker
        speaker.tick(face)
        if not speaker.done:
            return
        if speaker.error:
            # Shown though not said: the render or the playback failed.
            face.summary.add(self.response.summary)
            face.detail.append(NOT_SPOKEN.format(reason=speaker.error))
        self._done = True
