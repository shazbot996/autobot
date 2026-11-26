"""`autobot.listener.service`, `ack` and `control`: what runs without hardware."""

from __future__ import annotations

import os
import signal
import socket
from pathlib import Path

import numpy as np
import pytest

from autobot.config import CONFIG_ENV_VAR, AckConfig, resolve_repo_path
from autobot.listener import ack, control, service
from autobot.listener.audio import WrongHostError, require_nuc
from autobot.listener.detector import Frame, WakeEvent
from autobot.listener.models import ModelMissingError
from autobot.listener.service import Pipeline
from autobot.listener.vad import SpeechGate

from .conftest import FIXED_TIME, NetworkAccessError, make_config, silence

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
