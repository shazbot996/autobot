"""The shared bearer token every request must carry (IS-13).

On `GAMEON1` the token lives outside the checkout, in
`~/.config/autobot-server/token`, a file of mode `0600` that the owner
creates with `make server-token`. The server reads it once at start and
refuses a file anyone but its owner could read or write. A request whose
`Authorization` header is not `Bearer <token>` gets `401`; the two are
compared in constant time.

The token is a secret: nothing here logs it, prints it or puts it in a
message.
"""

from __future__ import annotations

import hmac
import stat
from collections.abc import Mapping
from pathlib import Path

# The token file on the server, outside the checkout (IS-13).
TOKEN_PATH = Path("~/.config/autobot-server/token")
# The only mode the token file may have.
TOKEN_MODE = 0o600

BEARER = "bearer"


class TokenError(Exception):
    """The token file cannot be used. The message is one line, without the token."""


def token_path() -> Path:
    """The absolute path of the server's token file."""
    return TOKEN_PATH.expanduser()


def read_token(path: Path) -> str:
    """The token in `path`; `TokenError` when it is missing, empty or not `0600`."""
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        raise TokenError(f"no server token at {path}: run make server-token") from None
    except OSError as error:
        raise TokenError(f"cannot read {path}: {error.strerror}") from None
    if mode != TOKEN_MODE:
        raise TokenError(
            f"the server token {path} has mode {mode:04o}, not {TOKEN_MODE:04o}: "
            f"run chmod {TOKEN_MODE:o} {path}"
        )
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise TokenError(f"cannot read {path}: {error.strerror}") from None
    except UnicodeDecodeError:
        raise TokenError(f"the server token {path} is not text") from None
    if not token:
        raise TokenError(f"the server token {path} is empty: run make server-token")
    return token


def authorized(headers: Mapping[str, str], token: str) -> bool:
    """Whether `headers` carry `Authorization: Bearer <token>`.

    `headers` are keyed in lower case, as `app.handle` passes them.
    """
    scheme, _, given = headers.get("authorization", "").strip().partition(" ")
    if scheme.lower() != BEARER or not token:
        return False
    return hmac.compare_digest(given.strip().encode(), token.encode())
