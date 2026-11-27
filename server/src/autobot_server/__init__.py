"""autobot's GPU inference server on `GAMEON1`, in WSL (IS-1).

The NUC stays the front end; this server transcribes, routes and chats for it
over the LAN, on one port, with a shared bearer token on every request (IS-2,
IS-13). It keeps nothing between requests but the models it has loaded
(IS-5), reaches no cloud service and holds no Google Cloud credential (IS-7).

- `config`: the `[server]` table of `config/autobot.toml`, read on its own.
- `contract`: the shapes of every request and response (IS-22).
- `hostcheck`: the server runs on the configured hostname only (IS-25).
- `auth`: the token file and the bearer check (IS-13).
- `gpu`: the VRAM budget and the card's memory (IS-10).
- `job`: one model's readiness and its admission of requests (IS-23, IS-24).
- `health`: the answer to `GET /health` (IS-11).
- `app`: the dispatch of a request to its answer, with no socket.
- `http`: the HTTP server on the configured port (IS-2).

It does not import `autobot`: `GAMEON1` installs none of the NUC's audio and
cloud dependencies.
"""

# The server's version, reported by `/health` (IS-11).
__version__ = "0.1.0"
