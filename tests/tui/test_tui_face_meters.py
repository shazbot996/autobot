"""`autobot.tui.face_meters`: the face's microphone meter and its mouth."""

import numpy as np
from tui_fakes import VOICE_FIXTURE, FakeAudio, fixture_clip, tone

from autobot.tui.audio import (
    METER_FLOOR_DB,
    SILENCE_WARNING,
    AudioError,
    BlockHandler,
    Capture,
    Clip,
    InputStream,
    level_db,
    meter_zones,
)
from autobot.tui.face_meters import (
    MOUTH_BLOCK_SECONDS,
    OFF_TEXT,
    SILENT_TEXT,
    MicMeter,
    Mouth,
    bar,
    mouth_level,
)

RATE = 16000
# One 80 ms block of the listener's format.
BLOCK = 1280
FRAME = 0.1


class FakeClock:
    def __init__(self) -> None:
        self.now = 50.0

    def __call__(self) -> float:
        return self.now


def open_meter(audio: FakeAudio) -> MicMeter:
    meter = MicMeter(Capture(RATE))
    assert meter.open(audio, "pipewire", BLOCK)
    return meter


# --- the microphone meter ----------------------------------------------------


def test_a_meter_not_opened_is_off() -> None:
    meter = MicMeter(Capture(RATE))
    assert meter.state == "off"
    assert meter.text == OFF_TEXT
    assert meter.warning == ""
    assert meter.bar(20).cells == 0


def test_the_meter_opens_the_configured_input_into_its_capture() -> None:
    audio = FakeAudio()
    meter = open_meter(audio)
    # "pipewire" is device 3 of the NUC's list.
    assert audio.opened == [3]
    audio.feed(8000)
    assert meter.state == "live"
    assert meter.capture.level().peak == 8000
    assert meter.text == "-12.2 dBFS"
    shown = meter.bar(30)
    assert 0 < shown.cells <= 30
    assert (shown.warn, shown.hot) == meter_zones(30)


def test_the_hold_marker_stays_above_a_falling_level() -> None:
    audio = FakeAudio()
    meter = open_meter(audio)
    audio.feed(16000)
    audio.feed(300)
    shown = meter.bar(30)
    assert shown.hold > shown.cells > 0


def test_two_seconds_of_zeros_put_the_meter_in_silent() -> None:
    audio = FakeAudio()
    meter = open_meter(audio)
    for _ in range(24):
        audio.feed(0)
    states = [meter.state]
    audio.feed(0)
    states.append(meter.state)
    assert states == ["live", "silent"]
    assert meter.text == SILENT_TEXT
    assert meter.warning == SILENCE_WARNING
    assert meter.bar(20).cells == 0
    # A signal brings it back at once.
    audio.feed(2000)
    states.append(meter.state)
    assert states[-1] == "live"
    assert meter.warning == ""


def test_a_device_that_does_not_resolve_is_failed_with_its_error() -> None:
    meter = MicMeter(Capture(RATE))
    assert not meter.open(FakeAudio(), "no such microphone", BLOCK)
    assert meter.state == "failed"
    assert "no such microphone" in meter.text
    assert "\n" not in meter.text


def test_a_device_that_will_not_open_is_failed_with_its_error() -> None:
    class Refusing(FakeAudio):
        def open_input(
            self,
            index: int,
            sample_rate: int,
            block_samples: int,
            on_block: BlockHandler,
        ) -> InputStream:
            raise AudioError("cannot open the input device: busy")

    meter = MicMeter(Capture(RATE))
    assert not meter.open(Refusing(), "pipewire", BLOCK)
    assert meter.state == "failed"
    assert meter.text == "cannot open the input device: busy"


def test_closing_releases_the_device_and_stops_a_recording() -> None:
    audio = FakeAudio()
    meter = open_meter(audio)
    audio.feed(4000)
    meter.capture.start_recording(5.0)
    meter.close()
    assert audio.streams[0].closed
    assert not meter.capture.recording
    assert meter.state == "off"
    assert meter.capture.level().peak == 0


def test_opening_twice_opens_one_stream() -> None:
    audio = FakeAudio()
    meter = open_meter(audio)
    assert meter.open(audio, "pipewire", BLOCK)
    assert len(audio.streams) == 1


# --- the bar -------------------------------------------------------------------


def test_a_bar_of_silence_is_empty() -> None:
    shown = bar(0.0, 0.0, 12)
    assert (shown.cells, shown.hold) == (0, 0)
    assert bar(5000.0, 5000.0, 0).cells == 0


# --- the mouth -----------------------------------------------------------------


def test_mouth_level_is_the_rms_of_the_block_being_played() -> None:
    samples = np.concatenate([tone(0, BLOCK), tone(1000, BLOCK)])
    clip = Clip(samples, RATE)
    assert mouth_level(clip, 0.0) == 0.0
    assert mouth_level(clip, MOUTH_BLOCK_SECONDS + 0.01) == 1000.0
    assert mouth_level(clip, -0.01) == 0.0
    assert mouth_level(clip, clip.seconds) == 0.0
    assert mouth_level(Clip(np.zeros(0, dtype=np.int16), RATE), 0.0) == 0.0


def test_the_mouth_follows_a_fixture_clip_and_is_flat_after() -> None:
    clock = FakeClock()
    mouth = Mouth(clock)
    # Nothing plays: flat.
    assert mouth.level() == 0.0
    assert not mouth.playing
    clip = fixture_clip(VOICE_FIXTURE)
    mouth.play(clip)
    levels: list[float] = []
    cells: list[int] = []
    while clock.now - 50.0 < clip.seconds:
        assert mouth.playing
        levels.append(mouth.level())
        cells.append(mouth.bar(20).cells)
        clock.now += FRAME
    # Above the meter's floor all through the speech, and it moves with it.
    assert all(level_db(level) > METER_FLOOR_DB for level in levels)
    assert max(levels) > 10 * min(levels)
    assert all(count > 0 for count in cells)
    # After its end: flat, and the clip is let go.
    assert mouth.level() == 0.0
    assert not mouth.playing
    assert mouth._clip is None
    clock.now += 5.0
    assert mouth.level() == 0.0
    assert mouth.bar(20).cells == 0


def test_the_mouth_follows_whichever_clip_is_playing() -> None:
    clock = FakeClock()
    mouth = Mouth(clock)
    mouth.play(Clip(tone(4000, RATE), RATE))
    clock.now += 0.5
    assert mouth.level() == 4000.0
    # The recording starts playing: the mouth follows it from its start.
    mouth.play(Clip(np.concatenate([tone(200, BLOCK), tone(9000, RATE)]), RATE))
    assert mouth.level() == 200.0
    clock.now += 0.5
    assert mouth.level() == 9000.0


def test_stopping_the_mouth_makes_it_flat() -> None:
    clock = FakeClock()
    mouth = Mouth(clock)
    mouth.play(Clip(tone(4000, RATE), RATE))
    mouth.stop()
    assert mouth.level() == 0.0
    assert not mouth.playing
