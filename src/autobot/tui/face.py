"""The face's frame: its two panels, what an experience drives, and the model. No drawing.

`autobot.tui.screens.face` draws this.

- `SummaryPanel` holds what the experience says about the session, newest
  at the bottom (FS-8). Before the first wake it says what to do: say the
  first wake phrase. It is cleared when a session begins, so a finished
  run can be read until the next wake.
- `DetailPanel` holds the lines the experience puts there (FS-9). The face
  draws them and does not read them. They scroll with the Live monitor's
  keys, and a panel scrolled back stays on the lines it shows.
- `Experience` is one spoken exchange from a wake to an end (FS-10). It is
  driven by `tick`, once a frame, and never blocks.
- `FaceContext` is what an experience drives: the panels, the reactions,
  autobot's voice, the microphone's capture, the listener's hold, playback
  and the clock. `say` shows a line in the summary and queues the same
  text to be spoken, so the spoken and the shown summary are one text.
  `respond` answers in two parts (AG-4): the summary is spoken and shown,
  and the detail is laid out to fit the detail panel as it was last shown.
  `confirm` asks a question aloud and listens for a yes or a no (AG-13).
  `seams` is what an agent reaches the world through: the wall clock, the
  start of a local command, Google Cloud as the service account, the
  weather lookup and the language model. Tests give fakes for each.
- `FaceModel` owns the ticker, the eyes, the meters, the panels and the
  running experience. Every wake goes to the ticker and plays *wake* at
  once. A wake while nothing runs also clears the panels and starts a new
  experience; a wake during one reaches only the ticker and the eyes.
- What the listener hears while an experience runs is put in
  `FaceContext.heard`, so that the experience can listen for a command: the
  words after the wake phrase in the transcript that woke it first, then
  each transcript after it. Nothing is put there while the face holds the
  listener's ears, and it is emptied when a session begins.

The listener is reached only through `python -m autobot.listener.control`
as a subprocess and the events read from `run/listener.sock` (TS-10). All
audio stays on this machine (`CN-1`, `CN-2`).
"""

from __future__ import annotations

import random
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from autobot.audio_check import AudioCheckError, resolve_device
from autobot.config import Config
from autobot.tui.audio import AudioBackend, AudioError, Capture, Clip
from autobot.tui.face_meters import MicMeter, Mouth
from autobot.tui.face_reactions import Eyes, Pose, ReactionSet
from autobot.tui.face_ticker import (
    DEFAULT_MARKER,
    EVENT_TRANSCRIPT,
    Ticker,
    phrase_span,
)
from autobot.tui.monitor import EVENT_SCORE, EVENT_WAKE, Event
from autobot.tui.runner import FunctionTask, Runner, Task, listener_hold_argv
from autobot.tui.server_state import ServerNotice

if TYPE_CHECKING:
    from autobot.agents.confirm import Confirmation
    from autobot.agents.gemini import Model
    from autobot.agents.hearing import Hearing
    from autobot.agents.response import Response
    from autobot.agents.speech import Speaker
    from autobot.agents.weather_api import Lookup
    from autobot.embedding import Embedder
    from autobot.gcp.client import GoogleCloud
    from autobot.server.client import ServerClient
    from autobot.server.watch import ServerWatch
    from autobot.tui.app import Services
    from autobot.tui.tree_game import Position

# What the summary says before the first wake.
PROMPT = 'Say "{phrase}" to wake me.'
# How many lines each panel keeps.
SUMMARY_LINES = 100
DETAIL_LINES = 500
# `control hold` waits up to 5 s for the listener; this leaves it room.
HOLD_TIMEOUT = 15.0
# How many transcripts `FaceContext.heard` keeps for the experience.
HEARD_LINES = 20
# The detail panel's width before the screen has shown it once.
DETAIL_COLS = 60


class FaceError(Exception):
    """Something the face was asked to do cannot be done. The message is one line."""


def prompt_text(phrase: str) -> str:
    """The summary before the first wake: `Say "autobot" to wake me.`"""
    return PROMPT.format(phrase=phrase)


# --- the panels ---------------------------------------------------------------


@dataclass(frozen=True)
class SummaryLine:
    """One line of the summary panel."""

    text: str
    # True for trouble or an error: drawn in the theme's warning role.
    trouble: bool = False


class SummaryPanel:
    """What the experience says about the session, newest at the bottom."""

    def __init__(self, phrase: str) -> None:
        self.lines: deque[SummaryLine] = deque(
            [SummaryLine(prompt_text(phrase))], maxlen=SUMMARY_LINES
        )

    def add(self, text: str) -> None:
        self.lines.append(SummaryLine(text))

    def update(self, text: str) -> None:
        """Replace the newest line, as a countdown does; add one when there is none."""
        if self.lines:
            self.lines.pop()
        self.add(text)

    def trouble(self, text: str) -> None:
        """Add a line about trouble or an error."""
        self.lines.append(SummaryLine(text, trouble=True))

    def clear(self) -> None:
        """A session begins: the last one's messages go."""
        self.lines.clear()

    @property
    def texts(self) -> list[str]:
        return [line.text for line in self.lines]


class DetailPanel:
    """The experience's lines, and how far the owner has scrolled back through them.

    `scroll` counts lines back from the end, 0 following it, as on the Live
    monitor: Up and Down move one line, PgUp and PgDn one `page`, End
    follows again. `page` is the height the panel was last shown at.
    """

    def __init__(self) -> None:
        self.lines: deque[str] = deque(maxlen=DETAIL_LINES)
        self.scroll = 0
        self.page = 10
        # The width the panel was last shown at: a response's detail is laid
        # out to `page` rows of `cols` columns (AG-4).
        self.cols = DETAIL_COLS

    def append(self, line: str) -> None:
        self.lines.append(line)
        # A panel scrolled back stays on the lines it shows.
        if self.scroll:
            self.scroll += 1

    def update(self, line: str) -> None:
        """Replace the last line, as a running count does; add one when there is none."""
        if self.lines:
            self.lines[-1] = line
        else:
            self.append(line)

    def clear(self) -> None:
        self.lines.clear()
        self.scroll = 0

    def up(self) -> None:
        self.scroll += 1

    def down(self) -> None:
        self.scroll = max(0, self.scroll - 1)

    def page_up(self) -> None:
        self.scroll += self.page

    def page_down(self) -> None:
        self.scroll = max(0, self.scroll - self.page)

    def end(self) -> None:
        self.scroll = 0

    def shown(self, rows: int) -> list[str]:
        """The lines a panel `rows` high shows, oldest first; it becomes the page."""
        rows = max(0, rows)
        self.page = max(1, rows)
        lines = list(self.lines)
        self.scroll = max(0, min(self.scroll, len(lines) - rows))
        if rows == 0:
            return []
        return lines[: len(lines) - self.scroll][-rows:]


# --- autobot's voice ------------------------------------------------------------


class Voice:
    """Lines rendered in autobot's voice in the background, each once.

    `prepare` starts a render with `Services.spawn`; `clip` gives it once it
    is ready, and `error` its one-line failure. A rendered line is kept for
    as long as the face is open, so it is spoken again at once.
    """

    def __init__(
        self,
        render: Callable[[str, str, Path], Clip],
        spawn: Callable[[Callable[[], object]], FunctionTask],
        voice: str,
        models_root: Path,
    ) -> None:
        self._render = render
        self._spawn = spawn
        self.voice = voice
        self.models_root = models_root
        self._tasks: dict[str, FunctionTask] = {}

    def prepare(self, text: str) -> None:
        """Start rendering `text`, unless it has been started already."""
        if text not in self._tasks:
            self._tasks[text] = self._spawn(
                lambda: self._render(text, self.voice, self.models_root)
            )

    def clip(self, text: str) -> Clip | None:
        """The rendered `text`; None while it renders, or when it failed or was never asked for."""
        task = self._tasks.get(text)
        if task is None:
            return None
        result = task.poll()
        if result is None or not result.ok or not isinstance(task.value, Clip):
            return None
        return task.value

    def error(self, text: str) -> str:
        """The one line of a render of `text` that failed; empty otherwise."""
        task = self._tasks.get(text)
        result = task.poll() if task is not None else None
        if result is None or result.ok:
            return ""
        return result.summary()


# --- experiences ---------------------------------------------------------------


def _no_launch(argv: Sequence[str], cwd: Path) -> str:
    return "no local command can be started here"


@dataclass
class Seams:
    """What an agent reaches the world through, each one faked in the tests.

    `launch` starts a local command detached and answers its one-line error,
    empty when it started. `connect` is Google Cloud as the service account,
    or `GcpError`. `weather` is the weather lookup, `models` makes the
    language model of each kind, `local` and `gemini`, for a configuration
    (IS-18), and `hearing` transcribes, locally,
    a recording an agent made after its own cue (`CN-1`). A seam left None is one the face was
    not given: the agent that needs it says so in one line.
    """

    wall_clock: Callable[[], datetime] = datetime.now
    launch: Callable[[Sequence[str], Path], str] = _no_launch
    connect: Callable[[Config, Path], GoogleCloud] | None = None
    weather: Lookup | None = None
    models: Mapping[str, Callable[[Config, Path], Model]] = field(default_factory=dict)
    hearing: Hearing | None = None
    # The inference server on the LAN: what the NUC last learned of it, and
    # the client that calls it (IS-23). None where the face was given none.
    watch: ServerWatch | None = None
    server: ServerClient | None = None
    # The NUC's own sentence embeddings, for tier 2 of the routing cascade
    # (IS-17). None where the face was given none: that tier is then skipped.
    embedder: Embedder | None = None
    # The tools a model may call, by name: each takes the call's arguments
    # and answers its result as text. The NUC runs them (IS-21). None ships.
    tools: Mapping[str, Callable[[Mapping[str, Any]], str]] = field(
        default_factory=dict
    )


class Experience(Protocol):
    """One spoken exchange the face runs from a wake to an end (FS-10).

    An experience may also have `cancel()`, called when the owner says a
    global action keyword while it runs (AG-14): it drops what it holds. The
    face stops its playback either way.
    """

    @property
    def reactions(self) -> ReactionSet | None:
        """The experience's own reactions; None plays the built-in set."""

    @property
    def done(self) -> bool:
        """Whether it has ended; the face is then idle again."""

    def start(self, face: FaceContext) -> None:
        """The wake that began it: the *wake* reaction is already playing."""

    def tick(self, face: FaceContext) -> None:
        """One frame. Never blocks."""


class FaceContext:
    """What an experience drives: the face's parts, and the listener's hold."""

    def __init__(
        self,
        config: Config,
        *,
        audio: AudioBackend,
        runner: Runner,
        voice: Voice,
        repo: Path,
        hostname: str,
        clock: Callable[[], float],
        ticker: Ticker,
        eyes: Eyes,
        mic: MicMeter,
        mouth: Mouth,
        spawn: Callable[[Callable[[], object]], FunctionTask] | None = None,
        models_root: Path | None = None,
        seams: Seams | None = None,
    ) -> None:
        # Starts a slow call without blocking the frame, as `Services.spawn`.
        self.spawn = spawn if spawn is not None else FunctionTask
        self.models_root = models_root if models_root is not None else repo
        self.seams = seams if seams is not None else Seams()
        # The confirmation an agent is asking right now, if any (AG-13).
        self._confirmation: Confirmation | None = None
        self.config = config
        self.audio = audio
        self.runner = runner
        self.voice = voice
        self.repo = repo
        self.hostname = hostname
        self.clock = clock
        self.ticker = ticker
        self.eyes = eyes
        self.mic = mic
        self.mouth = mouth
        self.summary = SummaryPanel(config.wake_phrases[0].phrase)
        self.detail = DetailPanel()
        # The texts `say` has queued to be spoken, oldest first.
        self.speech: deque[str] = deque()
        # What the listener heard since the session began, oldest first: the
        # experience takes the lines it reads.
        self.heard: deque[str] = deque(maxlen=HEARD_LINES)
        # The running experience's reactions; None plays the built-in set.
        self.reactions: ReactionSet | None = None

    # --- what the owner sees --------------------------------------------------

    def react(self, name: str) -> None:
        """Play the reaction `name`, from the running experience's set."""
        self.eyes.play(name, self.reactions)

    def say(self, text: str) -> None:
        """Show `text` in the summary and queue the same text to be spoken."""
        self.summary.add(text)
        self.speech.append(text)
        self.voice.prepare(text)

    def respond(self, response: Response) -> Speaker:
        """Answer in two parts (AG-4): say the summary, lay the detail out.

        The detail replaces what the panel holds, fitted to the panel as it
        was last shown, not scrolled into. The caller ticks the speaker it
        is given until it is done.
        """
        from autobot.agents.response import layout
        from autobot.agents.speech import Speaker

        self.detail.clear()
        for line in layout(response, self.detail.page, self.detail.cols):
            self.detail.append(line)
        speaker = Speaker(response.summary)
        speaker.start(self)
        return speaker

    def confirm(self, question: str) -> Confirmation:
        """Ask `question` aloud and listen for a yes or a no (AG-13).

        The agent that asks ticks the confirmation until it is done. While
        it runs the face listens for its answer, even for an agent whose
        `listening` is off (AG-15).
        """
        from autobot.agents.confirm import Confirmation

        self._confirmation = Confirmation(question, self.config.words)
        return self._confirmation

    @property
    def confirming(self) -> bool:
        """Whether a confirmation an agent asked is still waiting for its answer."""
        return self._confirmation is not None and not self._confirmation.done

    # --- the NUC and the devices ----------------------------------------------

    @property
    def on_nuc(self) -> bool:
        nuc = self.config.host.nuc_hostname
        return bool(nuc) and nuc.lower() == self.hostname.lower()

    def refusal(self, what: str) -> str:
        """The line the other audio paths give off the NUC (TS-11); empty on it."""
        if self.on_nuc:
            return ""
        return f"{what} runs on the NUC only; this host is {self.hostname}"

    @property
    def capture(self) -> Capture:
        """The microphone meter's capture: it records as well."""
        return self.mic.capture

    def output_device(self) -> int:
        """The index of `[audio] output_device`, or `FaceError`."""
        try:
            return resolve_device(
                self.config.audio.output_device, "output", self.audio.query_devices()
            )
        except (AudioCheckError, AudioError) as error:
            raise FaceError(str(error)) from None

    # --- the listener's ears ----------------------------------------------------

    def hold(self, seconds: float) -> Task:
        """Ask the listener not to hear the next `seconds`, and mute the ticker (FS-12).

        The command runs as a subprocess and is polled by the caller.
        """
        self.ticker.muted = True
        return self.runner.start(
            listener_hold_argv(seconds), cwd=self.repo, timeout=HOLD_TIMEOUT
        )

    def release(self) -> None:
        """The face has finished making sound: the ticker shows what is heard again."""
        self.ticker.muted = False

    # --- playback ------------------------------------------------------------------

    def play(self, clip: Clip) -> None:
        """Play `clip` on the output device, with the mouth following it; or `FaceError`."""
        index = self.output_device()
        try:
            self.audio.play(clip, index)
        except AudioError as error:
            raise FaceError(str(error)) from None
        self.mouth.play(clip)

    @property
    def playing(self) -> bool:
        return self.mouth.playing

    def stop(self) -> None:
        """Stop whatever is playing; the mouth is flat."""
        self.audio.stop_playback()
        self.mouth.stop()


class FaceModel:
    """The face: its parts, and the experience that runs after a wake.

    `experience` makes a new experience for each session: the echo in the
    interface, a test's fake in the tests. The clock is in seconds,
    `time.monotonic` in the interface and a fake in the tests.
    """

    def __init__(
        self,
        config: Config,
        *,
        experience: Callable[[], Experience],
        audio: AudioBackend,
        runner: Runner,
        render: Callable[[str, str, Path], Clip],
        spawn: Callable[[Callable[[], object]], FunctionTask],
        repo: Path,
        models_root: Path,
        hostname: str,
        clock: Callable[[], float],
        chance: random.Random | None = None,
        marker: str = DEFAULT_MARKER,
        seams: Seams | None = None,
    ) -> None:
        self._make = experience
        self.ticker = Ticker(marker)
        self.eyes = Eyes(clock, chance)
        self.mic = MicMeter(Capture(config.audio.sample_rate))
        self.mouth = Mouth(clock)
        self.context = FaceContext(
            config,
            audio=audio,
            runner=runner,
            voice=Voice(render, spawn, config.ack.voice, models_root),
            repo=repo,
            hostname=hostname,
            clock=clock,
            ticker=self.ticker,
            eyes=self.eyes,
            mic=self.mic,
            mouth=self.mouth,
            spawn=spawn,
            models_root=models_root,
            seams=seams,
        )
        self.experience: Experience | None = None
        self._pose = self.eyes.pose()
        # The stretch of the last score or transcript line, and the last
        # transcript with its stretch: a wake belongs to that stretch.
        self._stretch: int | None = None
        self._transcript: tuple[int, str] | None = None
        # Tells when the inference server comes or goes (IS-23).
        self._server_notice = ServerNotice()

    @classmethod
    def from_services(
        cls,
        config: Config,
        services: Services,
        experience: Callable[[], Experience],
        marker: str = DEFAULT_MARKER,
    ) -> FaceModel:
        """The face on the interface's own services."""
        return cls(
            config,
            experience=experience,
            audio=services.audio,
            runner=services.runner,
            render=services.voice,
            spawn=services.spawn,
            repo=services.repo,
            models_root=services.models_root,
            hostname=services.hostname,
            clock=services.clock,
            marker=marker,
            seams=services.seams(),
        )

    # --- what the experience and the screen read -------------------------------

    @property
    def summary(self) -> SummaryPanel:
        return self.context.summary

    @property
    def detail(self) -> DetailPanel:
        return self.context.detail

    @property
    def voice(self) -> Voice:
        return self.context.voice

    @property
    def running(self) -> bool:
        """Whether an experience is running."""
        return self.experience is not None

    def pose(self) -> Pose:
        """The eyes' pose as of the last `tick`."""
        return self._pose

    @property
    def position(self) -> Position:
        """Where the session stands on the agent tree (AG-3).

        It is the running experience's, when it publishes one (the router
        does); at the root, listening, when the face is idle.
        """
        from autobot.tui.tree_game import Position

        position = getattr(self.experience, "position", None)
        return position if isinstance(position, Position) else Position()

    # --- the microphone ----------------------------------------------------------

    def open(self) -> bool:
        """Open `[audio] input_device` into the meter's capture; False when it fails.

        The caller checks the NUC first (FS-14): off it, nothing is opened.
        """
        audio = self.context.config.audio
        return self.mic.open(
            self.context.audio, audio.input_device, audio.frame_samples
        )

    def close(self) -> None:
        """The face closes: playback and recording stop, and the experience is dropped."""
        self.context.stop()
        self.mic.close()
        # The recording that stopping finished is dropped, never kept.
        self.mic.capture.take_clip()
        self.experience = None
        self.context.reactions = None
        self.context.speech.clear()
        self.context.heard.clear()
        self.context._confirmation = None
        self.ticker.muted = False

    # --- each frame ------------------------------------------------------------------

    def feed(self, events: Iterable[Event], *, connected: bool = True) -> None:
        """Take what `EventSource.poll` returned, and whether the stream is connected."""
        events = list(events)
        if connected:
            self.ticker.down = False
        # Muted while the face holds the listener's ears: nothing is heard.
        muted = self.ticker.muted
        self.ticker.feed(events)
        for event in events:
            kind = event.get("event")
            if kind == EVENT_WAKE:
                self._wake(str(event.get("phrase", "")))
            elif muted:
                continue
            elif kind == EVENT_TRANSCRIPT:
                self._heard(event)
            elif kind == EVENT_SCORE:
                stretch = _stretch_id(event)
                if stretch is not None:
                    self._stretch = stretch
        if not connected:
            self.ticker.down = True
            self._stretch = None
            self._transcript = None

    def _heard(self, event: Event) -> None:
        text = " ".join(str(event.get("text", "")).split())
        stretch = _stretch_id(event)
        if stretch is None or not text:
            return
        if self._transcript == (stretch, text):
            # Another `text_match` phrase transcribed the same speech.
            return
        self._stretch = stretch
        self._transcript = (stretch, text)
        if self.experience is not None:
            self.context.heard.append(text)

    def _waking_words(self, phrase: str) -> str:
        """The words after `phrase` in the transcript that woke the listener."""
        if self._transcript is None or self._transcript[0] != self._stretch:
            return ""
        text = self._transcript[1]
        span = phrase_span(text, phrase)
        # "Hey autobot, echo this." -> "echo this."
        return text[span[1] :].lstrip(" ,;:.!?-").strip() if span else text

    def _wake(self, phrase: str) -> None:
        if self.experience is not None:
            # Shown on the ticker and the eyes; ignored by the experience.
            self.context.react("wake")
            return
        face = self.context
        face.summary.clear()
        face.detail.clear()
        face.speech.clear()
        face.heard.clear()
        face._confirmation = None
        words = self._waking_words(phrase)
        if words:
            face.heard.append(words)
        experience = self._make()
        self.experience = experience
        face.reactions = experience.reactions
        face.react("wake")
        experience.start(face)
        self._finish_if_done()

    def tick(self) -> None:
        """One frame: the ticker moves, the experience runs, the eyes move on."""
        self.ticker.advance(self.context.clock())
        self._notice_server()
        if self.experience is not None:
            self.experience.tick(self.context)
            self._finish_if_done()
        self._pose = self.eyes.pose()

    def next_frame(self) -> float:
        """Seconds until the next frame is wanted: when the ticker's next column is due."""
        return self.ticker.due(self.context.clock())

    def _notice_server(self) -> None:
        """React when the inference server has come or gone (IS-23)."""
        watch = self.context.seams.watch
        if watch is None:
            return
        line = self._server_notice.changed(watch)
        if not line:
            return
        self.ticker.note(line)
        # The eyes are an experience's while one runs: the ticker says it then.
        if self.experience is None:
            self.eyes.play("notice")

    def _finish_if_done(self) -> None:
        if self.experience is not None and self.experience.done:
            self.experience = None
            self.context.reactions = None


def _stretch_id(event: Event) -> int | None:
    value = event.get("stretch")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value
