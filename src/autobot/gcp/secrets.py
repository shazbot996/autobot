"""Storing a pasted secret: in Secret Manager, and nowhere else (GC-9).

`store_secret` is the one path a secret's value takes: from the masked field
it was pasted into, through the service account's connection, to a new
version of an expected secret. The value is held in a `bytearray` so that it
can be cleared, and it is cleared here whether the call worked or not. It is
never logged, never written to a file, and never part of an error's text.
The Agent editor's edit of the weather API key uses this same function.
"""

from __future__ import annotations

from autobot.config import GcpConfig
from autobot.gcp.client import GcpError, GoogleCloud
from autobot.gcp.needs import expected_secret


def clear(value: bytearray) -> None:
    """Overwrite a buffer with zero bytes, in place."""
    value[:] = bytes(len(value))


def _without(text: str, value: bytearray) -> str:
    """`text` with the secret's value taken out, should a message ever echo it."""
    shown = value.decode("utf-8", errors="replace")
    return text.replace(shown, "[the value]") if shown.strip() else text


def store_secret(
    cloud: GoogleCloud, config: GcpConfig, secret: str, value: bytearray
) -> str:
    """Add `value` to Secret Manager as a new version of `secret`; clear `value`.

    Returns the new version's name. Raises `GcpError`, in one line naming the
    secret and the cause, when the secret is not one autobot expects, when
    nothing was pasted, or when Google Cloud refuses.
    """
    try:
        if expected_secret(secret) is None:
            raise GcpError(f"{secret} is not a secret autobot expects: not stored")
        if not value:
            raise GcpError(f"nothing was pasted: {secret} is unchanged")
        try:
            return cloud.add_secret_version(secret, value)
        except GcpError as error:
            cause = _without(str(error), value)
            raise GcpError(
                f"{secret} was not stored in {config.project}: {cause}"
            ) from None
    finally:
        clear(value)
