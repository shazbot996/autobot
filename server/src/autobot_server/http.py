"""The server's one port: HTTP with JSON, and server-sent events for chat (IS-2).

A standard library `ThreadingHTTPServer`, bound to `0.0.0.0` inside WSL on the
port of `[server] url`, hands every request to `App.handle` and writes back
what it answers (plan choice 1). This module holds the socket and nothing
else: no endpoint, no check and no log line of its own.

A request body is read only up to `MAX_BODY` bytes; a larger one is refused
with `413` before it is read. A streamed response is sent a chunk at a time
until it ends or the NUC hangs up.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from autobot_server.app import App, Response

# Every address of the host: the firewall rules, not the bind, keep the port
# to the NUC (IS-13).
BIND_ADDRESS = "0.0.0.0"
# The largest request body read, in bytes: a minute of 16 kHz 16-bit audio,
# base64-encoded, is under 3 MiB.
MAX_BODY = 16 * 1024 * 1024


class Server(ThreadingHTTPServer):
    """The HTTP server, holding the app every request is handed to."""

    daemon_threads = True

    def __init__(self, app: App, port: int, address: str = BIND_ADDRESS):
        self.app = app
        super().__init__((address, port), Handler)


class Handler(BaseHTTPRequestHandler):
    """One request: read it, hand it to the app, write the answer."""

    server: Server

    def do_GET(self) -> None:
        self._serve("GET")

    def do_POST(self) -> None:
        self._serve("POST")

    def do_PUT(self) -> None:
        self._serve("PUT")

    def do_DELETE(self) -> None:
        self._serve("DELETE")

    def log_message(self, format: str, *args: Any) -> None:
        """Silent: the app writes the one log line of each request (IS-5)."""

    def _serve(self, method: str) -> None:
        app = self.server.app
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0:
            self._send(app.refuse(method, self.path, 0, 400, "bad Content-Length"))
            return
        if length > MAX_BODY:
            self._send(
                app.refuse(
                    method,
                    self.path,
                    length,
                    413,
                    f"the request body is over {MAX_BODY} bytes",
                )
            )
            return
        body = self.rfile.read(length) if length else b""
        headers = {name: value for name, value in self.headers.items()}
        self._send(app.handle(method, self.path, headers, body))

    def _send(self, response: Response) -> None:
        self.send_response(response.status)
        for name, value in response.headers:
            self.send_header(name, value)
        try:
            if isinstance(response.body, bytes):
                self.send_header("Content-Length", str(len(response.body)))
                self.end_headers()
                self.wfile.write(response.body)
                return
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            for chunk in response.body:
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            # The NUC hung up, as it does on a timeout (IS-4).
            self.close_connection = True
        finally:
            # A stream gives its job's place back even when it was cut short.
            closer = getattr(response.body, "close", None)
            if callable(closer):
                closer()


def serve(app: App, port: int, address: str = BIND_ADDRESS) -> Server:
    """The server bound to `address:port`; `serve_forever()` runs it."""
    return Server(app, port, address)
