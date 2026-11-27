# CLAUDE.md

`AGENTS.md` is the guide for agents in this repository; read it first. This file adds what Claude Code sessions need beyond it.

@AGENTS.md

## The peer project `assistant`

**What it is.** `assistant` is the owner's separate home-automation project at `/home/schiele/projects/assistant` (`../assistant` from here). It is not a git repository and not a Python package: it is a `Makefile` over a few small programs built around a local Home Assistant:

- `ha/`: Home Assistant (`ghcr.io/home-assistant/home-assistant:stable`) in Docker Compose, host networking, on `http://localhost:8123`, REST API enabled (`api:`), with the Bird Buddy and HACS custom components. `ha/config/www/` serves `fourway.html` and `birdbuddy.html` at `/local/`.
- `fourway/`: the four-camera view. `fourway/server.py` is a standard-library HTTP server on port 8099 (`FOURWAY_PORT`) that serves static files and proxies `/api/*` to Home Assistant (`HA_URL`).
- `birdbuddy/`: the Bird Buddy gallery, an aiohttp server on port 8199 with its own venv (`birdbuddy/venv/bin/python3`).
- `voicecode/`: VoiceCode BBS, a separate curses prompt workshop with its own venv (`voicecode/venv/`).
- `tunnel-up` / `tunnel-down`: a Cloudflare tunnel.

**How it is run.** Always from its own directory, through `make` (`make -C /home/schiele/projects/assistant <target>`); a bare `make` lists the targets. The servers run in the foreground and are long-running.

| Target | Does | Interpreter |
|---|---|---|
| `fourway-server` | frees port 8099, runs `fourway/server.py` (foreground) | system `python3` |
| `fourway` | opens `http://localhost:8123/local/fourway.html` in Firefox, detached | none |
| `birdbuddy-server` | frees port 8199, runs the gallery server (foreground) | `birdbuddy/venv/bin/python3` |
| `birdbuddy` | opens `http://localhost:8199/` in Firefox, detached | none |

**What autobot may call.** Only `fourway-server` and `fourway` (the `camera` agent, AG-6), and later `birdbuddy-server` and `birdbuddy`, all on the NUC only. autobot never calls `birdbuddy-install` (uses `sudo`, writes into Home Assistant's config), `tunnel-up` / `tunnel-down`, or `voicecode`. The command lines, the directory and any interpreter live in `config/autobot.toml`, never in code.

**Rules for agents.**

- Never change anything inside `../assistant`; it is read-only context from this repository.
- Never read its secrets: `tunnel-token.txt`, `birdbuddy-key.txt`, `ha/config/secrets.yaml`, `ha/config/.storage/`, and the Home Assistant database and logs.
- Never start its servers, the Home Assistant container or the tunnel from a session; they are long-running.
