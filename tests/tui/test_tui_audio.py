"""`autobot.tui.audio`: devices, levels, capture and clips, without hardware."""

import math
import wave
from pathlib import Path

import numpy as np
import pytest
from tui_fakes import DEVICES, USABLE, tone

from autobot.tui.audio import (
    METER_FLOOR_DB,
    SILENCE_WARNING_SECONDS,
    SILENT_PEAK,
    AudioError,
    Capture,
    Clip,
    clip_peak,
    describe_device,
    device_spec,
    follows_pipewire,
    level_db,
    meter_cells,
    meter_zones,
    sanitise_clip_name,
    usable_devices,
    write_wav,
)


def test_only_devices_that_open_at_16_khz_are_offered() -> None:
    choices = usable_devices(DEVICES, "input", lambda index: index in USABLE)
    assert [choice.name for choice in choices] == ["sysdefault", "pipewire", "default"]


def test_devices_without_channels_of_the_kind_are_left_out() -> None:
    choices = usable_devices(DEVICES, "output", lambda index: True)
    assert "AIRHUG 21: USB Audio (hw:1,0)" not in [choice.name for choice in choices]


def test_a_device_is_written_by_name_when_the_name_is_unambiguous() -> None:
    assert device_spec(3, "input", DEVICES) == "pipewire"
    # "default" is also inside "sysdefault", but an exact name wins.
    assert device_spec(4, "input", DEVICES) == "default"


def test_a_device_is_written_by_index_when_its_name_is_shared() -> None:
    devices = [
        *DEVICES,
        {"name": "pipewire", "max_input_channels": 2, "max_output_channels": 2},
    ]
    assert device_spec(5, "input", devices) == 5


def test_describe_device_reports_what_a_spec_resolves_to() -> None:
    assert describe_device("pipewire", "input", DEVICES) == (3, "#3 pipewire")
    index, text = describe_device("no such mic", "input", DEVICES)
    assert index is None and "no input device matches" in text


def test_follows_pipewire() -> None:
    assert follows_pipewire("pipewire", "input", DEVICES)
    assert follows_pipewire(4, "output", DEVICES)
    assert not follows_pipewire("sysdefault", "input", DEVICES)
    assert not follows_pipewire("no such mic", "input", DEVICES)


def test_level_db() -> None:
    assert level_db(32768) == 0
    assert level_db(3276.8) == pytest.approx(-20)
    assert level_db(0) == -math.inf


def test_meter_cells_run_from_the_floor_to_full_scale() -> None:
    assert meter_cells(0, 60) == 0
    assert meter_cells(32768, 60) == 60
    assert meter_cells(40000, 60) == 60
    assert meter_cells(3276.8, 60) == 40  # -20 dB of a 60 dB scale
    assert meter_cells(32768 * 10 ** (METER_FLOOR_DB / 20), 60) == 0
    assert meter_cells(100, 0) == 0


def test_a_quiet_but_audible_level_lights_one_cell() -> None:
    assert meter_cells(40, 10) == 1


def test_meter_zones_are_ordered() -> None:
    warn, hot = meter_zones(60)
    assert (warn, hot) == (42, 54)


def test_capture_reports_the_level_of_the_latest_block() -> None:
    capture = Capture(16000)
    capture.feed(tone(1000))
    level = capture.level()
    assert level.peak == 1000
    assert level.rms == pytest.approx(1000)
    capture.feed(tone(-200))
    assert capture.level().peak == 200


def test_the_held_peak_falls_slowly() -> None:
    capture = Capture(16000)
    capture.feed(tone(10000))
    capture.feed(tone(0))
    held = capture.level().hold
    assert 0 < held < 10000
    capture.reset_level()
    assert capture.level().hold == 0


def test_capture_records_only_between_start_and_stop() -> None:
    capture = Capture(16000)
    capture.feed(tone(1))
    capture.start_recording()
    capture.feed(tone(2))
    capture.feed(tone(3))
    assert capture.recording
    assert capture.recorded_seconds == pytest.approx(0.16)
    capture.stop_recording()
    capture.feed(tone(4))
    clip = capture.take_clip()
    assert clip is not None
    assert clip.samples.dtype == np.int16
    assert clip.seconds == pytest.approx(0.16)
    assert set(clip.samples.tolist()) == {2, 3}
    assert capture.take_clip() is None


def test_a_recording_stops_by_itself_at_the_cap() -> None:
    capture = Capture(16000, max_seconds=0.1)
    capture.start_recording()
    capture.feed(tone(5))
    capture.feed(tone(5))
    assert not capture.recording
    clip = capture.take_clip()
    assert clip is not None and len(clip.samples) == 1600


def test_a_recording_can_name_its_own_limit() -> None:
    capture = Capture(16000, max_seconds=0.1)
    # An ambient prompt records a minute; here, 0.2 s.
    capture.start_recording(0.2)
    assert capture.limit_seconds == 0.2
    for _ in range(4):
        capture.feed(tone(5))
    assert not capture.recording
    clip = capture.take_clip()
    assert clip is not None and len(clip.samples) == 3200
    # The next recording that names none is back at the capture's own.
    capture.start_recording()
    assert capture.limit_seconds == 0.1


def read_wav(path: Path) -> tuple[int, int, int, bytes]:
    with wave.open(str(path), "rb") as wav:
        return (
            wav.getnchannels(),
            wav.getsampwidth(),
            wav.getframerate(),
            wav.readframes(wav.getnframes()),
        )


def test_a_streamed_recording_goes_to_a_file_not_to_memory(tmp_path: Path) -> None:
    target = tmp_path / "voice" / ".recording-1.wav.part"
    capture = Capture(16000)
    capture.start_recording(3600.0, stream_to=target)
    # The directory was made and the file is there from the start.
    assert target.exists()
    capture.feed(tone(100))
    capture.feed(tone(-9000))
    capture.feed(tone(7))
    assert capture.recording and capture.recorded_seconds == pytest.approx(0.24)
    # Every block is on disk as it arrives: a crash now loses nothing written.
    channels, width, rate, frames = read_wav(target)
    assert (channels, width, rate, len(frames)) == (1, 2, 16000, 3 * 1280 * 2)
    assert capture._blocks == []
    capture.stop_recording()
    # It is handed over as a file, once, and never as a clip.
    assert capture.take_clip() is None
    streamed = capture.take_streamed()
    assert streamed is not None and capture.take_streamed() is None
    assert (streamed.path, streamed.sample_rate, streamed.samples) == (
        target,
        16000,
        3840,
    )
    assert (streamed.peak, streamed.error) == (9000, "")
    assert streamed.seconds == pytest.approx(0.24)
    samples = np.frombuffer(read_wav(target)[3], dtype="<i2")
    assert list(samples[[0, 1280, 2560]]) == [100, -9000, 7]


def test_a_streamed_recording_stops_at_its_limit_and_cuts_the_last_block(
    tmp_path: Path,
) -> None:
    target = tmp_path / "long.wav.part"
    capture = Capture(16000)
    capture.start_recording(0.1, stream_to=target)
    capture.feed(tone(5))
    capture.feed(tone(5))
    capture.feed(tone(5))
    assert not capture.recording
    streamed = capture.take_streamed()
    assert streamed is not None and streamed.samples == 1600
    assert len(read_wav(target)[3]) == 1600 * 2


def test_a_stream_file_that_cannot_be_made_is_one_line(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a directory is wanted", encoding="utf-8")
    capture = Capture(16000)
    with pytest.raises(AudioError, match="cannot write") as raised:
        capture.start_recording(1.0, stream_to=blocker / "x.wav.part")
    assert "\n" not in str(raised.value)
    assert not capture.recording


def test_an_in_memory_recording_after_a_streamed_one(tmp_path: Path) -> None:
    capture = Capture(16000)
    capture.start_recording(1.0, stream_to=tmp_path / "a.wav.part")
    capture.feed(tone(5))
    capture.stop_recording()
    capture.start_recording()
    capture.feed(tone(6))
    capture.stop_recording()
    clip = capture.take_clip()
    assert clip is not None and len(clip.samples) == 1280
    # Starting again dropped the streamed result that was never collected.
    assert capture.take_streamed() is None


def test_clip_peak() -> None:
    assert clip_peak(np.zeros(0, dtype=np.int16)) == 0
    assert clip_peak(np.array([3, -32768, 100], dtype=np.int16)) == 32768
    assert clip_peak(tone(-41)) == 41


def test_stopping_without_recording_makes_no_clip() -> None:
    capture = Capture(16000)
    capture.stop_recording()
    assert capture.take_clip() is None


@pytest.mark.parametrize(
    ("typed", "name"),
    [
        ("hey autobot", "hey-autobot.wav"),
        ("yo_autobot.wav", "yo_autobot.wav"),
        ("Near Miss.WAV", "Near-Miss.wav"),
        ("../../etc/passwd", "etc-passwd.wav"),
        ("/abs/path", "abs-path.wav"),
        ("  room   noise!! ", "room-noise.wav"),
        (".hidden", "hidden.wav"),
        ("a" * 200, "a" * 64 + ".wav"),
    ],
)
def test_sanitise_clip_name(typed: str, name: str) -> None:
    assert sanitise_clip_name(typed) == name


@pytest.mark.parametrize("typed", ["", "   ", "../..", ".wav", "!!!"])
def test_a_name_with_nothing_usable_is_refused(typed: str) -> None:
    with pytest.raises(ValueError, match="letter or digit"):
        sanitise_clip_name(typed)


def test_write_wav_is_16_khz_mono_16_bit(tmp_path: Path) -> None:
    clip = Clip(np.array([0, 1000, -1000, 32767, -32768], dtype=np.int16), 16000)
    path = tmp_path / "new" / "clip.wav"
    write_wav(path, clip)
    with wave.open(str(path), "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (
            1,
            2,
            16000,
        )
        frames = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    assert frames.tolist() == clip.samples.tolist()
    assert clip.peak == 32768


def test_write_wav_does_not_overwrite_unless_told(tmp_path: Path) -> None:
    path = tmp_path / "clip.wav"
    write_wav(path, Clip(tone(1, 10), 16000))
    with pytest.raises(FileExistsError):
        write_wav(path, Clip(tone(2, 20), 16000))
    with wave.open(str(path), "rb") as wav:
        assert wav.getnframes() == 10
    write_wav(path, Clip(tone(2, 20), 16000), overwrite=True)
    with wave.open(str(path), "rb") as wav:
        assert wav.getnframes() == 20


def test_capture_counts_unbroken_digital_silence() -> None:
    capture = Capture(16000)
    assert capture.silent_seconds == 0.0 and not capture.delivering_silence
    # 24 blocks of 80 ms: just under two seconds of zeros.
    for _ in range(24):
        capture.feed(np.zeros(1280, dtype=np.int16))
    assert capture.silent_seconds == pytest.approx(1.92)
    assert not capture.delivering_silence
    capture.feed(np.zeros(1280, dtype=np.int16))
    assert capture.delivering_silence
    # A sample of dither is still silence; a real noise floor is not.
    capture.feed(np.full(1280, SILENT_PEAK, dtype=np.int16))
    assert capture.delivering_silence
    capture.feed(np.full(1280, SILENT_PEAK + 1, dtype=np.int16))
    assert capture.silent_seconds == 0.0 and not capture.delivering_silence
    # Closing the stream forgets it: a new stream is judged afresh.
    for _ in range(30):
        capture.feed(np.zeros(1280, dtype=np.int16))
    assert capture.silent_seconds >= SILENCE_WARNING_SECONDS
    capture.reset_level()
    assert capture.silent_seconds == 0.0
