"""`autobot.audio_check`: everything that runs without opening audio hardware."""

import socket
import sys
from pathlib import Path
from typing import Any

import pytest

from autobot import audio_check
from autobot.audio_check import (
    AudioCheckError,
    check_host,
    dbfs,
    levels,
    resolve_device,
)
from autobot.config import CONFIG_ENV_VAR, Config, parse_config

# The shape of `sounddevice.query_devices()` on the NUC.
DEVICES: list[dict[str, Any]] = [
    {
        "name": "HDA Intel PCH: ALC256 Analog (hw:0,0)",
        "max_input_channels": 2,
        "max_output_channels": 2,
    },
    {
        "name": "HDA Intel PCH: HDMI 0 (hw:0,3)",
        "max_input_channels": 0,
        "max_output_channels": 8,
    },
    {
        "name": "AIRHUG 21: USB Audio (hw:1,0)",
        "max_input_channels": 2,
        "max_output_channels": 0,
    },
    {"name": "sysdefault", "max_input_channels": 128, "max_output_channels": 128},
    {"name": "pipewire", "max_input_channels": 64, "max_output_channels": 64},
    {"name": "default", "max_input_channels": 64, "max_output_channels": 64},
]


def config_for(nuc_hostname: str) -> Config:
    return parse_config(
        {
            "host": {"nuc_hostname": nuc_hostname},
            "audio": {"input_device": "pipewire", "output_device": "pipewire"},
            "wake_phrases": [
                {"phrase": "autobot", "kind": "text_match", "target": "ack"}
            ],
        }
    )


def test_check_host_accepts_the_nuc() -> None:
    check_host(config_for("nuc"), "nuc")
    check_host(config_for("nuc"), "NUC")


def test_check_host_refuses_another_host() -> None:
    with pytest.raises(AudioCheckError) as raised:
        check_host(config_for("nuc"), "wsl-dev")
    assert str(raised.value) == "audio-check runs on the NUC only; this host is wsl-dev"


def test_check_host_refuses_when_the_nuc_is_unset() -> None:
    with pytest.raises(AudioCheckError, match="this host is nuc"):
        check_host(config_for(""), "nuc")


def test_main_refuses_off_the_nuc_before_touching_audio(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "autobot.toml"
    path.write_text(
        '[host]\nnuc_hostname = "nuc"\n'
        '[audio]\ninput_device = "pipewire"\noutput_device = "pipewire"\n'
        '[[wake_phrases]]\nphrase = "autobot"\nkind = "text_match"\ntarget = "ack"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv(CONFIG_ENV_VAR, str(path))
    monkeypatch.setattr(socket, "gethostname", lambda: "wsl-dev")
    # Importing sounddevice would fail the test: the refusal must come first.
    monkeypatch.setitem(sys.modules, "sounddevice", None)

    assert audio_check.main() == 1
    captured = capsys.readouterr()
    assert captured.err == "audio-check runs on the NUC only; this host is wsl-dev\n"
    assert captured.out == ""


def test_main_reports_a_bad_config_in_one_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "absent.toml"))
    assert audio_check.main() == 1
    assert (
        capsys.readouterr().err
        == f"config file not found: {tmp_path / 'absent.toml'}\n"
    )


def test_resolve_device_by_name_substring() -> None:
    assert resolve_device("AIRHUG", "input", DEVICES) == 2
    assert resolve_device("airhug 21", "input", DEVICES) == 2
    assert resolve_device("pipewire", "output", DEVICES) == 4


def test_resolve_device_prefers_an_exact_name() -> None:
    # "default" is also a substring of "sysdefault".
    assert resolve_device("default", "input", DEVICES) == 5


def test_resolve_device_by_index() -> None:
    assert resolve_device(2, "input", DEVICES) == 2
    assert resolve_device(1, "output", DEVICES) == 1


def test_resolve_device_only_considers_the_wanted_kind() -> None:
    with pytest.raises(AudioCheckError, match="no output device matches 'AIRHUG'"):
        resolve_device("AIRHUG", "output", DEVICES)
    with pytest.raises(AudioCheckError, match="has no input channels"):
        resolve_device(1, "input", DEVICES)


def test_resolve_device_errors() -> None:
    with pytest.raises(AudioCheckError, match="no input device matches 'nothing'"):
        resolve_device("nothing", "input", DEVICES)
    with pytest.raises(AudioCheckError, match="is ambiguous"):
        resolve_device("HDA Intel", "output", DEVICES)
    with pytest.raises(AudioCheckError, match="index 99 does not exist"):
        resolve_device(99, "input", DEVICES)


def test_levels() -> None:
    assert levels([]) == (0, 0.0)
    assert levels([0, 0, 0]) == (0, 0.0)
    peak, rms = levels([3, -4, 0, 0])
    assert peak == 4
    assert rms == pytest.approx(2.5)


def test_dbfs() -> None:
    assert dbfs(0) == "-inf dBFS"
    assert dbfs(32768) == "0.0 dBFS"
    assert dbfs(3276.8) == "-20.0 dBFS"
