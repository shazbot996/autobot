"""`make server-token` on `GAMEON1`: make the shared bearer token (IS-13).

    python -m autobot_server.token

Writes 32 random bytes, as text, to `~/.config/autobot-server/token` at mode
`0600`, when no token is there yet, and prints the file's path. It never
prints the token: the owner opens the file himself and carries the value to
the NUC, where `make server-token` takes it at a hidden prompt (plan choice
5). An existing token is left as it is, so that running the target twice
does not lock the NUC out.

Only the owner runs this. An agent never does, and never reads the file.
"""

from __future__ import annotations

import os
import secrets
import socket
import sys
from collections.abc import Callable
from pathlib import Path

from autobot_server.auth import TOKEN_MODE, token_path
from autobot_server.config import ServerConfigError, load_settings
from autobot_server.hostcheck import WrongHostError, require_server_host

TOKEN_BYTES = 32


class TokenWriteError(Exception):
    """The token file could not be made. The message is one line, without the token."""


def create_token(path: Path) -> bool:
    """Make the token file at `path`, mode `0600`; false when one is already there."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # O_EXCL: never over an existing token; the mode holds from the first byte.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, TOKEN_MODE)
    except FileExistsError:
        return False
    except OSError as error:
        raise TokenWriteError(f"cannot write {path}: {error.strerror}") from None
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(f"{secrets.token_urlsafe(TOKEN_BYTES)}\n")
    os.chmod(path, TOKEN_MODE)
    return True


def main(hostname: Callable[[], str] = socket.gethostname) -> int:
    try:
        require_server_host(hostname(), load_settings().hostname)
        path = token_path()
        made = create_token(path)
    except (ServerConfigError, TokenWriteError, WrongHostError) as error:
        print(error, file=sys.stderr)
        return 1
    if made:
        print(f"server token written: {path}")
    else:
        print(f"a server token is already there, and was left as it is: {path}")
    print(
        "open that file yourself and give its one line to 'make server-token' "
        "on the NUC; restart the server after making a new token"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
