"""The Audio screen: devices, the level meter, and test clips.

A saved clip goes into the voice dataset (`data/voice/`), with the label and
the speaker the owner gives it; nothing here writes a test fixture.

Two selections decide what the listener hears. The config file names a
`sounddevice` device (`[audio] input_device` and `output_device`); only
devices that open at 16 kHz mono are offered. When that device is "pipewire"
(or "default"), it follows PipeWire's default source and sink, so the screen
also sets those, which is the choice that matters on the NUC.

One input stream feeds both the meter and the recorder, on the audio thread;
the screen only reads the latest level each frame, so it never blocks.

When the input has been digital silence for a couple of seconds (a muted
microphone: the AIRHUG has a mute button of its own), the RMS row gives way
to a NO SIGNAL line and the message line says so, until a signal returns.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from autobot.audio_check import AudioCheckError, DeviceKind, dbfs, resolve_device
from autobot.tui.app import MenuView
from autobot.tui.audio import (
    MAX_CLIP_SECONDS,
    SILENCE_WARNING,
    AudioError,
    Capture,
    Clip,
    InputStream,
    describe_device,
    follows_pipewire,
    meter_cells,
    meter_zones,
    sanitise_clip_name,
    usable_devices,
)
from autobot.tui.config_edit import Change
from autobot.tui.menu import MenuItem
from autobot.tui.pipewire import NodeKind, PipeWireError, PipeWireStatus
from autobot.tui.ui import Canvas, Rect, fit
from autobot.wakeword.dataset import (
    LABEL_AMBIENT,
    LABEL_NEGATIVE,
    LABEL_POSITIVE,
    SOURCE_SESSION,
    ClipRecord,
    DatasetError,
    recorded_now,
)

NODE_WORDS: dict[NodeKind, str] = {"source": "microphone", "sink": "speaker"}
DEVICE_KINDS: tuple[DeviceKind, ...] = ("input", "output")
NODE_KINDS: tuple[tuple[NodeKind, DeviceKind], ...] = (
    ("source", "input"),
    ("sink", "output"),
)
# What the manifest records as the prompt of a clip saved from this screen.
AUDIO_SCREEN_PROMPT = "audio screen"
SAVE_LABELS = (LABEL_POSITIVE, LABEL_NEGATIVE, LABEL_AMBIENT)


def draw_meter(
    canvas: Canvas,
    rect: Rect,
    row: int,
    label: str,
    level: float,
    hold: float,
) -> None:
    """One row of level meter: a bar from the floor to full scale, and the reading."""
    theme = canvas.theme
    glyphs = canvas.glyphs
    reading = dbfs(level).rjust(11)
    width = max(0, rect.width - 8 - len(reading) - 2)
    cells = meter_cells(level, width)
    warn, hot = meter_zones(width)
    canvas.put_in(rect, row, 1, label, theme.normal)
    canvas.put_in(rect, row, 7, glyphs.empty * width, theme.dim)
    for start, end, attr in (
        (0, warn, theme.meter_low),
        (warn, hot, theme.meter_mid),
        (hot, width, theme.meter_high),
    ):
        filled = min(cells, end) - start
        if filled > 0:
            canvas.put_in(rect, row, 7 + start, glyphs.full * filled, attr)
    marker = meter_cells(hold, width)
    if marker > cells:
        canvas.put_in(rect, row, 7 + marker - 1, glyphs.hold, theme.value)
    canvas.put_in(rect, row, 7 + width + 1, reading, theme.value)


def draw_silence_warning(canvas: Canvas, rect: Rect, row: int) -> None:
    """One row, in the error colour: the input is digital silence (a muted microphone)."""
    text = f" NO SIGNAL  {SILENCE_WARNING} "
    canvas.put_in(rect, row, 1, fit(text, rect.width - 2), canvas.theme.error)


def input_device_name(spec: str | int, devices: Sequence[Mapping[str, Any]]) -> str:
    """The name of the input device a clip is recorded through, for the manifest."""
    try:
        index = resolve_device(spec, "input", devices)
    except AudioCheckError:
        return str(spec)
    return str(devices[index]["name"])


class AudioScreen(MenuView):
    title = "Audio"
    facts_title = "Devices"
    panel_rows = 3

    def enter(self) -> None:
        self.devices: list[Mapping[str, Any]] = []
        self.capture = Capture(self.app.config.audio.sample_rate)
        self.stream: InputStream | None = None
        self.meter_wanted = True
        self.clip: Clip | None = None
        # The last speaker's name given when saving, offered again next time.
        self.speaker = ""
        self.pipewire: PipeWireStatus | None = None
        self.pipewire_error = ""
        # Whether the message line has said that the input is digital silence.
        self.silence_told = False
        self.load_devices()
        self.load_pipewire()
        self.start_meter()

    def leave(self) -> None:
        self.stop_meter()
        self.app.services.audio.stop_playback()

    def tick(self) -> None:
        clip = self.capture.take_clip()
        if clip is not None:
            self.clip = clip
            self.app.say(
                f"Recorded {clip.seconds:.1f} s, peak {dbfs(clip.peak)}. "
                "Play it or save it.",
                "ok",
            )
        self.watch_for_silence()

    def watch_for_silence(self) -> None:
        """Say so once when the input turns to digital silence, and when it ends."""
        silent = self.stream is not None and self.capture.delivering_silence
        if silent and not self.silence_told:
            self.app.error(f"No signal: {SILENCE_WARNING}.")
        elif self.silence_told and not silent and self.stream is not None:
            self.app.say("The microphone is delivering a signal again.", "ok")
        self.silence_told = silent

    # --- devices ------------------------------------------------------------

    def load_devices(self) -> None:
        try:
            self.devices = self.app.services.audio.query_devices()
        except AudioError as error:
            self.devices = []
            self.app.error(str(error))

    def load_pipewire(self) -> None:
        try:
            self.pipewire = self.app.services.pipewire.status()
            self.pipewire_error = ""
        except PipeWireError as error:
            self.pipewire = None
            self.pipewire_error = str(error)

    def configured(self, kind: DeviceKind) -> str | int:
        audio = self.app.config.audio
        return audio.input_device if kind == "input" else audio.output_device

    def uses_pipewire(self, kind: DeviceKind) -> bool:
        return follows_pipewire(self.configured(kind), kind, self.devices)

    def pick_device(self, kind: DeviceKind) -> None:
        app = self.app
        backend = app.services.audio
        rate = app.config.audio.sample_rate
        self.load_devices()
        choices = usable_devices(
            self.devices, kind, lambda index: backend.supports(index, kind, rate)
        )
        total = sum(1 for device in self.devices if device[f"max_{kind}_channels"] > 0)
        current, _ = describe_device(self.configured(kind), kind, self.devices)
        selected = next(
            (row for row, choice in enumerate(choices) if choice.index == current), 0
        )
        hidden = total - len(choices)
        picked = app.pick(
            f"{kind.capitalize()} device",
            [choice.label for choice in choices],
            selected,
            f"{hidden} hidden: they do not open at {rate} Hz mono" if hidden else "",
        )
        if picked is None:
            return
        saved = app.save(Change("audio", f"{kind}_device", choices[picked].spec))
        if saved and kind == "input":
            # Reopen the stream on the new device, keeping the "Saved" message
            # unless the device will not open.
            self.stop_meter()
            self.start_meter()

    def pick_node(self, kind: NodeKind) -> None:
        app = self.app
        self.load_pipewire()
        if self.pipewire is None:
            app.error(self.pipewire_error)
            return
        nodes = self.pipewire.nodes(kind)
        selected = next((row for row, node in enumerate(nodes) if node.default), 0)
        picked = app.pick(
            f"PipeWire {NODE_WORDS[kind]} (default {kind})",
            [
                f"{'*' if node.default else ' '} {node.id:>3}  {node.name}"
                for node in nodes
            ],
            selected,
            "* is the current default",
        )
        if picked is None:
            return
        node = nodes[picked]
        try:
            app.services.pipewire.set_default(node)
        except PipeWireError as error:
            app.error(str(error))
            return
        self.load_pipewire()
        # Reopen the stream so it follows the new default at once.
        self.stop_meter()
        self.start_meter()
        app.say(f"PipeWire default {kind} is now {node.name}", "ok")

    # --- meter, record, play, save ------------------------------------------

    def start_meter(self) -> bool:
        if self.stream is not None:
            return True
        if not self.meter_wanted:
            return False
        audio = self.app.config.audio
        try:
            index = resolve_device(audio.input_device, "input", self.devices)
            self.stream = self.app.services.audio.open_input(
                index, audio.sample_rate, audio.frame_samples, self.capture.feed
            )
        except (AudioCheckError, AudioError) as error:
            self.app.error(str(error))
            return False
        return True

    def stop_meter(self) -> None:
        self.capture.stop_recording()
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        self.capture.reset_level()

    def toggle_meter(self) -> None:
        self.meter_wanted = not self.meter_wanted
        if self.meter_wanted:
            self.start_meter()
        else:
            self.stop_meter()

    def toggle_recording(self) -> None:
        if self.capture.recording:
            self.capture.stop_recording()
            return
        self.meter_wanted = True
        if not self.start_meter():
            return
        self.app.services.audio.stop_playback()
        self.capture.start_recording()
        self.app.say(
            f"Recording: speak now. Enter stops it (it stops by itself at "
            f"{MAX_CLIP_SECONDS:g} s).",
            "warning",
        )

    def play(self) -> None:
        if self.clip is None:
            return
        try:
            index = resolve_device(
                self.app.config.audio.output_device, "output", self.devices
            )
            self.app.services.audio.play(self.clip, index)
        except (AudioCheckError, AudioError) as error:
            self.app.error(str(error))
            return
        self.app.say(
            f"Playing {self.clip.seconds:.1f} s on {self.devices[index]['name']}"
        )

    def input_device_name(self) -> str:
        """The name of the input device the clip was recorded through."""
        return input_device_name(self.app.config.audio.input_device, self.devices)

    def save(self) -> None:
        """Save the clip into the voice dataset, labelled by the owner."""
        app = self.app
        if self.clip is None:
            return
        dataset = app.services.dataset
        directory = self.shown(dataset.root)
        phrase = app.config.wake_phrases[0].phrase
        picked = app.pick(
            "What is in the clip?",
            (
                f'The phrase "{phrase}" ({LABEL_POSITIVE})',
                f"Other speech ({LABEL_NEGATIVE})",
                f"No speech: the room ({LABEL_AMBIENT})",
            ),
            note=f"Saved in {directory}/, the voice dataset",
        )
        if picked is None:
            return

        def complaint(text: str) -> str | None:
            try:
                sanitise_clip_name(text)
            except ValueError:
                return "the name needs at least one letter or digit"
            return None

        try:
            initial = self.speaker or dataset.last_speaker()
        except DatasetError:
            initial = self.speaker
        typed = app.prompt(
            "Save the clip",
            "Who is speaking? The speaker's name:",
            initial,
            validate=complaint,
        )
        if typed is None:
            return
        self.speaker = typed.strip()
        record = ClipRecord(
            file="",
            phrase=phrase,
            prompt=AUDIO_SCREEN_PROMPT,
            prompt_id="",
            label=SAVE_LABELS[picked],
            source=SOURCE_SESSION,
            speaker=self.speaker,
            device=self.input_device_name(),
            recorded_at=recorded_now(),
        )
        try:
            stored = dataset.add(self.clip, record)
        except DatasetError as error:
            app.error(f"Not saved: {error}")
            return
        app.say(
            f"Saved {directory}/{stored.file} "
            f"({self.clip.seconds:.1f} s, {stored.label})",
            "ok",
        )

    def shown(self, path: Path) -> str:
        """A path relative to the repository when it is inside it."""
        try:
            return str(path.relative_to(self.app.services.repo))
        except ValueError:
            return str(path)

    # --- drawing ------------------------------------------------------------

    def facts(self) -> list[tuple[str, str, str]]:
        facts: list[tuple[str, str, str]] = []
        for device_kind in DEVICE_KINDS:
            spec = self.configured(device_kind)
            index, text = describe_device(spec, device_kind, self.devices)
            facts.append(
                (
                    f"{device_kind.capitalize()} device",
                    f"{spec!r}  ({text})",
                    "value" if index is not None else "warning",
                )
            )
        for node_kind, device_kind in NODE_KINDS:
            label = f"PipeWire {NODE_WORDS[node_kind]}"
            if not self.uses_pipewire(device_kind):
                facts.append((label, f"(not used by this {device_kind} device)", "dim"))
            elif self.pipewire is None:
                facts.append((label, self.pipewire_error, "warning"))
            else:
                node = self.pipewire.default(node_kind)
                facts.append(
                    (
                        label,
                        node.name if node else "(no default)",
                        "value" if node else "warning",
                    )
                )
        return facts

    def items(self) -> list[MenuItem]:
        capture = self.capture
        have_clip = self.clip is not None
        pipewire = self.pipewire is not None
        if capture.recording:
            record = f"Stop recording ({capture.recorded_seconds:.1f} s)"
        else:
            record = "Record a test clip"
        return [
            MenuItem(
                "Input device...",
                lambda: self.pick_device("input"),
                "Writes [audio] input_device; only devices that open at 16 kHz are listed",
            ),
            MenuItem(
                "Output device...",
                lambda: self.pick_device("output"),
                "Writes [audio] output_device; only devices that open at 16 kHz are listed",
            ),
            MenuItem(
                "PipeWire microphone...",
                lambda: self.pick_node("source"),
                "Sets PipeWire's default source: the microphone behind 'pipewire'",
                enabled=pipewire and self.uses_pipewire("input"),
            ),
            MenuItem(
                "PipeWire speaker...",
                lambda: self.pick_node("sink"),
                "Sets PipeWire's default sink: the speaker behind 'pipewire'",
                enabled=pipewire and self.uses_pipewire("output"),
            ),
            MenuItem(
                record,
                self.toggle_recording,
                f"Records from the input device, up to {MAX_CLIP_SECONDS:g} s",
            ),
            MenuItem(
                "Play the clip",
                self.play,
                "Plays the last recording on the output device",
                enabled=have_clip,
            ),
            MenuItem(
                "Save the clip...",
                self.save,
                "Saves the last recording into "
                f"{self.shown(self.app.services.dataset.root)}/, the voice dataset",
                enabled=have_clip,
            ),
            MenuItem(
                f"Level meter: {'on' if self.meter_wanted else 'off'}",
                self.toggle_meter,
                "Turns the input level meter on and off",
            ),
        ]

    def draw_meter(
        self,
        canvas: Canvas,
        rect: Rect,
        row: int,
        label: str,
        level: float,
        hold: float,
    ) -> None:
        draw_meter(canvas, rect, row, label, level, hold)

    def draw_panel(self, canvas: Canvas, rect: Rect) -> None:
        theme = canvas.theme
        capture = self.capture
        if self.stream is None:
            text = "off" if not self.meter_wanted else "no input stream"
            canvas.put_in(rect, 0, 1, f"Level  ({text})", theme.dim)
        else:
            level = capture.level()
            self.draw_meter(canvas, rect, 0, "Peak", level.peak, level.hold)
            if capture.delivering_silence:
                draw_silence_warning(canvas, rect, 1)
            else:
                self.draw_meter(canvas, rect, 1, "RMS", level.rms, 0.0)
        if capture.recording:
            canvas.put_in(
                rect,
                2,
                1,
                f"{canvas.glyphs.bullet} REC {capture.recorded_seconds:4.1f} s",
                theme.error,
            )
        elif self.clip is not None:
            clip = self.clip
            canvas.put_in(
                rect,
                2,
                1,
                fit(
                    f"Clip   {clip.seconds:.1f} s, {clip.sample_rate} Hz mono, "
                    f"peak {dbfs(clip.peak)}",
                    rect.width - 2,
                ),
                theme.normal,
            )
        else:
            canvas.put_in(rect, 2, 1, "Clip   (none recorded yet)", theme.dim)
