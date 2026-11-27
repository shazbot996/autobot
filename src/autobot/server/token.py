"""The shared bearer token on the NUC: read by the client, entered by the owner (IS-13).

The token lives in `[server] token_file`, by default the git-ignored
`secrets/server-token`, at mode `0600`. `read_token` is the client's one
way to it; a file that is missing, empty or readable by anyone else is a
`TokenError` whose line the main screen shows, and the server then counts
as down (IS-23). The token is never logged, printed or put in a message.

    python -m autobot.server.token

is what `make server-token` runs on the NUC. It asks for the token at a
hidden prompt, as a password is asked for, and writes the file at `0600`.
The owner made the value on `GAMEON1` with the same target and carries it
over himself: nothing copies it across the LAN (plan choice 5). Only the
owner runs this; an agent never does, and never reads the file.
"""

from __future__ import annotations

import getpass
import os
import stat
import sys
from collections.abc import Callable
from pathlib import Path

from autobot.config import ConfigError, load_config

# The only mode the token file may have.
TOKEN_MODE = 0o600
# What a missing token is called on the main screen (IS-13).
NO_TOKEN = "no server token: run make server-token"


class TokenError(Exception):
    """The token file cannot be used. The message is one line, without the token."""


def read_token(path: Path) -> str:
    """The token in `path`; `TokenError` when it is missing, empty or not `0600`."""
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        raise TokenError(NO_TOKEN) from None
    except OSError as error:
        raise TokenError(f"cannot read the server token: {error.strerror}") from None
    if mode != TOKEN_MODE:
        raise TokenError(
            f"the server token has mode {mode:04o}, not {TOKEN_MODE:04o}: "
            f"run chmod {TOKEN_MODE:o} {path}"
        )
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise TokenError(f"cannot read the server token: {error.strerror}") from None
    except UnicodeDecodeError:
        raise TokenError("the server token is not text") from None
    if not token:
        raise TokenError(NO_TOKEN)
    return token


def write_token(path: Path, token: str) -> None:
    """Write `token` to `path` at mode `0600`, over whatever was there."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Made at 0600 from the first byte, and never world-readable meanwhile.
        temporary = path.with_name(path.name + ".tmp")
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, TOKEN_MODE
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(f"{token}\n")
        os.chmod(temporary, TOKEN_MODE)
        os.replace(temporary, path)
    except OSError as error:
        raise TokenError(f"cannot write {path}: {error.strerror}") from None


def main(prompt: Callable[[str], str] = getpass.getpass) -> int:
    try:
        path = load_config().server.token_path
        token = prompt("server token (from GAMEON1; it is not shown): ").strip()
        if not token or any(character.isspace() for character in token):
            raise TokenError(
                "that is not a token: paste the one line of the file "
                "'make server-token' made on GAMEON1"
            )
        write_token(path, token)
    except (ConfigError, TokenError) as error:
        print(error, file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("no token entered; nothing was written", file=sys.stderr)
        return 1
    print(f"server token written: {path}")
    print("'make server-ping' asks the server with it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
