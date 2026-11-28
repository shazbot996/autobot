"""The server's runtime control: `control`, `check`, `token`, `fetch_models` (IS-13, IS-14, IS-25).

Nothing here starts a server, opens a port, downloads a file or touches
systemd: the smoke test is driven through an `App` on fakes, the token is
written in a temporary directory, and the control commands are only asked
what they would refuse.
"""

from __future__ import annotations

import json
import re
import stat
from pathlib import Path

import pytest

from autobot_server import check, control, fetch_models, hostcheck, token
from autobot_server.__main__ import READY_MESSAGE
from autobot_server.auth import read_token
from autobot_server.chat import CHAT_PATH, chat_endpoint
from autobot_server.check import CheckError, Result, Sender, run_checks
from autobot_server.config import ServerSettings, checkout_root
from autobot_server.contract import Segment, Tokens, contract_dir
from autobot_server.job import Job
from autobot_server.route import ROUTE_PATH, route_endpoint
from autobot_server.runtime import Answer
from autobot_server.stt import STT_PATH, Transcribed, Utterance, stt_endpoint
from tests.server_fakes import (
    BEARER,
    FakeClock,
    FakeEngine,
    FakeRuntime,
    health_jobs,
    llm_job,
    make_app,
)

FIXTURE = checkout_root() / "tests" / "fixtures" / "audio" / "hey_autobot.wav"
HEARD = Transcribed(
    text="Hey autobot.",
    language="en",
    segments=(Segment(start_seconds=0.0, end_seconds=1.8, text="Hey autobot."),),
)


class Hears(FakeEngine):
    """A speech engine that hears `HEARD` whatever it is given."""

    def run(self, request: object) -> object:
        super().run(request)
        return HEARD


def example(name: str) -> str:
    return (contract_dir() / name).read_text(encoding="utf-8")


def server(route_answer: str | None = None) -> Sender:
    """A sender into an app on fakes: no socket, the token of the fakes."""
    stt: Job[Utterance, Transcribed] = Job("stt", Hears(), clock=FakeClock())  # type: ignore[arg-type]
    stt.start()
    route = llm_job(
        "route",
        FakeRuntime(Answer(content=route_answer or example("route-response.json"))),
    )
    chat = llm_job(
        "chat",
        FakeRuntime(
            Answer(
                content='{"summary": "Your nine minute timer is running."}',
                tokens=Tokens(prompt=486, completion=24),
            )
        ),
    )
    app = make_app(
        health_jobs(),
        endpoints={
            STT_PATH: stt_endpoint(stt, "large-v3-turbo", clock=FakeClock()),
            ROUTE_PATH: route_endpoint(route),
            CHAT_PATH: chat_endpoint(chat, "qwen3:8b"),
        },
    )

    def send(method: str, path: str, body: bytes | None) -> tuple[int, bytes]:
        response = app.handle(method, path, BEARER, body or b"")
        assert isinstance(response.body, bytes)
        return response.status, response.body

    return send


# --- the smoke test -----------------------------------------------------------


def test_the_smoke_test_asks_every_endpoint_and_passes_on_a_healthy_server() -> None:
    results = run_checks(server(), FIXTURE.read_bytes())
    assert [result.name for result in results] == [
        "GET /health",
        "POST /v1/stt",
        "POST /v1/route",
        "POST /v1/chat",
    ]
    assert all(result.ok for result in results), [r.line() for r in results]
    assert results[1].detail.startswith("'Hey autobot.' by large-v3-turbo")
    assert results[2].detail == (
        "'will I need an umbrella this afternoon' -> weather (0.86) {'day': 'today'}"
    )
    assert results[3].line().startswith("ok   POST /v1/chat: ")


def test_a_failing_endpoint_fails_its_check_and_not_the_others() -> None:
    bad = json.dumps({"agent": "lights", "mode": "", "confidence": 1, "parameters": {}})
    results = run_checks(server(bad), FIXTURE.read_bytes())
    assert [result.ok for result in results] == [True, True, False, True]
    assert results[2].line() == (
        "FAIL POST /v1/route: 503 route is not ready: "
        "the model named an agent outside the list"
    )


def test_a_job_not_ready_or_another_contract_fails_the_health_check() -> None:
    def not_ready(method: str, path: str, body: bytes | None) -> tuple[int, bytes]:
        health = json.loads(example("health.json"))
        health["jobs"]["chat"] = {
            "ready": False,
            "model": "qwen3:8b",
            "reason": "loading",
        }
        return 200, json.dumps(health).encode("utf-8")

    with pytest.raises(CheckError) as failed:
        check.check_health(not_ready)
    assert str(failed.value) == "chat not ready (loading)"

    def other_contract(method: str, path: str, body: bytes | None) -> tuple[int, bytes]:
        health = json.loads(example("health.json"))
        health["contract_version"] = "2"
        return 200, json.dumps(health).encode("utf-8")

    with pytest.raises(CheckError) as failed:
        check.check_health(other_contract)
    assert "the server's contract is 2, this checkout's is 1" in str(failed.value)


def test_an_answer_that_is_not_json_is_a_one_line_failure() -> None:
    def html(method: str, path: str, body: bytes | None) -> tuple[int, bytes]:
        return 502, b"<html>"

    (first, *_) = run_checks(html, b"")
    assert first == Result("GET /health", False, "502, and a body that is not JSON")


def test_the_smoke_test_refuses_off_the_server_host(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert check.main(hostname=lambda: "branch") == 1
    assert capsys.readouterr().err.strip() == (
        "the inference server runs on GAMEON1 only; this host is branch"
    )


# --- control ------------------------------------------------------------------


@pytest.mark.parametrize("command", ["up", "down", "status", "logs", "install"])
def test_every_control_command_refuses_off_the_server_host(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert control.main([command], hostname=lambda: "branch") == 1
    assert capsys.readouterr().err.strip() == (
        "the inference server runs on GAMEON1 only; this host is branch"
    )


def test_the_unit_is_rendered_from_the_template_for_this_checkout() -> None:
    template = (checkout_root() / control.UNIT_TEMPLATE).read_text(encoding="utf-8")
    text = control.render_unit(template, Path("/home/schiele/autobot"))
    assert "@REPO_ROOT@" not in text
    assert "@CUDA_LIBRARY_PATH@" not in text
    assert (
        "ExecStart=/home/schiele/autobot/.venv-server/bin/python -m autobot_server"
        in text
    )
    assert "StandardOutput=append:/home/schiele/autobot/run/server.log" in text
    assert "Restart=on-failure" in text
    assert "RestartPreventExitStatus=2" in text
    assert "WantedBy=default.target" in text
    with pytest.raises(control.ControlError):
        control.render_unit(template, Path("/home/some one/autobot"))


def test_the_nvidia_wheels_libraries_are_on_the_path_in_both_modes(
    tmp_path: Path,
) -> None:
    venv = tmp_path / ".venv-server"
    site = venv / "lib" / "python3.12" / "site-packages" / "nvidia"
    for wheel in ("cudnn", "cublas"):
        (site / wheel / "lib").mkdir(parents=True)
    (site / "cuda_nvrtc").mkdir()
    dirs = control.cuda_library_dirs(venv)
    assert dirs == [site / "cublas" / "lib", site / "cudnn" / "lib"]

    environment = control.server_environment({"LD_LIBRARY_PATH": "/opt/x"}, venv)
    assert environment["LD_LIBRARY_PATH"] == (
        f"{site / 'cublas' / 'lib'}:{site / 'cudnn' / 'lib'}:/opt/x"
    )
    assert environment["PYTHONUNBUFFERED"] == "1"
    assert "LD_LIBRARY_PATH" not in control.server_environment({}, tmp_path / "none")

    template = (checkout_root() / control.UNIT_TEMPLATE).read_text(encoding="utf-8")
    text = control.render_unit(template, tmp_path, dirs)
    assert "@CUDA_LIBRARY_PATH@" not in text
    assert (
        f"Environment=LD_LIBRARY_PATH={site / 'cublas' / 'lib'}:{site / 'cudnn' / 'lib'}"
        in text
    )


def test_up_waits_for_the_serving_line_and_reports_a_start_that_died(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "server.log"
    monkeypatch.setattr(control, "LOG_FILE", log)
    clock = FakeClock()
    log.write_text("2026-11-27T22:00:00 server: stopped\n", encoding="utf-8")
    offset = log.stat().st_size

    def sleep(seconds: float) -> None:
        clock.advance(seconds)
        with log.open("a", encoding="utf-8") as handle:
            handle.write(f"2026-11-27T22:00:05 server: {READY_MESSAGE} on port 8300\n")

    control.wait_until_serving(
        offset, lambda: True, "the server", clock=clock, sleep=sleep
    )
    assert clock.now == pytest.approx(0.2)

    log.write_text("the VRAM budget of 10752 MiB exceeds the card's 8192 MiB\n")
    with pytest.raises(control.ControlError) as died:
        control.wait_until_serving(
            0, lambda: False, "the server", clock=clock, sleep=clock.advance
        )
    assert str(died.value) == (
        "the server did not start: "
        "the VRAM budget of 10752 MiB exceeds the card's 8192 MiB"
    )
    log.write_text("")
    with pytest.raises(control.ControlError) as slow:
        control.wait_until_serving(
            0, lambda: True, "the server", timeout=1.0, clock=clock, sleep=clock.advance
        )
    assert "is not serving after 1 s" in str(slow.value)


def test_the_host_check_is_a_command_of_its_own(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("socket.gethostname", lambda: "gameon1")
    assert hostcheck.main() == 0
    monkeypatch.setattr("socket.gethostname", lambda: "branch")
    assert hostcheck.main() == 1
    assert "GAMEON1 only" in capsys.readouterr().err


# --- the token ----------------------------------------------------------------


def test_the_token_is_made_once_at_0600_and_never_printed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "autobot-server" / "token"
    monkeypatch.setattr(token, "token_path", lambda: path)
    assert token.main(hostname=lambda: "GAMEON1") == 0
    made = read_token(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert len(made) >= 43 and re.fullmatch(r"[A-Za-z0-9_-]+", made)
    first = capsys.readouterr().out
    assert str(path) in first and made not in first
    # A second run leaves the token as it is.
    assert token.main(hostname=lambda: "GAMEON1") == 0
    assert read_token(path) == made
    second = capsys.readouterr().out
    assert "already there" in second and made not in second


def test_the_token_is_not_made_off_the_server_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "token"
    monkeypatch.setattr(token, "token_path", lambda: path)
    assert token.main(hostname=lambda: "branch") == 1
    assert not path.exists()
    assert "GAMEON1 only" in capsys.readouterr().err


# --- the models ---------------------------------------------------------------


def test_the_language_model_is_never_pulled_only_named() -> None:
    chosen = fetch_models.pull_lines(ServerSettings(llm_model="qwen3:8b"))
    assert chosen[-1] == "  ollama pull qwen3:8b"
    none = "\n".join(fetch_models.pull_lines(ServerSettings()))
    assert "no language model is chosen yet" in none
    assert "ollama pull qwen3:8b" in none and "ollama pull llama3.1:8b" in none
    assert "Gemma 4" in none and "make server-eval" in none


def test_only_the_turbo_model_is_fetched_and_a_present_file_is_left(
    tmp_path: Path,
) -> None:
    present = tmp_path / "model.bin"
    present.write_bytes(b"weights")
    # A file that is there is never asked for again: no request is made.
    assert fetch_models.fetch("http://127.0.0.1:9/never", present) is False
    other = ServerSettings(stt_model=str(tmp_path / "large-v2"))
    with pytest.raises(fetch_models.FetchError) as refused:
        fetch_models.fetch_whisper(other)
    assert "only large-v3-turbo is fetched here" in str(refused.value)


def test_fetching_refuses_off_the_server_host(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert fetch_models.main(hostname=lambda: "branch") == 1
    assert "GAMEON1 only" in capsys.readouterr().err
