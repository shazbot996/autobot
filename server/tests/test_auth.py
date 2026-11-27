"""`autobot_server.auth` and `hostcheck`: the token file, the bearer check and the host (IS-13, IS-25)."""

from __future__ import annotations

from pathlib import Path

import pytest

from autobot_server.auth import (
    TOKEN_PATH,
    TokenError,
    authorized,
    read_token,
    token_path,
)
from autobot_server.hostcheck import WrongHostError, require_server_host
from tests.server_fakes import TOKEN, write_token


def test_the_token_lives_outside_the_checkout() -> None:
    assert TOKEN_PATH == Path("~/.config/autobot-server/token")
    assert token_path() == Path.home() / ".config" / "autobot-server" / "token"


def test_a_token_file_at_0600_is_read(tmp_path: Path) -> None:
    assert read_token(write_token(tmp_path)) == TOKEN


def test_a_missing_token_file_names_the_make_target(tmp_path: Path) -> None:
    with pytest.raises(TokenError, match="run make server-token"):
        read_token(tmp_path / "token")


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o400, 0o700])
def test_a_token_file_at_another_mode_is_refused(tmp_path: Path, mode: int) -> None:
    path = write_token(tmp_path, mode=mode)
    try:
        with pytest.raises(
            TokenError, match=f"has mode {mode:04o}, not 0600"
        ) as caught:
            read_token(path)
    finally:
        path.chmod(0o600)
    assert TOKEN not in str(caught.value)


def test_an_empty_token_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(TokenError, match="is empty"):
        read_token(write_token(tmp_path, token="  "))


@pytest.mark.parametrize(
    "header",
    [
        f"Bearer {TOKEN}",
        f"bearer {TOKEN}",
        f"  Bearer   {TOKEN}  ",
    ],
)
def test_the_right_bearer_is_authorized(header: str) -> None:
    assert authorized({"authorization": header}, TOKEN)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"authorization": ""},
        {"authorization": TOKEN},
        {"authorization": f"Basic {TOKEN}"},
        {"authorization": f"Bearer {TOKEN}x"},
        {"authorization": "Bearer "},
    ],
)
def test_anything_else_is_not(headers: dict[str, str]) -> None:
    assert not authorized(headers, TOKEN)


def test_no_token_authorizes_nothing() -> None:
    assert not authorized({"authorization": "Bearer "}, "")


def test_the_configured_host_may_serve() -> None:
    require_server_host("GAMEON1", "GAMEON1")
    require_server_host("gameon1", "GAMEON1")


def test_any_other_host_is_refused_in_one_line() -> None:
    with pytest.raises(WrongHostError, match="runs on GAMEON1 only; this host is nuc"):
        require_server_host("nuc", "GAMEON1")
    with pytest.raises(WrongHostError, match=r"no \[server\] hostname"):
        require_server_host("GAMEON1", "")
