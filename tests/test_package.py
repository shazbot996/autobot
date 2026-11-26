"""The package imports and its entry point runs."""

import pytest

import autobot
from autobot.__main__ import main


def test_package_has_a_version() -> None:
    assert autobot.__version__ == "0.1.0"


def test_main_prints_the_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main() == 0
    assert f"autobot {autobot.__version__}" in capsys.readouterr().out
