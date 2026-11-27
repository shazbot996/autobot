---
run: schiele/0084
at: 2026-11-27T14:15:57.439Z
mode: direct
agent: direct:general
model: claude:claude-opus-5-5
target: null
template: direct/general
prompt-sha: c3f4a8359cf5
template-sha: f7093a112dd1
commit: 1bc35fc+
attachments: []
read:
  - AGENTS.md
outcome: success
usage:
  input: 241014
  output: 4927
  peak: 36105
  window: 1000000
cost: 0
harness:
  provider: claude
  kind: sdk
  workspace: self
  access: build
  turns: 8
  sandbox: true
  changed:
    - CLAUDE.md
---

I added the CLAUDE.md section, but I couldn't make the settings change. `.claude/settings.json` is blocked by the sandbox for both reading and writing. I didn't try another way around it, so you'll need to add the entry yourself. Nothing inside `../assistant` was changed, and I didn't read its token or key files.

## 1. `.claude/settings.json` (not done; please add this)

I couldn't read the file, so I can't see your existing settings. Add this alongside whatever is already in `"permissions"`:

```json
{
  "permissions": {
    "additionalDirectories": ["/home/schiele/projects/assistant"]
  }
}
```

If `additionalDirectories` already exists, add the path to its list. I used the absolute path rather than `../assistant` so it doesn't depend on where a session starts.

## 2. How `assistant` is run

- **What it is:** it isn't a Python package or a git repository. It's a `Makefile` over a few small programs built around a local Home Assistant:
  - **`ha/`:** Home Assistant in Docker Compose on `http://localhost:8123`. Its REST API is turned on (`api:`), it has the Bird Buddy and HACS custom components, and it serves `fourway.html` and `birdbuddy.html` at `/local/`.
  - **`fourway/server.py`:** a small server on port 8099 using only the standard library. It serves files and passes `/api/*` requests to Home Assistant.
  - **`birdbuddy/server.py`:** an aiohttp server on port 8199, run with its own venv at `birdbuddy/venv/bin/python3`.
  - **`voicecode/`:** VoiceCode BBS, a separate app with its own venv.
  - **`tunnel-up` / `tunnel-down`:** start and stop a Cloudflare tunnel.
- **Entry points:** everything runs through `make` in that directory: `fourway-server`, `fourway`, `birdbuddy-server`, `birdbuddy`, `birdbuddy-install` (uses sudo), `tunnel-up`, `tunnel-down` and `voicecode`.
- **Python environment:** `fourway-server` uses the system `python3`. Bird Buddy and VoiceCode each have their own venv. There's no project-wide venv.
- **Local HTTP API:** yes. Home Assistant's REST and WebSocket API is on port 8123 and needs a long-lived token. The fourway server also passes it through on port 8099. Note that `make fourway` opens the page Home Assistant serves on 8123, not the 8099 server. I didn't check whether Home Assistant is running right now.

## 3. CLAUDE.md

There was no CLAUDE.md, so I created one. It points to `AGENTS.md` first, then has a section on `assistant`:
- what it is and where it lives;
- a table of the four targets autobot may call and which interpreter each uses;
- which targets autobot must never call (`birdbuddy-install`, the tunnel targets, `voicecode`);
- rules for future sessions: don't change anything in `../assistant`, don't read its secrets, don't start its servers.

## 4. Recommendation

**Call the `make` targets as subprocesses now, and use Home Assistant's local API later.**

- **Camera agent (AG-6): use subprocesses.** This is what you already do by hand, and the spec says the camera agent runs local commands.
  - Run each target with `make -C <dir> <target>`, passing the command as an argument list rather than through a shell.
  - `fourway-server` keeps running, so start it detached (`start_new_session=True`, output sent to a log), then wait until port 8099 answers before running `make fourway`.
  - Refuse on any machine other than the NUC, like the other NUC-only paths.
  - Calling through `make` means autobot never needs to know `assistant`'s Python environment. That matters because there isn't one shared venv.
- **Home Assistant actions (lights, scripts, Bird Buddy data): use the REST API on `localhost:8123`.** It already runs as a service, and HTTP calls are easier to test with fakes than subprocesses. Its token is a secret, so it goes in an environment variable or a file outside the repository, never in `autobot.toml`.

Suggested config, which I haven't written yet:

```toml
[assistant]
dir = "/home/schiele/projects/assistant"
make = "make"                       # the interpreter is make; each target picks its own Python
ha_url = "http://localhost:8123"    # for later API use; token comes from the environment

[[agents]]
name = "camera"
commands = [
  { target = "fourway-server", detach = true, wait_for_port = 8099 },
  { target = "fourway" },
]
```

If you'd rather name the interpreter directly, the per-program fields would be `python = "python3"` for fourway and `python = "birdbuddy/venv/bin/python3"` for Bird Buddy. But that would copy what `assistant`'s Makefile already defines, so I'd keep `make` as the entry point.

I didn't run the tests, lint or type check, because no code changed.

Files changed:
- `CLAUDE.md` (new): points to `AGENTS.md` and describes `assistant`, its path, how to run it, which targets autobot may call, and the rules for sessions.
