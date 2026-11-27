"""The `question` agent: a question answered by a language model (AG-5). No drawing.

The owner says the command, and then the question. Each step runs in `tick`,
which never blocks, and each one is shown on the agent's running view, a
small game screen in the detail panel (`view_lines`), with a track along its
top of where the exchange stands: 3, 2, 1, BEEP, ASK, THINK, ANSWER.

1. **The query.** What was said after the command word, in the transcript
   that matched it, is the question: the command word and what precedes it
   are left out, so "question, how far is the moon" asks
   `how far is the moon`, and the agent goes straight to step 4.
2. **The invitation.** With nothing after the command ("I have a
   question"), autobot says `INVITE` ("... Ask me in"), then counts down out
   loud, one beat a second, with a big digit on the screen: three, two, one,
   and a beep. The listener holds its ears for the countdown and the beep
   (FS-12), so autobot does not hear itself.
3. **The question.** From the end of the beep the face's own microphone
   capture records, with the eyes *listening*, the view showing REC, the
   time left and the voice level. The recording ends when the owner stops
   talking (`END_QUIET_SECONDS` of quiet after speech), when nothing was
   said for `NO_SPEECH_SECONDS`, or after `QUESTION_SECONDS`. It is
   transcribed on this machine (`Seams.hearing`, faster-whisper), because
   the listener transcribes in two-second pieces made for a wake phrase.
   The recording is held in memory only, never written anywhere, and is
   dropped once transcribed (TS-4, TS-5). A transcript that is a global
   action keyword (`cancel`) ends the agent; an empty one says so.
4. **The confirmation.** With `confirm_request` on, the definition's
   question is asked first (AG-13): anything but a yes ends the agent with
   nothing sent.
5. **Asking.** The definition's prompt and the query go to the language
   model on a thread, with the eyes *working* (`Seams.model`: Gemini,
   through the ADK on Vertex AI, as autobot's service account, in the
   interface). A failure is one *trouble* line.
6. **The answer.** The model's text is taken apart into the two parts of
   AG-4 (`response.parse_model_response`): the summary is spoken in the
   local voice and the detail is laid out in the panel.

The text sent is the local transcript and the prompt, and nothing else: no
audio leaves the machine, and the answer is spoken here (`CN-1`, `CN-2`).
Nothing is remembered between questions (AG-11).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Literal

import numpy as np

from autobot.agents.base import Launch
from autobot.agents.confirm import Confirmation
from autobot.agents.hearing import LOCAL_SOURCE, Heard
from autobot.agents.models import KIND_LINE, Answered, first_answer
from autobot.agents.response import Response, parse_model_response
from autobot.agents.speech import Reply, Speaker, held
from autobot.agents.words import action_keyword
from autobot.audio_check import INT16_FULL_SCALE, dbfs
from autobot.routing import normalise
from autobot.tui.audio import Capture, Clip
from autobot.tui.face import FaceContext, FaceError, Voice
from autobot.tui.face_reactions import ReactionSet
from autobot.tui.runner import FunctionTask, Task

type Stage = Literal[
    "inviting",
    "counting",
    "recording",
    "hearing",
    "confirming",
    "asking",
    "answer",
    "done",
]

THINKING = "Thinking..."
ASKED = 'You asked: "{query}"'
# Which model heard the question, under the line that quotes it (IS-15).
HEARD_BY = "heard by {source}"
# What autobot says when the command came without a question; the count follows.
INVITE = (
    "Okay, cool, I can look that up for you. Tell me what your question is, "
    "and I'll see if I can find an answer for you. Ask me in"
)
COUNT_WORDS = ("Three.", "Two.", "One.")
COUNTDOWN = "Ask me in {count}"
ASK_NOW = "Beep! Ask your question now."
RECORDING = "Listening to your question: {left} s left"
HEARING = "Reading back what you said..."
NO_QUESTION = (
    "I didn't hear a question. Wake me, say question, and ask it after the beep."
)
CANCELLED = "Cancelled: nothing was sent."
NO_MODEL = "the question agent has no language model here"
NO_HEARING = "the question agent cannot transcribe a recording here"
NO_ANSWER = "the language model gave no answer"
NOT_CONFIRMED = "Not confirmed: nothing was sent."
NO_AUDIO = "the microphone stopped delivering audio"
MIC_NOT_OPEN = "the microphone is not open"
# What the off-NUC refusal names.
WHAT = "the question agent's recording"
# The detail line for a count or a beep that could not be played.
NOT_PLAYED = "sound: {reason}"
NOT_SPOKEN = "voice: {reason}"

# One beat of the countdown, in seconds; a spoken number longer than it is
# let finish.
BEAT_SECONDS = 1.0
# The beep after "one": a short tone the owner asks after.
BEEP_SECONDS = 0.25
BEEP_HERTZ = 880.0
BEEP_LEVEL = 0.3
BEEP_FADE_SECONDS = 0.01
# The listener holds its ears for the countdown and the beep, and a little more.
HOLD_SECONDS = len(COUNT_WORDS) * BEAT_SECONDS + BEEP_SECONDS + 0.5
# The longest question, in seconds; within the capture's own limit.
QUESTION_SECONDS = 10.0
# A recording still running this long after it should have ended has had no
# audio from the microphone.
RECORD_GRACE_SECONDS = 2.0
# Speech is a block peak at or above -35 dBFS; quieter is the room.
SPEECH_PEAK = round(INT16_FULL_SCALE * 10 ** (-35 / 20))
# The recording ends this long after the last speech...
END_QUIET_SECONDS = 1.5
# ...or this long after the beep, when nothing was said at all.
NO_SPEECH_SECONDS = 5.0

# The track along the top of the view, and which stage stands on which step.
TRACK = ("3", "2", "1", "BEEP", "ASK", "THINK", "ANSWER")
# The width of the view's meters, in cells.
METER_CELLS = 20
# How often the view's REC light blinks and its spinner turns, in seconds.
BLINK_SECONDS = 0.5

# The big digits of the countdown, five rows each.
BIG: dict[str, tuple[str, ...]] = {
    "3": ("#####", "    #", " ####", "    #", "#####"),
    "2": ("#####", "    #", "#####", "#    ", "#####"),
    "1": ("  #  ", " ##  ", "  #  ", "  #  ", " ### "),
    "GO": (
        "#####  #####  #",
        "#      #   #  #",
        "# ###  #   #  #",
        "#   #  #   #   ",
        "#####  #####  #",
    ),
}


def prepare(voice: Voice) -> None:
    """Start rendering the invitation and the count: once per visit to the face."""
    for line in (INVITE, *COUNT_WORDS):
        voice.prepare(line)


def beep(sample_rate: int) -> Clip:
    """The beep that says "ask now": `BEEP_SECONDS` of `BEEP_HERTZ`, faded in and out."""
    count = max(1, round(BEEP_SECONDS * sample_rate))
    at = np.arange(count) / sample_rate
    tone = np.sin(2 * math.pi * BEEP_HERTZ * at) * BEEP_LEVEL
    fade = max(1, min(count // 2, round(BEEP_FADE_SECONDS * sample_rate)))
    ramp = np.linspace(0.0, 1.0, fade)
    tone[:fade] *= ramp
    tone[-fade:] *= ramp[::-1]
    return Clip((tone * (INT16_FULL_SCALE - 1)).astype(np.int16), sample_rate)


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


# --- the running view ----------------------------------------------------------------


def track_line(step: str) -> str:
    """The track along the top of the view, with the step it stands on in brackets."""
    return " > ".join(f"[{name}]" if name == step else name for name in TRACK)


def meter(fraction: float, cells: int = METER_CELLS, full: str = "#") -> str:
    """A bar of `cells`, filled to `fraction`."""
    filled = max(0, min(cells, round(fraction * cells)))
    return "[" + full * filled + "." * (cells - filled) + "]"


def big(text: str) -> list[str]:
    """`text` (a digit of the count, or GO) in the view's big letters, indented."""
    return [f"    {row}" for row in BIG.get(text, (text,))]


def spinner(now: float, cells: int = 8) -> str:
    """A light running back and forth along `cells`, as the model is waited for."""
    span = max(1, cells - 1)
    step = int(now / (BLINK_SECONDS / 2)) % (2 * span)
    at = step if step <= span else 2 * span - step
    return "[" + "".join("=" if cell == at else " " for cell in range(cells)) + "]"


def view_lines(step: str, body: Sequence[str]) -> list[str]:
    """The running view: the track, a blank line, and the step's own lines."""
    return [track_line(step), "", *body]


class Question:
    """Ask the model one question and answer in two parts, or end with one line."""

    def __init__(self, launch: Launch) -> None:
        self._definition = launch.definition
        self._transcript = launch.transcript
        self.query = ""
        # Where the query's transcript came from: the listener's own model,
        # unless a recording was transcribed elsewhere (IS-15).
        self.source = LOCAL_SOURCE
        self.stage: Stage = "asking"
        self._task: FunctionTask | None = None
        self._confirmation: Confirmation | None = None
        self._reply: Reply | None = None
        self._done = False
        # The invitation and the countdown.
        self._speaker: Speaker | None = None
        self._hold: Task | None = None
        # The beat of the count: 0 to 2 the numbers, 3 the beep; -1 before.
        self._beat = -1
        self._beat_at = 0.0
        # The recording of the question, memory only.
        self._capture: Capture | None = None
        self._recording_since = 0.0
        self._spoke_at: float | None = None
        # The model loading, started with the invitation so it is ready.
        self._loading: FunctionTask | None = None

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
        if not face.seams.models:
            self._trouble(face, NO_MODEL)
            return
        if not self.query:
            self._invite(face)
            return
        self._have_query(face)

    def tick(self, face: FaceContext) -> None:
        if self._done:
            return
        if self.stage == "inviting":
            self._inviting(face)
        elif self.stage == "counting":
            self._counting(face)
        elif self.stage == "recording":
            self._recording(face)
        elif self.stage == "hearing":
            self._heard(face)
        elif self.stage == "confirming":
            self._confirming(face)
        elif self.stage == "asking":
            self._asked(face)
        elif self.stage == "answer":
            self._answering(face)

    def cancel(self) -> None:
        """The owner said `cancel`: a recording stops, and an answer on its way is dropped."""
        self._drop_recording()
        self._task = None
        self._done = True

    # --- the invitation, the countdown and the recording ---------------------------

    def _invite(self, face: FaceContext) -> None:
        """Nothing after the command: check what recording needs, then invite the question."""
        refusal = face.refusal(WHAT)
        if refusal:
            self._trouble(face, refusal)
            return
        if face.mic.stream is None:
            self._trouble(face, face.mic.error or MIC_NOT_OPEN)
            return
        hearing = face.seams.hearing
        if hearing is None:
            self._trouble(face, NO_HEARING)
            return
        try:
            face.output_device()
        except FaceError as error:
            self._trouble(face, str(error))
            return
        prepare(face.voice)
        self._loading = face.spawn(hearing.load)
        self.stage = "inviting"
        self._speaker = Speaker(INVITE)
        self._speaker.start(face)
        self._view(face)
        self._inviting(face)

    def _inviting(self, face: FaceContext) -> None:
        speaker = self._speaker
        assert speaker is not None
        speaker.tick(face)
        if not speaker.done:
            return
        self._speaker = None
        if speaker.error:
            face.summary.add(INVITE)
            face.detail.append(NOT_SPOKEN.format(reason=speaker.error))
        # The listener does not hear the count and the beep (FS-12).
        self.stage = "counting"
        self._hold = face.hold(HOLD_SECONDS)
        self._counting(face)

    def _counting(self, face: FaceContext) -> None:
        if self._hold is not None:
            if not held(face, self._hold):
                return
            self._hold = None
            self._next_beat(face)
            return
        if face.playing:
            return
        length = BEEP_SECONDS if self._beat == len(COUNT_WORDS) else BEAT_SECONDS
        if face.clock() - self._beat_at < length:
            return
        self._next_beat(face)

    def _next_beat(self, face: FaceContext) -> None:
        self._beat += 1
        self._beat_at = face.clock()
        if self._beat < len(COUNT_WORDS):
            word = COUNT_WORDS[self._beat]
            count = "... ".join(str(3 - beat) for beat in range(self._beat + 1))
            if self._beat == 0:
                face.summary.add(COUNTDOWN.format(count=count + "..."))
            else:
                face.summary.update(COUNTDOWN.format(count=count + "..."))
            clip = face.voice.clip(word)
            if clip is not None:
                self._play(face, clip)
            face.react("speaking")
            self._view(face)
        elif self._beat == len(COUNT_WORDS):
            face.summary.add(ASK_NOW)
            self._play(face, beep(face.config.audio.sample_rate))
            self._view(face)
        else:
            self._start_recording(face)

    def _play(self, face: FaceContext, clip: Clip) -> None:
        """Play a number or the beep; one that cannot be played is a detail line."""
        try:
            face.play(clip)
        except FaceError as error:
            face.detail.append(NOT_PLAYED.format(reason=error))

    def _start_recording(self, face: FaceContext) -> None:
        self.stage = "recording"
        face.release()
        # Only what is said after the beep is the question.
        face.heard.clear()
        self._capture = face.capture
        self._recording_since = face.clock()
        self._spoke_at = None
        face.capture.start_recording(QUESTION_SECONDS)
        face.react("listening")
        face.summary.add(RECORDING.format(left=math.ceil(QUESTION_SECONDS)))
        self._view(face)

    def _recording(self, face: FaceContext) -> None:
        capture = face.capture
        now = face.clock()
        since = now - self._recording_since
        if capture.recording:
            if since > QUESTION_SECONDS + RECORD_GRACE_SECONDS:
                self._drop_recording()
                self._trouble(face, NO_AUDIO)
                return
            if capture.level().peak >= SPEECH_PEAK:
                self._spoke_at = now
            if self._spoke_at is not None:
                ended = now - self._spoke_at >= END_QUIET_SECONDS
            else:
                ended = since >= NO_SPEECH_SECONDS
            if not ended:
                left = max(1, math.ceil(QUESTION_SECONDS - capture.recorded_seconds))
                face.summary.update(RECORDING.format(left=left))
                self._view(face)
                return
            capture.stop_recording()
        clip = capture.take_clip()
        self._capture = None
        # What the listener heard of the question meanwhile is not a command.
        face.heard.clear()
        if clip is None or len(clip.samples) == 0 or self._spoke_at is None:
            self._no_question(face)
            return
        hearing = face.seams.hearing
        assert hearing is not None
        self.stage = "hearing"
        face.summary.add(HEARING)
        face.react("working")
        # The clip goes to the thread and is dropped with it (TS-4).
        self._task = face.spawn(lambda: hearing.transcribe(clip))
        self._view(face)
        self._heard(face)

    def _heard(self, face: FaceContext) -> None:
        task = self._task
        if task is None:
            return
        result = task.poll()
        if result is None:
            self._view(face)
            return
        self._task = None
        if not result.ok:
            self._trouble(face, result.summary())
            return
        heard = task.value if isinstance(task.value, Heard) else Heard("")
        text = heard.text.strip()
        if not text:
            self._no_question(face)
            return
        if action_keyword(text, face.config) is not None:
            self.stage = "done"
            face.summary.add(CANCELLED)
            face.react("done")
            self._done = True
            return
        self.query = text
        # Where the transcript came from, for the lines that quote it (IS-15).
        self.source = heard.source
        self._have_query(face)

    def _asked_lines(self) -> list[str]:
        """The lines that quote the question, with the model that heard it."""
        return [
            ASKED.format(query=self.query),
            HEARD_BY.format(source=self.source),
        ]

    def _no_question(self, face: FaceContext) -> None:
        self.stage = "answer"
        self._reply = Reply(face, Response(NO_QUESTION))
        self._answering(face)

    def _drop_recording(self) -> None:
        capture = self._capture
        if capture is not None:
            capture.stop_recording()
            capture.take_clip()
        self._capture = None

    # --- the query, the confirmation and the answer --------------------------------

    def _have_query(self, face: FaceContext) -> None:
        for line in self._asked_lines():
            face.detail.append(line)
        if self._definition.confirm_request:
            self.stage = "confirming"
            self._confirmation = face.confirm(self._definition.confirm_request_question)
            self._confirming(face)
            return
        self._ask(face)

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
        definition, seams = self._definition, face.seams
        config, repo = face.config, face.repo
        prompt, query = definition.prompt, self.query

        def ask() -> Answered:
            # The agent's own model, then its fallback, in order (IS-18).
            return first_answer(
                definition, seams, config, repo, lambda model: model.ask(prompt, query)
            )

        self.stage = "asking"
        face.summary.add(THINKING)
        face.react("working")
        self._task = face.spawn(ask)
        self._view(face)
        self._asked(face)

    def _asked(self, face: FaceContext) -> None:
        task = self._task
        if task is None:
            return
        result = task.poll()
        if result is None:
            self._view(face)
            return
        self._task = None
        if not result.ok:
            self._trouble(face, result.summary())
            return
        answered = task.value if isinstance(task.value, Answered) else None
        if answered is not None and answered.choice.kind == KIND_LINE:
            # The chain ended in its spoken line: the agent says it (IS-18).
            self.stage = "answer"
            self._reply = Reply(face, Response(answered.choice.line))
            self._answering(face)
            return
        if answered is None or not answered.text.strip():
            self._trouble(face, (answered.trouble if answered else "") or NO_ANSWER)
            return
        self.stage = "answer"
        self._reply = Reply(face, parse_model_response(answered.text))
        self._answering(face)

    def _answering(self, face: FaceContext) -> None:
        assert self._reply is not None
        self._reply.tick(face)
        if self._reply.done:
            self.stage = "done"
            face.react("done")
            self._done = True

    def _trouble(self, face: FaceContext, reason: str) -> None:
        self._drop_recording()
        self.stage = "done"
        face.summary.trouble(reason)
        face.detail.append(f"x {reason}")
        face.react("trouble")
        face.release()
        self._done = True

    # --- the view ------------------------------------------------------------------

    def _view(self, face: FaceContext) -> None:
        """Lay the running view out in the detail panel for the stage it is at."""
        now = face.clock()
        blink = int(now / BLINK_SECONDS) % 2 == 0
        if self.stage == "inviting":
            step, body = (
                "3",
                [
                    "  QUESTION QUEST",
                    "",
                    "  Get ready to ask your question.",
                    "  I count down 3, 2, 1, then BEEP:",
                    "  ask it right after the beep.",
                ],
            )
        elif self.stage == "counting":
            if self._beat < len(COUNT_WORDS):
                digit = str(3 - max(0, self._beat))
                step, body = digit, [*big(digit), "", "  Get ready..."]
            else:
                step, body = "BEEP", [*big("GO"), "", "  BEEP! Ask now."]
        elif self.stage == "recording":
            capture = face.capture
            peak = capture.level().peak
            fraction = capture.recorded_seconds / QUESTION_SECONDS
            left = max(0.0, QUESTION_SECONDS - capture.recorded_seconds)
            light = "(*)" if blink else "( )"
            # Speech starts a quarter of the way along the voice meter.
            voice = meter(min(1.0, peak / SPEECH_PEAK / 4), full="|")
            step, body = (
                "ASK",
                [
                    f"  {light} REC   ASK YOUR QUESTION NOW",
                    "",
                    f"  time  {meter(1 - fraction)} {left:4.1f} s",
                    f"  voice {voice} {dbfs(peak)}",
                    "",
                    "  heard you: go on..."
                    if self._spoke_at is not None
                    else "  waiting for you to speak...",
                    "  I stop when you stop talking.",
                ],
            )
        elif self.stage == "hearing":
            step, body = (
                "ASK",
                [
                    "  Got it. Reading back what you said",
                    f"  {spinner(now)}",
                ],
            )
        elif self.stage == "asking":
            step, body = (
                "THINK",
                [
                    *self._asked_lines(),
                    "",
                    f"  Searching {spinner(now)}",
                ],
            )
        else:
            return
        face.detail.clear()
        for line in view_lines(step, body):
            face.detail.append(line)
