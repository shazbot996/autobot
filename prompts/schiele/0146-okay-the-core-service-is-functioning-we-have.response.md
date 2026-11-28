---
run: schiele/0146
at: 2026-11-28T13:17:46.673Z
mode: direct
agent: direct:ask
model: claude:claude-opus-5-5
target: null
template: direct/ask
prompt-sha: 02770f689d1e
template-sha: 44af0dfef32e
commit: 0f328f0
attachments: []
read:
  - AGENTS.md
  - docs/specs/active/autobot-gpu-inference-server-SPEC.md
  - src/autobot/agents/local_model.py
  - src/autobot/agents/question.py
  - docs/plans/active/gpu-inference-server-PLAN.md
  - src/autobot/agents/core.py
  - config/autobot.toml
  - src/autobot/cascade.py
  - docs/plans/active/gpu-inference-server-bring-up-on-gameon1-PLAN.md
  - docs/context/INFRASTRUCTURE.md
  - server/src/autobot_server/route.py
  - server/src/autobot_server/__main__.py
  - src/autobot/tui/face.py
outcome: success
usage:
  input: 832605
  output: 14960
  peak: 111972
  window: 1000000
cost: 0
harness:
  provider: claude
  kind: sdk
  workspace: self
  access: read
  turns: 22
  sandbox: false
---

# The three-layer assistant: where it stands and what to do next

## 1. Is it working?

I can't run tests or start anything from here; this run has no shell. What I can confirm comes from the code and from what earlier runs recorded:

- **The server on GAMEON1 is up and verified.** `docs/context/INFRASTRUCTURE.md` records:
  - `make server-status` shows speech-to-text (`stt`) on `large-v3-turbo`, and routing (`route`) and `chat` on `qwen3:8b`, all ready.
  - `make server-check` passes on all four endpoints.
  - `make server-ping` on the NUC reads `up`, contract version 1.
  - Both models together use about 7.9 GB of the card's 11 GB.
- **The model has been measured once.** `make server-eval` on `qwen3:8b` picked the right agent 93.3% of the time, and the right agent, mode and parameters 71.7%. Almost all the misses are the weather agent: it confuses today with `forecast`, and gets the `day` wrong. It stayed within the latency budget.
- **What you saw is the routing working as designed.** In `src/autobot/cascade.py`, a request with no keyword match goes to tier 2 (a meaning match on the NUC), then tier 3 (the server's model choosing an agent). If nothing is accepted, the router hands the request to the `core` agent (`src/autobot/agents/core.py`, `model = "local"`). `core` then asks qwen3 first, then Gemini, then says "the server is away" (`fallback` in `config/autobot.toml`).
- **To check which model answered:** the detail panel prints `answered by the local model (qwen3:8b)` (`ANSWERED_BY` in `core.py`). If it says `gemini`, the call fell back.
- **The architecture is correct against the spec.** Two things are still missing:
  - **Speech-to-text on the server is off.** `[server] stt_enabled = false`, so everything is still heard by the NUC's small `tiny.en`. It stays off until you amend `CN-1` in `CONSTRAINTS.md`. That amendment is yours to make, and it is probably the biggest quality gain available.
  - **No tools exist yet.** Plan choice 9 says "No tool ships". The core agent can call tools, but none are defined.

One update to make: `INFRASTRUCTURE.md` still says no face session has been routed by tier 3 or answered by `core` on the real server. Your test changes that.

## 2. One correction to your picture

Your keyword commands don't come from the wake-word engine. Your wake phrase is `kind = "text_match"`: the NUC's `tiny.en` transcribes everything it hears, and both the wake and the commands are plain string matches on that text (`autobot.routing.match_route`). A trained openWakeWord model can only fire on its one phrase; it can't hear commands at all. This makes the pivot easier: no ML layer has to be retrained. The keyword layer is just `[[routes]]` data.

## 3. Terminology

The code already uses "tier" for the three routing steps (`TIER_KEYWORD`, `TIER_SEMANTIC`, `TIER_SERVER`), so don't reuse it for your three levels. I suggest calling the three levels **layers**, named by where they run:

| Layer | Runs on | What it is | Word in the code today |
|---|---|---|---|
| **Edge layer** ("reflexes") | NUC, CPU | Wake word, voice detection, `tiny.en`, piper, `cancel` and yes/no, keyword rules, the agents' own code | `model = "none"`, routing tiers 1 and 2 |
| **Home layer** ("home model") | GAMEON1, GPU | `large-v3-turbo` speech-to-text, `qwen3:8b` on Ollama: routing, chat, and later tool calls | `model = "local"`, routing tier 3, `core` |
| **Cloud layer** | Google Cloud, as the service account | Gemini (`gemini-3.5-flash-lite`), and any other Vertex models later | `model = "gemini"`, handoff |

- **Models:** "wake-word model", "home model" (or "home LLM"), "cloud model". I'd drop "local" for GAMEON1, because the NUC is local too. The config value `local` can stay; the glossary just says it means the home model.
- **Agents and tools:**
  - An **agent** runs a spoken session with you: `core`, `question`, `echo`, `introduce`.
  - A **tool** is a function the home model asks for and the NUC runs: a weather lookup, reading the clock, starting the cameras.
  - Weather, time and camera are moving from being agents to being tools.
- **Reflexes:** what must work with GAMEON1 off: the wake word, `cancel`, yes/no, and a few offline commands.

These words belong in `GLOSSARY.md`. The spec already lists "inference server", "tier", "cascade", "core agent", "handoff" and "tool" as glossary entries still to add.

## 4. Where it stands against the plan

- **Built:** the whole of `docs/plans/active/gpu-inference-server-PLAN.md` (12 of 12 steps) and the bring-up plan for GAMEON1 (8 of 8).
- **Not done:** the follow-on document changes the spec lists:
  - the `CN-1`/`CN-2` amendment;
  - **ADR 0007** (home models beside Gemini). `docs/decisions/` stops at 0006;
  - the ARCH, GLOSSARY and SCHEMA updates;
  - the changes to the other specs (RR-1, AS-1, AG-1).
- **Not done:** the other candidate models. Gemma 4 and Llama 3.1 8B (your "I forget what else") are neither pulled nor measured.

You already have **two** ways for the home model to reach local features:

1. **`/v1/route` (tier 3)** classifies what you said into one of your agents and fills its parameters. The NUC then runs that agent's code. Weather, time and camera are already reachable this way, without keywords.
2. **`/v1/chat` with tools (IS-21).** `core` sends tool schemas, the model answers with tool calls, and the NUC runs them through `Seams.tools` for up to `tool_rounds` rounds. Everything works except that no real tools exist.

So the change you want is mostly configuration and wiring, not new infrastructure.

## 5. Advice on removing the keywords

- **Don't delete `[[routes]]` yet.**
  - In `cascade.py`, `agent_modes()` takes an agent's modes from its routes. Remove the `forecast` route and tier 3 loses the `forecast` mode.
  - Tier 2 embeds each route's `command` and `examples`.
  - `question.strip_command` also reads the route commands.
- **Demote the keywords instead of removing them.** Make them offline reflexes: used only when the server's `route` job isn't ready, or only when the whole utterance is the command.
- **Then expose the agents' functions to `core` as tools.** That puts one conversational brain in front of everything, with the edge layer as the safety net when GAMEON1 is off.

That is a second spec. It comes after the console, because it's much easier to tune a tool-calling prompt once you have a console to try it in.

## 6. The admin console on GAMEON1

I agree with the direction, and copying the studio's pattern (candidate, compare, install, revert) fits well. The NUC already falls back when the home model goes away (IS-4), so switching models is safe to try: while one model unloads, the NUC uses tiers 1–2 and Gemini.

Four things the spec has to face:

1. **ADR 0006 and `AGENTS.md` say no web interface is built.** A new ADR (0008) must supersede that, for GAMEON1 only.
2. **IS-1 and IS-13 allow only the NUC to reach the server.** Bind the console to `127.0.0.1` on GAMEON1. With mirrored networking, a browser on GAMEON1's Windows side should reach it on localhost with no firewall change (still to be verified). Also require a check of the `Host` and `Origin` headers plus a per-session anti-forgery (CSRF) token, so a web page you visit can't change the config through your browser.
3. **Where settings live.**
   - Everything the server reads is model configuration, not agent memory, so it can be owned on GAMEON1: which model, its sampling settings, the routing prompt (today hard-coded as `ROUTE_SYSTEM` in `server/src/autobot_server/route.py`) and the format text.
   - The agents' definitions (`core`'s personality and its tools) are sent by the NUC with each request (IS-21) and stay there.
   - The spec must decide whether the console writes `[server]` in GAMEON1's copy of `config/autobot.toml` or a file of its own.
4. **Downloads.** IS-7 means the server never pulls a model. The console should show the `ollama pull` command and leave running it to you, the way the Google Cloud screen shows `gcloud` commands.

The playground can be a learning tool:

- try a routing request;
- chat with a candidate model;
- do a **tool-call dry run**, showing which tool the model would call and with what arguments, with no tool actually run;
- compare `server-eval` reports side by side.

**Spec or plan?** A spec first (new behaviour, an ADR reversal, amendments to the server spec), then a plan made from it here in cr2, then that plan applied on GAMEON1, like the bring-up plan.

One practical point: GAMEON1 has only `.venv-server`, so `make test`, `make lint` and `make typecheck` can't run there. The plan has to say how its checks run.

## 7. Prompt for your spec-create agent

```text
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
```
