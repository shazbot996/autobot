---
run: schiele/0160
at: 2026-11-28T14:12:34.293Z
mode: create
agent: create
model: claude:claude-opus-5-5
target: docs/specs/active/autobot-inference-server-console-SPEC.md
template: create
prompt-sha: 8786d174a41f
template-sha: d7922285ccf1
commit: 9d585ec
attachments: []
read:
  - AGENTS.md
  - prompts/builtin/examples/spec.md
  - docs/context/INFRASTRUCTURE.md
  - docs/specs/active/autobot-gpu-inference-server-SPEC.md
  - server/src/autobot_server/ollama.py
  - server/src/autobot_server/route.py
  - server/src/autobot_server/__main__.py
  - server/src/autobot_server/config.py
  - server/src/autobot_server/eval.py
  - server/src/autobot_server/job.py
  - src/autobot/agents/core.py
  - src/autobot/cascade.py
  - config/autobot.toml
  - docs/context/CONVENTIONS.md
  - docs/context/CONSTRAINTS.md
  - docs/context/GLOSSARY.md
  - server/src/autobot_server/http.py
  - docs/decisions/0006-full-screen-tui-and-training-on-the-owners-voice-ADR.md
  - Makefile
  - server/src/autobot_server/app.py
  - server/src/autobot_server/control.py
  - server/src/autobot_server/runtime.py
  - pyproject.toml
  - server/pyproject.toml
  - docs/specs/active/autobot-wake-word-training-studio-SPEC.md
outcome: success
usage:
  input: 887660
  output: 37559
  peak: 130043
  window: 1000000
cost: 0
---

Write a new spec: docs/specs/active/autobot-inference-server-console-SPEC.md,
"autobot — inference server console". Rule prefix: IC-.

Context to read first: AGENTS.md; docs/context/*.md; ADR 0006
(no web interface; the TUI is the application); the GPU inference server
spec (IS-1 to IS-27) and both of its plans; docs/context/INFRASTRUCTURE.md
"The server as it runs on GAMEON1"; server/src/autobot_server/
(route.py ROUTE_SYSTEM, ollama.py, eval.py, job.py, __main__.py);
src/autobot/agents/core.py (system_prompt, FORMAT, HANDOFF_FORMAT, tools_of);
src/autobot/cascade.py; config/autobot.toml [server] and the core agent.

What it is: a small web-based admin console on GAMEON1, beside the inference
server, where the owner configures, tries out and controls the home
language model (today qwen3:8b on Ollama) and learns how its settings
behave. It is to the home model what the wake-word training studio is to
the wake word: a candidate is configured and measured while the installed
one keeps serving the NUC, then installed, and can be reverted. Because the
NUC already falls back while route/chat are not ready (IS-4), swapping
models is safe.

Terminology to adopt and add to GLOSSARY.md: edge layer (NUC), home layer
(GAMEON1, "home model"), cloud layer (Gemini). "Tier" keeps its IS-17
meaning (routing cascade). Agent = runs a spoken session; tool = a function
the home model asks for and the NUC runs.

Rules the spec must cover, each with its reason:
- Host and reach: runs on GAMEON1 only (hostname check as IS-25), binds
  127.0.0.1 on its own port (propose 8301), used from a browser on
  GAMEON1's Windows side via WSL mirrored networking; never on the LAN,
  never through the Cloudflare tunnel; no firewall rule added. Host/Origin
  header check and a per-session CSRF token. Decide whether the 8300
  bearer token is also required.
- Process: decide whether it is a separate process from the server or part
  of it. Recommended: separate, so a console fault never takes down port 8300;
  it reads /health on loopback and reloads the server through a loopback-only
  admin path or `systemctl --user`. Standard library HTTP, no framework, as
  the server is; static HTML and small JS; tests drive a pure dispatch
  function on fakes, with no GPU, no model and no network (IS-12 style).
- Screens: Status (jobs, models, VRAM against the IS-10 budget, which
  profile is installed); Models (what Ollama holds, size, fits the budget;
  shows the `ollama pull` command for a new one and never runs it, as IS-7
  and the Google Cloud screen's pattern require); Profiles (a profile =
  model tag + runtime and sampling parameters + the route prompt + the chat
  format text; candidate vs installed; install; revert); Parameters (every
  setting with a plain-language explanation, its safe range, and a
  recommended default for qwen3:8b on a 2080 Ti, separately for routing
  and for chat: temperature, top_p, top_k, repeat penalty, max tokens,
  context length, keep_alive, think on/off, KV cache type and anything else
  ollama.py sends; say which need an Ollama restart, which is the owner's);
  Prompts (edit the route prompt and format text; show read-only the
  complete system prompt the NUC would send for the core agent, rendered
  from the checkout's config/autobot.toml); Playground (a routing request,
  a chat, and a tool-call dry run that shows the tool calls a candidate
  makes without running any tool); Evaluation (run server-eval against a
  candidate, compare reports side by side, with IS-26's criterion shown).
  The owner is configuring an LLM for the first time: the spec explains
  each parameter well enough for him to choose.
- State: the server stays stateless for requests (IS-5). Playground
  conversations live in the browser's memory only, are never logged or
  written. Decide where profiles and the installed/candidate state are
  stored: [server] (or [[server.profiles]]) in GAMEON1's checkout of
  config/autobot.toml, or a file of the server's own. Weigh CONVENTIONS'
  one-file rule against the two checkouts drifting apart. Agent definitions,
  tools and conversations stay on the NUC (IS-21, ADR 0002).
- No cloud: the console reaches no cloud service and holds no credential
  (IS-7); no owner audio ever (TS-5, IS-6).
- Runtime control: make targets console-up, console-down, console-status,
  console-logs, console-install (systemd user unit), each in the bare `make`
  summary.
- Checks: say how tests, lint and typecheck run on GAMEON1, where only
  .venv-server exists, and that the console's tests also run under
  `make test` on the NUC.

Out of scope: moving weather/time/camera from keyword agents to tools of
the core agent and demoting the keyword rules to offline reflexes (the next
spec; note that removing [[routes]] today would remove modes from tier 3
via cascade.agent_modes); speech-to-text and text-to-speech changes;
anything on the NUC beyond reading.

Changes needed elsewhere to list: a new ADR 0008 (a web console on GAMEON1,
superseding ADR 0006's "no web interface" for that host only); IS-1 (a
second loopback caller), IS-7 and IS-10 (model chosen by installed profile),
IS-26; AGENTS.md (the console, its targets, what agents must not touch);
ARCH.md, SCHEMA.md, GLOSSARY.md, INFRASTRUCTURE.md. Also note the
still-missing ADR 0007 and the CN-1/CN-2 amendment from the server spec.

Put the questions only the owner can answer under Open Questions: the
port, separate process or not, where profiles are stored, whether the
console may ever run `ollama pull`, and whether reaching it from another
LAN machine is ever wanted.

The plan made from this spec will be written in cr2 and applied on GAMEON1.
