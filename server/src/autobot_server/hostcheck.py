"""The server runs on its configured host and nowhere else (IS-1, IS-25).

The same check the NUC makes of `host.nuc_hostname` before it opens its
audio hardware (TS-11), turned around: `[server] hostname` names the one
machine the server may bind its port on. Hostnames are compared without
regard to case, since Windows reports `GAMEON1` and WSL may not.
"""

from __future__ import annotations

import socket
import sys


class WrongHostError(Exception):
    """This machine is not the configured server host. The message is one line."""


def require_server_host(hostname: str, configured: str) -> None:
    """Raise `WrongHostError` unless `hostname` is the configured server host."""
    if not configured or hostname.lower() != configured.lower():
        raise WrongHostError(
            f"the inference server runs on {configured or '(no [server] hostname)'} "
            f"only; this host is {hostname}"
        )


def main() -> int:
    """`python -m autobot_server.hostcheck`: exit 0 on the server host, 1 with one line else.

    What `make server-setup` asks before the server's environment exists.
    """
    from autobot_server.config import ServerConfigError, load_settings

    try:
        require_server_host(socket.gethostname(), load_settings().hostname)
    except (ServerConfigError, WrongHostError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
