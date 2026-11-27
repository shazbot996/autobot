"""The echo: the face's first agent. No drawing.

When a command after a wake routes to it (`autobot.tui.router`), the echo
runs five steps, each visible on the face (FS-11):

1. **Woken.** The summary says the wake was heard; the *wake* reaction is
   already playing. The NUC, the microphone, the output device and autobot's
   voice are checked.
2. **Speaking.** autobot says `SPEECH_LINE`, shown in the summary as it is
   spoken (`FaceContext.say`). The line is rendered once per visit to the
   face, in the background (`prepare`), and the echo waits for the render.
   The listener is asked to hold its ears for the line's length (FS-12), and
   the line is played with the mouth following it and the eyes *speaking*.
3. **Recording.** Five seconds from the microphone, through the face's own
   capture, with the eyes *listening*, the summary counting down and the
   detail panel showing the running length and level.
4. **Playing back.** The listener holds its ears for the recording's length,
   and the recording is played on `[audio] output_device` with the mouth
   following it.
5. **Done.** The eyes play *done*, the summary says the echo is finished, and
   the face is idle again.

A step that fails ends the echo with its one-line reason in the summary and
the *trouble* reaction. A hold that fails because the listener is not running
is not trouble: nothing is listening to wake on the playback. Any other hold
failure is one detail line, and the echo goes on.

The recording is held in memory only. It is never written to a file or to the
voice dataset, and is dropped when the echo ends (TS-4, TS-5). Each step runs
in `tick`, which never blocks. All audio stays on this machine (`CN-1`,
`CN-2`).
"""

from __future__ import annotations

import math
from typing import Literal

from autobot.audio_check import dbfs
from autobot.tui.audio import Clip
from autobot.tui.face import FaceContext, FaceError, Voice
from autobot.tui.face_reactions import ReactionSet
from autobot.tui.runner import CommandResult, Task

type Stage = Literal["woken", "speaking", "recording", "playing back", "done"]
# Within the speaking and playing back steps: what the echo waits for.
type Wait = Literal["render", "hold", "play"]

# What autobot says before it records (FS-11, the plan's choice of words).
SPEECH_LINE = "Hey, I'm going to record your voice. Go ahead."
HEARD = "I heard you."
RECORDING = "Recording: {left} s left"
RECORDED = "recorded {seconds:.1f} s of {limit:.1f} s, level {level}"
PLAYING_BACK = "Playing it back."
FINISHED = 'Echo finished. Say the wake phrase and "echo" to go again.'
# How long the echo records.
RECORD_SECONDS = 5.0
# A recording still running this long after it should have ended has had no
# audio from the microphone.
RECORD_GRACE_SECONDS = 2.0
NO_AUDIO = "the microphone stopped delivering audio"
NOTHING_RECORDED = "nothing was recorded"
MIC_NOT_OPEN = "the microphone is not open"
# What `control hold` says when there is no listener to hold.
LISTENER_NOT_RUNNING = "the listener is not running"
# What the off-NUC refusal names.
WHAT = "the echo"


def prepare(voice: Voice) -> None:
    """Start rendering the echo's line: once per visit, when the face opens on the NUC."""
    voice.prepare(SPEECH_LINE)


class Echo:
    """The echo's state machine, from the wake that began it to *done*."""

    def __init__(self) -> None:
        self.stage: Stage = "woken"
        self._wait: Wait = "render"
        self._hold: Task | None = None
        # The clip being spoken, then the recording: memory only.
        self._clip: Clip | None = None
        self._recording_since = 0.0
        self._done = False

    @property
    def reactions(self) -> ReactionSet | None:
        """The echo plays the built-in set."""
        return None

    @property
    def done(self) -> bool:
        return self._done

    def start(self, face: FaceContext) -> None:
        face.summary.add(HEARD)

    def tick(self, face: FaceContext) -> None:
        if self.stage == "woken":
            self._woken(face)
        elif self.stage == "speaking":
            self._speaking(face)
        elif self.stage == "recording":
            self._recording(face)
        elif self.stage == "playing back":
            self._playing_back(face)

    # --- the steps ---------------------------------------------------------------

    def _woken(self, face: FaceContext) -> None:
        refusal = face.refusal(WHAT)
        if refusal:
            self._trouble(face, refusal)
            return
        if face.mic.stream is None:
            self._trouble(face, face.mic.error or MIC_NOT_OPEN)
            return
        try:
            face.output_device()
        except FaceError as device_error:
            self._trouble(face, str(device_error))
            return
        prepare(face.voice)
        voice_error = face.voice.error(SPEECH_LINE)
        if voice_error:
            self._trouble(face, voice_error)
            return
        self.stage = "speaking"
        self._wait = "render"
        face.say(SPEECH_LINE)
        self._speaking(face)

    def _speaking(self, face: FaceContext) -> None:
        if self._wait == "render":
            clip = face.voice.clip(SPEECH_LINE)
            if clip is None:
                error = face.voice.error(SPEECH_LINE)
                if error:
                    self._trouble(face, error)
                return
            if face.speech and face.speech[0] == SPEECH_LINE:
                face.speech.popleft()
            self._clip = clip
            self._hold = face.hold(clip.seconds)
            self._wait = "hold"
        if self._wait == "hold":
            if not self._held(face):
                return
            if not self._play(face):
                return
            self._wait = "play"
        if face.playing:
            return
        self._start_recording(face)

    def _start_recording(self, face: FaceContext) -> None:
        self._clip = None
        face.release()
        self.stage = "recording"
        self._recording_since = face.clock()
        face.capture.start_recording(RECORD_SECONDS)
        face.react("listening")
        face.summary.add(RECORDING.format(left=math.ceil(RECORD_SECONDS)))
        face.detail.append(self._recorded_line(face))

    def _recording(self, face: FaceContext) -> None:
        capture = face.capture
        if capture.recording:
            if face.clock() - self._recording_since > (
                RECORD_SECONDS + RECORD_GRACE_SECONDS
            ):
                capture.stop_recording()
                capture.take_clip()
                self._trouble(face, NO_AUDIO)
                return
            left = max(1, math.ceil(RECORD_SECONDS - capture.recorded_seconds))
            face.summary.update(RECORDING.format(left=left))
            face.detail.update(self._recorded_line(face))
            return
        face.detail.update(self._recorded_line(face))
        clip = capture.take_clip()
        if clip is None or len(clip.samples) == 0:
            self._trouble(face, NOTHING_RECORDED)
            return
        self._clip = clip
        self.stage = "playing back"
        face.summary.add(PLAYING_BACK)
        self._hold = face.hold(clip.seconds)
        self._wait = "hold"
        self._playing_back(face)

    def _playing_back(self, face: FaceContext) -> None:
        if self._wait == "hold":
            if not self._held(face):
                return
            if not self._play(face):
                return
            self._wait = "play"
        if face.playing:
            return
        self._finish(face)

    def _finish(self, face: FaceContext) -> None:
        self.stage = "done"
        face.react("done")
        face.summary.add(FINISHED)
        face.release()
        self._clip = None
        self._hold = None
        self._done = True

    # --- the parts the steps share ------------------------------------------------

    def _held(self, face: FaceContext) -> bool:
        """Whether the hold asked for has been answered; a failure is a detail line."""
        assert self._hold is not None
        result = self._hold.poll()
        if result is None:
            return False
        self._hold = None
        if not result.ok and not _not_running(result):
            face.detail.append(f"hold: {result.summary()}")
        return True

    def _play(self, face: FaceContext) -> bool:
        """Play the clip with the eyes *speaking*; False, and trouble, when it cannot."""
        assert self._clip is not None
        try:
            face.play(self._clip)
        except FaceError as error:
            self._trouble(face, str(error))
            return False
        face.react("speaking")
        return True

    def _recorded_line(self, face: FaceContext) -> str:
        capture = face.capture
        return RECORDED.format(
            seconds=capture.recorded_seconds,
            limit=capture.limit_seconds,
            level=dbfs(capture.level().peak),
        )

    def _trouble(self, face: FaceContext, reason: str) -> None:
        """End the echo with its one-line reason and the *trouble* reaction."""
        self.stage = "done"
        face.summary.trouble(reason)
        face.react("trouble")
        face.release()
        self._clip = None
        self._hold = None
        self._done = True


def _not_running(result: CommandResult) -> bool:
    return result.summary() == LISTENER_NOT_RUNNING
