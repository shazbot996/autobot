"""`autobot.listener.service`, `ack` and `control`: what runs without hardware."""

from __future__ import annotations

import argparse
import os
import signal
import socket
import wave
from pathlib import Path

import numpy as np
import pytest

from autobot.config import CONFIG_ENV_VAR, AckConfig, resolve_repo_path
from autobot.listener import ack, control, service
from autobot.listener.audio import WrongHostError, require_nuc
from autobot.listener.detector import Frame, WakeEvent
from autobot.listener.events import ScoreEvent
from autobot.listener.models import ModelMissingError
from autobot.listener.service import (
    Pipeline,
    RecentAudio,
    SnapshotError,
    answer_snapshot_request,
    step,
    write_snapshot,
)
from autobot.listener.vad import SpeechGate

from .conftest import (
    FIXED_TIME,
    FRAME_SAMPLES,
    SAMPLE_RATE,
    NetworkAccessError,
    load_clip,
    make_config,
    silence,
)

CONFIG_TEXT = """
[host]
nuc_hostname = "the-nuc"

[audio]
input_device = "pipewire"
output_device = "pipewire"

[[wake_phrases]]
phrase = "autobot"
kind = "text_match"
target = "ack"
"""


class AlwaysSpeech:
    def probability(self, frame: Frame) -> float:
        return 1.0

    def reset(self) -> None:
        return None


class CountingDetector:
    """Fires on the third frame of each stretch and on its end."""

    def __init__(self) -> None:
        self.fed = 0
        self.resets = 0

    def _event(self) -> WakeEvent:
        return WakeEvent("autobot", "ack", "fake", 1.0, FIXED_TIME)

    def feed(self, frame: Frame) -> WakeEvent | None:
        self.fed += 1
        return self._event() if self.fed == 3 else None

    def end_of_speech(self) -> WakeEvent | None:
        return self._event()

    def reset(self) -> None:
        self.fed = 0
        self.resets += 1


def test_the_network_guard_is_active() -> None:
    with pytest.raises(NetworkAccessError):
        socket.create_connection(("192.0.2.1", 9), timeout=0.1)
    with pytest.raises(NetworkAccessError):
        socket.getaddrinfo("example.invalid", 80)
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["ORT_DISABLE_TELEMETRY"] == "1"


def test_pipeline_feeds_every_detector_and_collects_events() -> None:
    detectors = [CountingDetector(), CountingDetector()]
    pipeline = Pipeline(SpeechGate(AlwaysSpeech()), detectors)
    events = [len(pipeline.process(frame)) for frame in silence(4)]
    assert events == [0, 0, 2, 0]
    assert [detector.fed for detector in detectors] == [4, 4]
    pipeline.reset()
    assert [detector.resets for detector in detectors] == [1, 1]
    assert not pipeline.gate.in_speech


def test_require_nuc_refuses_another_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "gethostname", lambda: "wsl-box")
    with pytest.raises(WrongHostError) as error:
        require_nuc(make_config(nuc_hostname="the-nuc"), "the listener")
    assert str(error.value) == "the listener runs on the NUC only; this host is wsl-box"
    monkeypatch.setattr(socket, "gethostname", lambda: "the-nuc")
    require_nuc(make_config(nuc_hostname="the-nuc"), "the listener")


def test_the_listener_refuses_to_run_off_the_nuc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "autobot.toml"
    config.write_text(CONFIG_TEXT)
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config))
    monkeypatch.setattr(socket, "gethostname", lambda: "wsl-box")
    # main() installs signal handlers; keep the test process's own.
    monkeypatch.setattr(signal, "signal", lambda *args: None)
    assert service.main() == service.EXIT_PERMANENT
    captured = capsys.readouterr()
    assert captured.err == "the listener runs on the NUC only; this host is wsl-box\n"


def test_the_ack_command_refuses_to_run_off_the_nuc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "autobot.toml"
    config.write_text(CONFIG_TEXT)
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config))
    monkeypatch.setattr(socket, "gethostname", lambda: "wsl-box")
    assert ack.main() == 1
    assert "runs on the NUC only" in capsys.readouterr().err


def test_the_acknowledgement_renders_once_to_memory(ack_voice: str) -> None:
    clip = ack.render(AckConfig(text="Yes?", voice=ack_voice))
    assert clip.samples.dtype == np.int16
    assert clip.samples.ndim == 1
    assert 0.2 < clip.seconds < 3.0
    assert int(np.abs(clip.samples).max()) > 1000


def test_a_missing_voice_is_one_clear_line() -> None:
    with pytest.raises(ModelMissingError) as error:
        ack.render(AckConfig(text="Yes?", voice="en_XX-nobody-medium"))
    assert "piper voice 'en_XX-nobody-medium' model file is missing" in str(error.value)
    assert "\n" not in str(error.value)


def test_the_unit_is_rendered_from_the_template() -> None:
    template = resolve_repo_path(control.UNIT_TEMPLATE).read_text()
    unit = control.render_unit(template, Path("/srv/autobot"))
    assert "@REPO_ROOT@" not in unit
    assert "ExecStart=/srv/autobot/.venv/bin/python -m autobot.listener\n" in unit
    assert "StandardOutput=append:/srv/autobot/run/listener.log\n" in unit
    assert "Restart=on-failure\n" in unit
    assert "WantedBy=default.target\n" in unit


def test_the_unit_is_not_rendered_for_a_path_with_a_space() -> None:
    with pytest.raises(control.ControlError, match="space"):
        control.render_unit("x", Path("/srv/my autobot"))


def test_the_unit_path_follows_xdg_config_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert control.unit_path() == (
        tmp_path / "systemd" / "user" / "autobot-listener.service"
    )
    assert not control.unit_installed()


def test_process_stats_reads_this_process() -> None:
    stats = control.process_stats(os.getpid())
    assert stats is not None
    assert stats.pid == os.getpid()
    assert stats.cpu_seconds > 0
    assert stats.uptime_seconds >= 0
    assert stats.rss_bytes > 1_000_000
    assert control.process_stats(2**22 + 1) is None


def test_only_a_listener_process_counts_as_the_listener() -> None:
    # This test process is pytest, not `python -m autobot.listener`.
    assert not control.is_listener(os.getpid())


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(5, "5 s"), (192, "3 min 12 s"), (7500, "2 h 5 min"), (200000, "2 d 7 h 33 min")],
)
def test_durations_read_naturally(seconds: int, text: str) -> None:
    assert control._duration(seconds) == text


# --- stretches, their scores and the snapshot (studio plan step 7) ------------


class ScriptedVad:
    """Speech probabilities from a list, one per frame; quiet after it ends."""

    def __init__(self, scores: list[float]):
        self._scores = list(scores)

    def probability(self, frame: Frame) -> float:
        return self._scores.pop(0) if self._scores else 0.0

    def reset(self) -> None:
        return None


class PeakDetector:
    """Stands in for a model-scored detector: `last_peak`, no transcript."""

    phrase = "autobot"
    kind = "peak"

    def __init__(
        self,
        peak: float,
        fire_on: int | None = None,
        margins: tuple[int, int] | None = None,
    ):
        self._peak = peak
        self._fire_on = fire_on
        self.fed: list[Frame] = []
        self.last_peak: float | None = None
        if margins is not None:
            self.gate_margins = margins

    def feed(self, frame: Frame) -> WakeEvent | None:
        self.fed.append(frame)
        if len(self.fed) == self._fire_on:
            # Once only.
            self._fire_on = None
            return WakeEvent(self.phrase, "other", self.kind, 0.5, FIXED_TIME)
        return None

    def end_of_speech(self) -> WakeEvent | None:
        self.last_peak = self._peak
        self.fed = []
        return None

    def reset(self) -> None:
        self.fed = []


def numbered(count: int, start: int = 0) -> list[Frame]:
    """Frames that can be told apart: frame `n` holds the value `start + n`."""
    return [
        np.full(FRAME_SAMPLES, start + index, dtype=np.int16) for index in range(count)
    ]


def drive(
    pipeline: Pipeline, recent: RecentAudio, frames: list[Frame]
) -> list[WakeEvent | ScoreEvent]:
    """Run frames through `step` with a fake publisher; return what it was given."""
    published: list[WakeEvent | ScoreEvent] = []
    for frame in frames:
        step(pipeline, recent, frame, published.append)
    return published


def one_stretch(speech: int = 2) -> list[float]:
    """VAD scores for five quiet frames, `speech` frames of speech, and the quiet after."""
    return [0.0] * 5 + [1.0] * speech + [0.0] * 8


def seconds_of(path: Path) -> float:
    with wave.open(str(path), "rb") as wav:
        assert wav.getframerate() == SAMPLE_RATE
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        return wav.getnframes() / wav.getframerate()


def test_every_stretch_gets_one_score_event_with_the_next_identifier() -> None:
    scores = one_stretch() * 3
    pipeline = Pipeline(SpeechGate(ScriptedVad(scores)), [PeakDetector(0.25)])
    recent = RecentAudio(clock=lambda: FIXED_TIME)
    published = drive(pipeline, recent, numbered(len(scores)))
    assert published == [
        ScoreEvent("autobot", "peak", 0.25, identifier, FIXED_TIME)
        for identifier in (1, 2, 3)
    ]
    assert recent.identifiers() == [1, 2, 3]


def test_a_held_stretch_is_its_pre_roll_its_speech_and_its_hangover() -> None:
    scores = one_stretch(speech=2)
    pipeline = Pipeline(SpeechGate(ScriptedVad(scores)), [PeakDetector(0.25)])
    recent = RecentAudio()
    drive(pipeline, recent, numbered(len(scores)))
    audio = recent.stretch_audio(1)
    assert audio is not None
    # Frames 2 to 4 are the pre-roll, 5 and 6 the speech, 7 to 12 the hangover.
    assert [int(value) for value in audio[::FRAME_SAMPLES]] == list(range(2, 13))
    assert audio.dtype == np.int16
    # The rolling buffer holds every microphone frame, speech or not.
    buffer = recent.buffer_audio()
    assert buffer is not None
    assert len(buffer) == len(scores) * FRAME_SAMPLES


def test_a_wake_inside_a_stretch_is_published_after_that_stretchs_score() -> None:
    scores = [0.0] * 5 + [1.0] * 6 + [0.0] * 8
    # Fires on the fifth frame it is fed: three of pre-roll, then the second of speech.
    detector = PeakDetector(0.9, fire_on=5)
    pipeline = Pipeline(SpeechGate(ScriptedVad(scores)), [detector])
    recent = RecentAudio(clock=lambda: FIXED_TIME)
    published = drive(pipeline, recent, numbered(len(scores)))
    # Scored once, early, at the score it fired at; not again when it ended.
    assert published == [
        ScoreEvent("autobot", "peak", 0.5, 1, FIXED_TIME),
        WakeEvent("autobot", "other", "peak", 0.5, FIXED_TIME),
    ]
    # The stretch went on after the wake, and what is held went on with it.
    audio = recent.stretch_audio(1)
    assert audio is not None
    assert [int(value) for value in audio[::FRAME_SAMPLES]] == list(range(2, 17))


def test_a_stretch_cut_short_by_the_acknowledgement_is_held_up_to_the_wake() -> None:
    scores = [0.0] * 5 + [1.0] * 6 + one_stretch()
    pipeline = Pipeline(SpeechGate(ScriptedVad(scores)), [PeakDetector(0.9, fire_on=5)])
    recent = RecentAudio(clock=lambda: FIXED_TIME)
    frames = numbered(len(scores))
    published = drive(pipeline, recent, frames[:7])
    assert [type(event) for event in published] == [ScoreEvent, WakeEvent]
    # What the service does after the acknowledgement was spoken.
    pipeline.reset()
    recent.reset()
    later = drive(pipeline, recent, [*frames[11:], *silence(8)])
    assert later == [ScoreEvent("autobot", "peak", 0.9, 2, FIXED_TIME)]
    first = recent.stretch_audio(1)
    assert first is not None
    assert [int(value) for value in first[::FRAME_SAMPLES]] == [2, 3, 4, 5, 6]


def test_the_widest_gate_is_the_one_whose_stretches_are_numbered_and_held() -> None:
    # Speech, seven quiet frames, speech again: the default gate (hangover 6)
    # hears two stretches, the wide one (hangover 10) hears one.
    scores = [0.0] * 14 + [1.0] * 2 + [0.0] * 7 + [1.0] * 2 + [0.0] * 12
    narrow = PeakDetector(0.25)
    wide = PeakDetector(0.75, margins=(12, 10))
    pipeline = Pipeline(SpeechGate(ScriptedVad(scores)), [narrow, wide])
    assert pipeline.stretch_gate.pre_roll_frames == 12
    assert pipeline.stretch_gate.hangover_frames == 10
    assert pipeline.gate.pre_roll_frames == 3
    recent = RecentAudio(clock=lambda: FIXED_TIME)
    published = drive(pipeline, recent, numbered(len(scores)))
    # One stretch, one score event per detector, in the detectors' order.
    assert published == [
        ScoreEvent("autobot", "peak", 0.25, 1, FIXED_TIME),
        ScoreEvent("autobot", "peak", 0.75, 1, FIXED_TIME),
    ]
    assert pipeline.stretches == 2
    audio = recent.stretch_audio(1)
    assert audio is not None
    # Twelve frames of pre-roll (2 to 13), both bursts, and ten of hangover.
    assert [int(value) for value in audio[::FRAME_SAMPLES]] == list(range(2, 35))


def test_detectors_with_different_wishes_get_a_stretch_gate_that_covers_both() -> None:
    long_before = PeakDetector(0.25, margins=(12, 4))
    long_after = PeakDetector(0.75, margins=(2, 10))
    pipeline = Pipeline(SpeechGate(ScriptedVad([])), [long_before, long_after])
    gate = pipeline.stretch_gate
    assert (gate.pre_roll_frames, gate.hangover_frames) == (12, 10)


def test_a_detector_with_no_score_for_the_stretch_reports_none() -> None:
    scores = one_stretch()
    # CountingDetector has neither a transcript nor a peak.
    pipeline = Pipeline(
        SpeechGate(ScriptedVad(scores)), [CountingDetector()], [("autobot", "fake")]
    )
    recent = RecentAudio(clock=lambda: FIXED_TIME)
    published = drive(pipeline, recent, numbered(len(scores)))
    # It fires on the third frame: the score it fired at is all there is.
    assert published[0] == ScoreEvent("autobot", "fake", 1.0, 1, FIXED_TIME)
    assert isinstance(published[1], WakeEvent)

    class Mute(CountingDetector):
        def feed(self, frame: Frame) -> WakeEvent | None:
            return None

        def end_of_speech(self) -> WakeEvent | None:
            return None

    scores = one_stretch()
    pipeline = Pipeline(
        SpeechGate(ScriptedVad(scores)), [Mute()], [("autobot", "fake")]
    )
    published = drive(pipeline, RecentAudio(clock=lambda: FIXED_TIME), numbered(19))
    assert published == [ScoreEvent("autobot", "fake", None, 1, FIXED_TIME)]


def test_the_fixtures_give_one_score_event_per_stretch_with_rising_identifiers(
    text_match_pipeline: Pipeline,
) -> None:
    recent = RecentAudio()
    frames: list[Frame] = []
    for clip in ("unrelated_speech", "autobot", "near_miss_auto", "room_noise"):
        frames += [*load_clip(clip), *silence(12)]
    published = drive(text_match_pipeline, recent, frames)
    scores = [event for event in published if isinstance(event, ScoreEvent)]
    wakes = [event for event in published if isinstance(event, WakeEvent)]
    assert text_match_pipeline.stretches >= 3
    # One per stretch, numbered from 1 with no gaps.
    assert [event.stretch for event in scores] == list(
        range(1, text_match_pipeline.stretches + 1)
    )
    assert all(event.phrase == "autobot" for event in scores)
    assert all(event.kind == "text_match" for event in scores)
    assert all(event.score is not None and 0.0 < event.score <= 1.0 for event in scores)
    # The one wake comes straight after the score event of its own stretch.
    assert len(wakes) == 1
    before = published[published.index(wakes[0]) - 1]
    assert isinstance(before, ScoreEvent)
    assert before.score == wakes[0].score
    # The stretch a mark on that detection would save is still held, whole.
    audio = recent.stretch_audio(before.stretch)
    assert audio is not None
    assert 0.8 < len(audio) / SAMPLE_RATE < len(load_clip("autobot")) * 0.08 + 1.0


def test_an_openwakeword_wake_follows_its_stretchs_score_event(
    openwakeword_pipeline: Pipeline,
) -> None:
    recent = RecentAudio()
    frames = [*load_clip("hey_autobot"), *silence(14), *load_clip("unrelated_speech")]
    published = drive(openwakeword_pipeline, recent, [*frames, *silence(14)])
    kinds = [type(event) for event in published]
    assert kinds[:2] == [ScoreEvent, WakeEvent]
    score, wake = published[0], published[1]
    assert isinstance(score, ScoreEvent) and isinstance(wake, WakeEvent)
    assert (score.stretch, score.kind, score.score) == (1, "openwakeword", wake.score)
    # The speech after it is further stretches, scored below the threshold.
    later = published[2:]
    assert later and all(isinstance(event, ScoreEvent) for event in later)
    assert [event.stretch for event in later if isinstance(event, ScoreEvent)] == list(
        range(2, 2 + len(later))
    )


def filled(stretches: int, stretch_frames: int = 20) -> RecentAudio:
    """A recorder that has heard `stretches` stretches and a full ten seconds."""
    scores = ([0.0] * 12 + [1.0] * (stretch_frames - 9) + [0.0] * 6) * stretches
    scores += [0.0] * 125
    pipeline = Pipeline(SpeechGate(ScriptedVad(scores)), [PeakDetector(0.25)])
    recent = RecentAudio()
    drive(pipeline, recent, numbered(len(scores)))
    return recent


def test_the_snapshot_of_a_stretch_is_a_wav_of_that_stretch(tmp_path: Path) -> None:
    recent = filled(stretches=2)
    path = tmp_path / "snapshot.wav"
    said = write_snapshot(recent, "2\n", path, SAMPLE_RATE)
    # Three frames of pre-roll, eleven of speech, six of hangover: 1.6 s.
    assert seconds_of(path) == pytest.approx(20 * 0.08)
    assert said == "stretch 2 (1.60 s)"
    with wave.open(str(path), "rb") as wav:
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    held = recent.stretch_audio(2)
    assert held is not None
    assert np.array_equal(samples, held)
    assert not list(tmp_path.glob("*.tmp"))
    assert path.stat().st_mode & 0o777 == 0o600


def test_the_snapshot_of_the_buffer_is_the_last_ten_seconds(tmp_path: Path) -> None:
    recent = filled(stretches=1)
    path = tmp_path / "snapshot.wav"
    assert write_snapshot(recent, "buffer", path, SAMPLE_RATE) == (
        "the rolling buffer (10.00 s)"
    )
    assert seconds_of(path) == pytest.approx(10.0)
    assert len(recent.buffer) == 125


def test_a_sixth_stretch_drops_the_first(tmp_path: Path) -> None:
    recent = filled(stretches=6)
    assert recent.identifiers() == [2, 3, 4, 5, 6]
    path = tmp_path / "snapshot.wav"
    with pytest.raises(SnapshotError) as error:
        write_snapshot(recent, "1", path, SAMPLE_RATE)
    assert str(error.value) == (
        "stretch 1 is no longer held: the listener holds stretches 2 to 6"
    )
    assert not path.exists()
    write_snapshot(recent, "6", path, SAMPLE_RATE)
    assert seconds_of(path) == pytest.approx(1.6)
    with pytest.raises(SnapshotError, match="stretch 7 is not held"):
        write_snapshot(recent, "7", path, SAMPLE_RATE)
    with pytest.raises(SnapshotError, match="bad snapshot request"):
        write_snapshot(recent, "everything", path, SAMPLE_RATE)


def test_nothing_heard_yet_is_said_in_one_line(tmp_path: Path) -> None:
    with pytest.raises(SnapshotError) as error:
        write_snapshot(RecentAudio(), "buffer", tmp_path / "snapshot.wav", SAMPLE_RATE)
    assert str(error.value) == "the listener has heard no audio yet"
    with pytest.raises(SnapshotError) as error:
        write_snapshot(RecentAudio(), "1", tmp_path / "snapshot.wav", SAMPLE_RATE)
    assert str(error.value) == "stretch 1 is not held: the listener holds no stretches"


def test_a_long_stretch_keeps_only_its_last_seconds() -> None:
    scores = [1.0] * 40
    pipeline = Pipeline(SpeechGate(ScriptedVad(scores)), [PeakDetector(0.25)])
    recent = RecentAudio(max_stretch_frames=10)
    drive(pipeline, recent, numbered(len(scores) + 6))
    audio = recent.stretch_audio(1)
    assert audio is not None
    assert [int(value) for value in audio[::FRAME_SAMPLES]] == list(range(36, 46))


def test_a_request_is_answered_with_the_wav_or_with_the_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    recent = filled(stretches=6)
    request, wav, error = service.snapshot_paths(tmp_path)
    request.write_text("1\n")
    answer_snapshot_request(recent, SAMPLE_RATE, tmp_path)
    assert error.read_text() == (
        "stretch 1 is no longer held: the listener holds stretches 2 to 6\n"
    )
    assert not wav.exists()
    assert not request.exists()
    assert "snapshot refused: stretch 1 is no longer held" in capsys.readouterr().out

    request.write_text("buffer\n")
    answer_snapshot_request(recent, SAMPLE_RATE, tmp_path)
    assert seconds_of(wav) == pytest.approx(10.0)
    # A good answer clears the reason an earlier one left.
    assert not error.exists()
    assert not request.exists()
    assert "listener: snapshot written: the rolling buffer" in capsys.readouterr().out

    service.clear_snapshot_files(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_nothing_is_written_without_a_request(tmp_path: Path) -> None:
    recent = filled(stretches=3)
    assert recent.identifiers() == [1, 2, 3]
    # A signal with no request file leaves a reason, and never any audio.
    answer_snapshot_request(recent, SAMPLE_RATE, tmp_path)
    assert [path.name for path in tmp_path.iterdir()] == ["snapshot.error"]
    assert (tmp_path / "snapshot.error").read_text() == (
        "no snapshot request was found\n"
    )


def snapshot_args(*arguments: str) -> argparse.Namespace:
    return control.build_parser().parse_args(["snapshot", *arguments])


def test_the_snapshot_command_takes_a_stretch_or_the_buffer() -> None:
    assert snapshot_args("--stretch", "7").stretch == 7
    assert snapshot_args("--buffer").buffer is True
    for bad in (
        ["--stretch", "0"],
        ["--stretch", "x"],
        [],
        ["--stretch", "1", "--buffer"],
    ):
        with pytest.raises(SystemExit):
            snapshot_args(*bad)


def test_snapshot_fails_in_one_line_when_the_listener_is_not_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(control, "resolve_repo_path", lambda path: tmp_path / path)
    monkeypatch.setattr(control, "listener_pid", lambda: None)
    assert control.main(["snapshot", "--buffer"]) == 1
    captured = capsys.readouterr()
    assert captured.err == "the listener is not running\n"
    assert captured.out == ""
    assert list(tmp_path.iterdir()) == []


def fake_listener(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recent: RecentAudio
) -> list[int]:
    """Make `control` see a running listener that answers SIGUSR1 at once."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "listener.sock").touch()
    signals: list[int] = []

    def kill(pid: int, signum: int) -> None:
        assert pid == 4242
        signals.append(signum)
        answer_snapshot_request(recent, SAMPLE_RATE, run_dir)

    monkeypatch.setattr(control, "resolve_repo_path", lambda path: tmp_path / path)
    monkeypatch.setattr(control, "listener_pid", lambda: 4242)
    monkeypatch.setattr(control, "is_listener", lambda pid: True)
    monkeypatch.setattr(os, "kill", kill)
    return signals


def test_snapshot_signals_the_listener_and_prints_the_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    signals = fake_listener(tmp_path, monkeypatch, filled(stretches=2))
    # A snapshot left by an earlier request must not be taken for this one's.
    (tmp_path / "run" / "snapshot.wav").write_bytes(b"old")
    assert control.main(["snapshot", "--stretch", "2"]) == 0
    captured = capsys.readouterr()
    assert captured.out.splitlines()[-1] == "run/snapshot.wav"
    assert captured.err == ""
    assert signals == [signal.SIGUSR1]
    assert seconds_of(tmp_path / "run" / "snapshot.wav") == pytest.approx(1.6)
    assert not (tmp_path / "run" / "snapshot.request").exists()

    assert control.main(["snapshot", "--buffer"]) == 0
    assert seconds_of(tmp_path / "run" / "snapshot.wav") == pytest.approx(10.0)


def test_snapshot_fails_in_one_line_when_the_stretch_is_no_longer_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_listener(tmp_path, monkeypatch, filled(stretches=6))
    assert control.main(["snapshot", "--stretch", "1"]) == 1
    captured = capsys.readouterr()
    assert captured.err == (
        "stretch 1 is no longer held: the listener holds stretches 2 to 6\n"
    )
    assert "run/snapshot.wav" not in captured.out
    assert sorted(path.name for path in (tmp_path / "run").iterdir()) == [
        "listener.sock"
    ]


def test_snapshot_gives_up_when_the_listener_does_not_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_listener(tmp_path, monkeypatch, RecentAudio())
    monkeypatch.setattr(os, "kill", lambda pid, signum: None)
    monkeypatch.setattr(control, "SNAPSHOT_TIMEOUT_SECONDS", 0.2)
    assert control.main(["snapshot", "--buffer"]) == 1
    assert capsys.readouterr().err == (
        "the listener did not write run/snapshot.wav within 0.2 s\n"
    )
    assert not (tmp_path / "run" / "snapshot.request").exists()
