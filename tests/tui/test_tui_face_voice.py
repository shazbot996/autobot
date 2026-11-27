"""`autobot.tui.face_voice`: autobot's voice, and the fake that stands in for it."""

import sys
from pathlib import Path

import pytest
from tui_fakes import VOICE_FIXTURE, FakeVoice, fixture_clip, make_harness

from autobot.tui.face_voice import VoiceError, synthesise


def test_a_missing_voice_file_is_one_line_naming_it(tmp_path: Path) -> None:
    with pytest.raises(VoiceError) as raised:
        synthesise("Go ahead.", "en_US-joe-medium", tmp_path)
    message = str(raised.value)
    assert "\n" not in message
    assert str(tmp_path / "models" / "piper" / "en_US-joe-medium.onnx") in message
    assert "make fetch-models" in message


def test_a_missing_settings_file_is_named_too(tmp_path: Path) -> None:
    voices = tmp_path / "models" / "piper"
    voices.mkdir(parents=True)
    (voices / "en_US-joe-medium.onnx").write_bytes(b"")
    with pytest.raises(VoiceError, match=r"en_US-joe-medium\.onnx\.json"):
        synthesise("Go ahead.", "en_US-joe-medium", tmp_path)


def test_a_missing_voice_loads_no_speech_library(tmp_path: Path) -> None:
    loaded = set(sys.modules)
    with pytest.raises(VoiceError):
        synthesise("Go ahead.", "en_US-joe-medium", tmp_path)
    assert "piper" not in set(sys.modules) - loaded


def test_the_harness_speaks_with_the_fake_voice(tmp_path: Path) -> None:
    harness = make_harness(tmp_path, [])
    assert harness.app.services.voice is harness.voice
    clip = harness.app.services.voice("Go ahead.", "en_US-joe-medium", tmp_path)
    assert harness.voice.calls == [("Go ahead.", "en_US-joe-medium", tmp_path)]
    assert clip.sample_rate == 16000
    assert clip.seconds == fixture_clip(VOICE_FIXTURE).seconds > 1.0
    assert clip.peak > 0


def test_the_fake_voice_can_fail_as_the_real_one_does(tmp_path: Path) -> None:
    voice = FakeVoice(error=VoiceError("piper voice file is missing"))
    with pytest.raises(VoiceError, match="missing"):
        voice("Go ahead.", "en_US-joe-medium", tmp_path)
