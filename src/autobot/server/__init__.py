"""autobot's side of the GPU inference server on `GAMEON1`. No network here yet.

The server is a second program, `autobot_server` under `server/`, that
transcribes, routes and chats for the NUC over the LAN. This package is what
the NUC knows of it:

- `contract`: the shapes of every request and response, as the examples
  under `server/contract/` hold them, and the contract's version (IS-22).

The NUC never depends on the server (IS-4), and nothing here sends audio, a
transcript or text anywhere outside the house (`CN-1`, `CN-2`).
"""
