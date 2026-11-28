---
run: schiele/0168
at: 2026-11-28T14:58:24.951Z
mode: answer
agent: answer
model: claude:claude-sonnet-5-5
target: docs/specs/active/autobot-inference-server-console-SPEC.md
template: answer
prompt-sha: 41d41ccf04a6
template-sha: 90976540f104
commit: 4ba85a3
attachments: []
read:
  - AGENTS.md
  - docs/specs/active/autobot-inference-server-console-SPEC.md
outcome: success
usage:
  input: 34061
  output: 17104
  peak: 34061
  window: 1000000
cost: 0
---

---
type: spec
title: autobot — inference server console
authority: prescriptive
status: active
describes:
  - server/src/autobot_server/console/**
  - server/tests/**/test_console*
  - tests/**/test_console*
  - deploy/autobot-console.service
provenance:
  - run: schiele/0159
    at: 2026-11-28T14:12:34.293Z
    mode: create
  - run: schiele/0160
    at: 2026-11-28T14:19:49.755Z
    mode: revise
  - run: schiele/0161
    at: 2026-11-28T14:44:44.270Z
    mode: revise
  - run: schiele/0163
    at: 2026-11-28T14:47:32.285Z
    mode: revise
  - run: schiele/0165
    at: 2026-11-28T14:55:42.759Z
    mode: revise
---

# autobot — inference server console

The **inference server console** is a small web-based admin console on `GAMEON1`, beside the inference server. The owner uses it to configure, try out and control the **home model**, the language model behind routing and chat (today `qwen3:8b` on Ollama), and to learn how its settings behave. It also configures the server's speech-to-text (IC-26). It does for the home model what the wake-word training studio does for the wake word (TS-8). A **candidate profile** is configured and measured while the **installed profile** keeps serving the NUC. It is then installed by the owner, and it can be reverted. Swapping models is safe because the NUC already falls back on its own while `route`, `chat` and `stt` are not ready (IS-4). The console runs on `GAMEON1` only. It listens on loopback only and is used from a browser on `GAMEON1`'s Windows side. It reaches no cloud service, handles no audio, and never touches the NUC. Rules IC-1 to IC-26 govern it. It builds on IS-1, IS-4, IS-5, IS-6, IS-7, IS-8, IS-9, IS-10, IS-11, IS-16, IS-21, IS-24, IS-25, IS-26, EC-11, TS-5, TS-8, `CN-1` as amended on 2026-11-28, `CN-2`, AG-4, AG-11, GC-2, ADR 0002 and ADR 0007 ("Home models beside Gemini"). Cancel and the reflexes belong to the edge control plane spec (`autobot-edge-control-plane-SPEC.md`, EC-1 to EC-19), and this spec adds nothing on the NUC. It needs a new ADR 0008, because ADR 0006 says no web interface is built; that ADR is listed under "Changes needed elsewhere".

*Revised 2026-11-28: the server's speech-to-text settings are now in scope (IC-26), and the Playground's chat and dry run stop through the server's closed-connection cancel (IC-16, EC-11).*

## Terms

- **Edge layer.** The NUC: the wake word, the microphone, the speaker, the face, the rules layer, the agents and all agent state.
- **Home layer.** `GAMEON1`: the inference server, the **home model** it serves through Ollama, and its speech-to-text.
- **Cloud layer.** Gemini, called by the NUC as the service account (IS-20, GC-4).
- **Tier.** Keeps its IS-17 meaning: a step of the routing cascade. It is never a name for a layer.
- **Agent.** Runs a spoken session on the NUC (AG-1).
- **Tool.** A function the home model asks for and the NUC runs (IS-21). The console never runs one.
- **Profile.** One complete setting of the home model and of the server's speech-to-text (IC-8).
- **Candidate profile.** The profile being tried. There is at most one.
- **Installed profile.** The profile the server uses to answer the NUC.
- **Previous profile.** The profile an install replaced, kept so that the install can be reverted.

## Numbered rules, each with its reason

### Host and reach

1. **IC-1. The console runs on `GAMEON1` only.** It refuses to start unless this machine's hostname equals `[server] hostname`, with the one-line message the server gives (IS-25). Every `console-*` target refuses in the same way (IC-22). Reason: the console drives the server, Ollama and the card on this host. On the NUC it would have none of them, and a console that started there would fail later and less clearly.

2. **IC-2. It binds `127.0.0.1` on its own port, `8301`, and nothing else.**
   - The owner opens it from a browser on `GAMEON1`'s Windows side, at `http://127.0.0.1:8301/`. WSL's mirrored networking carries Windows loopback to WSL loopback.
   - The bind address is fixed in the code and is not a setting. The port is `[server] console_port`, and it is never `[server] url`'s port.
   - The console is never reachable from the LAN. It is never put behind the `assistant` project's Cloudflare tunnel or any other proxy, and no firewall rule is added for it, on Windows or in the Hyper-V firewall. The owner does not need it from another machine on the LAN for now.
   - When the port is taken, the console stops with one line naming the port.

   Reason: the console changes how the home model answers the house, so the only person who should reach it is the owner sitting at the PC. A loopback bind needs no firewall rule, and making the address a setting would let a typo put it on the LAN. Reaching it from the LAN would need its own decision, authentication over the network, TLS and a firewall rule. `INFRASTRUCTURE.md` records mirrored mode and the Hyper-V firewall's `LoopbackEnabled: True`. Port `8301` sits next to the server's `8300` and avoids the `assistant` project's 8099, 8123 and 8199.

3. **IC-3. Every request passes a Host and Origin check, and every change carries a per-session token.**
   - **Host.** A request whose `Host` header is not `127.0.0.1:<port>` or `localhost:<port>` is answered `403`.
   - **Origin.** A request that changes anything (any method but `GET`) whose `Origin` is not one of those two is answered `403`.
   - **Session.** The first `GET` of the page starts a session. The session has an `HttpOnly`, `SameSite=Strict` cookie and a random CSRF token embedded in the page. Every change must carry that token in a request header. Sessions are held in memory and end when the console stops.
   - **Nothing from outside.** The console never sends a CORS header. Its pages carry a `Content-Security-Policy` of `default-src 'self'`, and they load no script, font or style from outside the console.
   - A `GET` changes nothing.

   Reason: any web page open in the same Windows browser can send requests to `127.0.0.1`. The Host check defeats DNS rebinding, and the Origin check with the token defeats cross-site requests. Loading nothing from outside keeps the page itself from reaching the internet (IC-21).

4. **IC-4. The console has no login of its own, and the server's bearer token never reaches the browser.** The console process reads the server's token file, `~/.config/autobot-server/token`, to call the server on loopback, as `make server-check` does. It never sends the token to the browser, never shows it and never logs it. The owner is not asked to type the `8300` token to use the console. Reason: IC-2 already limits the console to someone at the PC, and IC-3 covers what loopback alone does not. A second password would only make the owner copy the token by hand, and every copy is another place it can leak (IS-13).

### Process

5. **IC-5. The console is a separate process from the server, and the server never depends on it.**
   - The console reads the server's state from `GET /health` on loopback.
   - It changes the server's state only through the server's admin path (IC-6), or through `systemctl --user` on `autobot-server.service` when the server must be restarted.
   - A console that is stopped, crashed or never installed changes nothing the server does.

   Reason: a fault in the console must never take port 8300 down. The NUC relies on that port whenever the PC is up (IS-14), and the console is a tool for the owner.

6. **IC-6. The server gains a loopback-only admin path, outside `/v1`.**
   - **Who may call it.** The path answers only a peer on a loopback address that presents the server's token. Any other caller is answered `403`, and the NUC never calls it.
   - **Operations.** Three:
     - *hold*: `route` and `chat` report not ready, with the reason "held by the console", and send nothing to Ollama;
     - *release*: ends a hold;
     - *reload*: reads the installed profile again and warms `route` and `chat` again. It loads and warms `stt` again only when the profile's speech-to-text model or compute type differs from the one loaded (IC-26). Otherwise `stt` stays loaded, and its other settings apply from the next request.
   - **The lease.** A hold is a lease that the console renews. If it is not renewed within 10 minutes it lapses by itself.
   - **Logging.** Each admin call leaves the same one log line as any other request (IS-5).

   Reason: installing a profile must not reload the speech model unless the profile changes it, because loading it takes most of the server's two-minute warm-up. While `stt` reloads it reports not ready, and the NUC transcribes with its own `tiny.en` (IS-4). A candidate that displaces the installed model (IC-17) must not fight the server's own requests for the card. The NUC already falls back cleanly while a job is not ready (IS-4), so a hold is the safe way to borrow the card. The lease means a console that dies mid-hold cannot leave the NUC without its home model. Keeping the path outside `/v1` leaves the NUC's contract unchanged (IS-22). This is the second loopback caller IS-1 must allow.

7. **IC-7. It is built from the standard library, as the server is.**
   - It is an `http.server` server with no web framework.
   - Its pages are static HTML with a small amount of JavaScript and no build step.
   - It lives in the `autobot_server` package and reuses the server's own modules for routing prompts, Ollama requests, configuration and evaluation, so that what the console tries is exactly what the server would send.
   - It does not import `autobot`.

   Reason: `GAMEON1` has only `.venv-server` and must not install the NUC's audio and cloud dependencies (IS-12). The same code path means a result in the Playground or the Evaluation screen holds for the server. The pages stay a front, as the terminal user interface is on the NUC (TS-10).

### Profiles

8. **IC-8. A profile is the home model's whole setting.** A profile has a name and holds:
   - the **model tag**, as Ollama names it (`qwen3:8b`);
   - the **context length**, which routing and chat share;
   - **for routing and for chat separately**: temperature, `top_p`, `top_k`, repeat penalty, maximum tokens, and whether thinking is on;
   - the **route prompt**: the server's routing system prompt, today `ROUTE_SYSTEM` in `route.py`;
   - the **chat format text**: the text the NUC puts after the core agent's personality, today `FORMAT` and `HANDOFF_FORMAT` in `src/autobot/agents/core.py`;
   - the **speech-to-text settings** of IC-26: the model, the compute type, the beam size, the VAD filter and its settings, the hotwords wording and the initial prompt.

   `keep_alive`, the request's JSON schema, its tools and `stream` are not part of a profile. They are fixed by IS-24 and the contract, and the Parameters screen shows them read-only (IC-13). Reason: routing and chat share one resident model (IS-10), so the model tag and the context, which Ollama loads with, must be shared. How the model samples is chosen per request, and a classification and a conversation want different settings. Keeping the prompts in the profile lets a prompt change be measured and reverted like any other setting. The speech-to-text settings share the card with the home model, so keeping them in the same profile lets one install and one revert cover everything the server does.

9. **IC-9. A profile is installed only when the owner confirms it, and the install can be reverted.**
   - **Profiles and the candidate.** The Profiles screen lists every profile and marks the installed one, the previous one and the candidate. The owner makes a profile by copying an existing one. With none yet, the first profile copies the server's current settings: `[server] llm_model` and `llm_context`, `ROUTE_SYSTEM`, `FORMAT` with `HANDOFF_FORMAT`, Ollama's own defaults for the model, `[server] stt_model` and `stt_compute_type`, and the speech-to-text values the server uses today (IC-26).
   - **Install.** Installing names the candidate as the installed profile, keeps the one it replaces as the previous profile, and reloads the server (IC-6).
   - **Revert.** Reverting swaps the installed and previous profiles back, with the same confirmation and reload.
   - **The confirmation.** It shows the candidate's evaluation beside the installed profile's (IC-18). It says so plainly when the candidate has no report, when its report fails IS-26's criterion, or when its report has no speech-to-text measurement. It also says when the install reloads the speech model, because the NUC then transcribes with `tiny.en` until `stt` is ready again. The install is still the owner's choice.
   - **No profile installed.** The server answers exactly as it does today, from `[server] llm_model`, `llm_context`, `stt_model` and `stt_compute_type`, with its built-in speech-to-text values.

   Reason: a setting the house hears every day should be judged by the owner and should be cheap to take back. A revert that needs no remeasurement is what makes trying a candidate cheap (TS-8). A checkout with no profiles must keep working unchanged.

10. **IC-10. Profiles and their state live in `GAMEON1`'s checkout of `config/autobot.toml`, in keys only the server reads.**
    - The profiles are `[[server.profiles]]` tables.
    - `[server] profile` names the installed one, `[server] previous_profile` the previous one and `[server] candidate_profile` the candidate.
    - The NUC's reader knows these keys and ignores them, as the server already ignores the NUC's keys.
    - The console writes them as the terminal user interface writes the NUC's settings: back into the one file, with its comments kept.

    Reason: `CONVENTIONS.md` keeps all configuration in that one file, and the server already reads its settings there (IS-3). The two checkouts will drift on these keys, but only the server reads them, as it alone reads `llm_model` today, so the drift changes nothing the NUC does. The cost is a local edit to a tracked file on `GAMEON1`, which a `git pull` must merge, as the NUC's checkout already must. A file of the server's own was considered and not chosen.

11. **IC-11. A profile that would break the NUC or the card cannot be installed.** The console refuses to save or install a profile when:
    - its context is below `[server] llm_context`, because the NUC trims conversations to that budget (IS-19) and a smaller context would silently cut off the start of the prompt;
    - its context, at the Ollama service's current cache type, needs more than IS-10's 1.5 GB KV-cache budget;
    - its chat maximum tokens is above 1024, the reply margin the NUC keeps free of the context;
    - its route prompt does not contain `{agents}` exactly once, or contains any other brace.

    Any other value outside IC-13's safe range is allowed. It is marked in the editor and in the install confirmation. IC-26 adds the speech-to-text refusals. Reason: these four limits are where a setting stops being a matter of taste and starts dropping prompts, overrunning the card or failing to render. Inside those limits, the owner is learning, and the safe ranges inform him rather than forbid.

### Screens

12. **IC-12. Status and Models.**
    - **Status** shows:
      - each job's readiness and reason from `/health` (IS-11);
      - the models Ollama has loaded, and how much VRAM each uses;
      - the GPU memory used and free, against IS-10's budget, part by part;
      - which profiles are installed, previous and candidate, and whether a hold is in force (IC-6).
    - **Models** lists the models Ollama holds, with their size on disk, family, parameter count and quantisation. It says whether each fits the budget at the candidate profile's context and cache type.
    - **Getting models.** For a model Ollama does not have, the Models screen can pull it. The owner asks for a tag, and the console asks Ollama to pull it at once, with no confirmation first. Ollama reports no size until a pull has started, so the console shows the size at the top of the progress, with the tag, as soon as Ollama reports it. Nothing is pulled unless the owner asks for that tag. Ollama does the download, the console shows its progress, and the owner can cancel it.
    - **Removing models.** The console shows the `ollama rm <tag>` command and never runs it.

    Reason: the owner needs to see what the card is doing before he changes it. The server never pulls a model (IS-10). A pull is a multi-gigabyte download, so it happens only as the owner's deliberate act, with its size put first as soon as it is known. The owner accepts large downloads, so no confirmation is asked before it starts. A command that removes a model from the host is shown for the owner to run, as the Google Cloud screen does (GC-2).

13. **IC-13. Parameters: every setting explained, with its safe range and a recommended value for `qwen3:8b` on the 2080 Ti.**
    - The screen shows each setting of IC-8 in plain words. It gives the setting's safe range and its recommended value, separately for routing and for chat, and says when a change takes effect. It also shows the model's own default as Ollama reports it, which is what applies today, since the server sends no sampling options.
    - The recommended values are starting points, to be confirmed with `make server-eval` (IC-18).
    - **When a change takes effect:**
      - **Per request**: on the next request after an install.
      - **Model load**: Ollama loads the model again, which takes seconds; meanwhile the NUC falls back.
      - **Ollama service**: the owner restarts Ollama himself (IC-14).

    | Setting | What it does, in plain words | Safe range | Routing | Chat | Takes effect |
    |||||
    | Model tag | Which model Ollama runs. One model serves both jobs (IS-10). | a model Ollama holds that fits the budget | `qwen3:8b` (shared) | (shared) | model load |
    | Context length (`num_ctx`) | How much text the model can see at once: the system prompt, the agent list, the conversation and the reply. More costs VRAM: about 144 KiB a token for `qwen3:8b` at `f16`, so 8,192 tokens is about 1.1 GiB. | from `[server] llm_context` up to what fits 1.5 GB: about 10,240 at `f16`, about twice that at `q8_0` | 8192 (shared) | (shared) | model load |
    | Temperature | How adventurous each next word is. At 0 the model always takes the likeliest word and gives the same answer every time. Higher values give more variety and more mistakes. | 0 to 1.0 | 0 (0 to 0.3): routing is a classification, and the same transcript should route the same way every time | 0.7 (0.3 to 1.0): Qwen's own value for non-thinking chat | per request |
    | `top_p` | Considers only the likeliest words whose chances add up to this share. Lower is safer and plainer. No effect at temperature 0. | 0.5 to 1.0 | 0.8 | 0.8 | per request |
    | `top_k` | Considers only this many of the likeliest words. No effect at temperature 0. | 10 to 40 | 20 | 20 | per request |
    | Repeat penalty | Makes words already used less likely. 1.0 is off. | 1.0 to 1.15 | 1.0: a route must repeat agent and mode names from its prompt, and a penalty pushes the model away from them | 1.0; raise toward 1.1 only if replies loop | per request |
    | Maximum tokens (`num_predict`) | The longest the answer may be. The model stops there, even mid-sentence. | routing 64 to 256; chat 256 to 1024 | 128: a route answer is a short JSON object | 512 | per request |
    | Thinking (`think`) | Qwen3 can reason privately before it answers. Those tokens come first and are never spoken, so they delay the answer by seconds. | on or off | off: routing has 1.5 s (IS-4) | off: the first spoken token has 2.0 s (IS-4) | per request |
    | `keep_alive` | How long Ollama keeps the model loaded after a request. | fixed at `-1`, forever (IS-24) | read-only | read-only | — |
    | KV cache type (`OLLAMA_KV_CACHE_TYPE`) | The precision of the model's working memory of the context. `f16` is full; `q8_0` uses half the memory, usually with little loss; `q4_0` uses a quarter, with noticeable loss. It needs `OLLAMA_FLASH_ATTENTION=1`, which is untried on Turing. | `f16` or `q8_0` | `f16` (service-wide) | (service-wide) | Ollama service |
    | `OLLAMA_NUM_PARALLEL` | How many requests Ollama runs at once. | fixed at 1 (IS-10) | read-only | read-only | Ollama service |

    The request's JSON schema (`format`, IS-9), its tools (IS-21) and `stream` are shown read-only, as the contract fixes them. Reason: the owner is configuring a language model for the first time. A setting he cannot read in plain words, with a range and a value to start from, is one he cannot choose. Two facts follow from how Ollama runs the one resident model: anything it loads with must be shared by both jobs, and anything it samples with can differ per job. The values come from Qwen3's published recommendations for non-thinking use, `qwen3:8b`'s architecture (36 layers, 8 KV heads of 128), and the budget and measurements in `INFRASTRUCTURE.md`.

14. **IC-14. Ollama's service settings are the owner's to change.** The console reads the settings of `ollama.service` (`systemctl show ollama.service`) and shows them. It shows the commands that change them (`sudo systemctl edit ollama.service`, then `sudo systemctl restart ollama.service`) and never runs them. Reason: Ollama is a system unit, installed and configured by the owner with `sudo` (IS-14). The console runs as the owner's user and has no such rights. A changed service setting affects every model Ollama serves, not only autobot's.

15. **IC-15. Prompts: the route prompt and the format text are edited, and the NUC's whole prompt is shown read-only.**
    - **Editing.** The screen edits the candidate profile's route prompt and chat format text.
    - **Route preview.** It shows the route prompt rendered with the agent list the NUC would send: the agents and routes of the checkout's `config/autobot.toml`, built as `autobot.cascade` builds them.
    - **Core prompt.** It shows read-only the complete system prompt the NUC would send for the `core` agent: its personality, then the format text, then the handoff list, rendered from the checkout's `config/autobot.toml` as `system_prompt` renders it. It also shows the core agent's tools as `tools_of` renders them.
    - **The format text does not reach the NUC.** A profile's format text is what the console's Playground and Evaluation use. Installing a profile does not change what the NUC sends: that comes from the NUC's own code. The next spec moves the format text into the core agent's definition in `config/autobot.toml`, edited in the NUC's Agent editor, and the console then shows the difference between the two.

    Reason: the owner cannot tune a prompt he cannot see whole. The route prompt is the server's own, so the server can use the installed one. The format text is part of the agent's answer shape, which the agent builds on the NUC (AG-4, IS-21), and changing what the NUC sends is outside this spec.

16. **IC-16. Playground: a routing request, a chat, and a tool-call dry run.**
    - **Routing.** A typed transcript is routed with the checkout's agent list. The console shows the agent, the mode, the confidence, the parameters and the time taken.
    - **Chat.** A typed conversation with the core agent's rendered prompt. The console streams the answer and shows the time to the first token, the summary and the detail as AG-4 takes them apart, and the tokens used.
    - **Tool-call dry run.** The core agent's tools from the checkout, or a tool schema the owner types in, go with a typed request. The console shows the tool calls the model asks for, with their arguments, and stops. It runs no tool and sends no result back.
    - **Which profile.** The installed profile is tried through the server on `8300`, so the result includes the server's own path. A candidate is tried against Ollama directly, through the server's own request code (IC-7).
    - **Stopping.** The chat and the dry run each have a Stop. Stop, leaving the screen, or the browser closing its connection to the console all close the console's own connection for that request: to the server on `8300` for the installed profile, or to Ollama for a candidate. This is the cancel the server exposes (EC-11): for a streamed chat it stops the generation and frees the job slot within 0.5 s, and no cancel endpoint is called or added. Nothing of a stopped answer is added to the page's conversation. A routing request does not stream, so the server finishes it within its timeout and the console discards the answer.
    - **Shared with the NUC.** While the console generates, a request from the NUC may wait or time out and fall back (IS-4, IS-24). The Playground says so.

    Reason: the owner learns what a setting does by trying it on his own words. A dry run shows what a model would ask the NUC to do, without doing it. Tools belong to the NUC (IS-21). A trial the owner has stopped must free the card at once, because IS-24 gives a busy job one waiting request and then answers the NUC `503`. A closed connection is the one cancel the stateless server has (IS-5, EC-11).

17. **IC-17. A candidate that displaces the installed model holds the server first.** A candidate displaces the installed model when its model tag or context differs from the installed profile's, because Ollama must load it in the installed model's place. Before it runs such a candidate in the Playground or the Evaluation, the console says that the NUC will fall back while it runs, and asks the owner to confirm. Once confirmed, it holds the server (IC-6). It releases the hold when the run ends, or when the owner leaves the screen. Reason: the card holds one language model beside the speech model (IS-10). A hold lets the NUC fall back cleanly, instead of timing out on a server that is loading models back and forth.

18. **IC-18. Evaluation: `make server-eval` against a profile, and reports side by side.**
    - **Running it.** The screen runs the evaluation of IS-26 against the candidate profile, or any other, as a subprocess, one run at a time, and shows its output as it comes.
    - **What it measures.** Routing and chat, from typed and committed utterances, and speech-to-text, from the committed WAV fixtures in `tests/fixtures/audio/`. Those fixtures are synthesised and are not the owner's voice (TS-5). The speech-to-text measurement transcribes each fixture with the profile's speech-to-text settings (IC-26) and compares the result with the text the fixture was made from.
    - **The report.** Each run writes `run/server-eval-<profile>.json`, with the profile's full settings in it, so that a report says what it measured.
    - **Side by side.** The screen sets reports next to each other:
      - right agent;
      - right agent, mode and parameters;
      - JSON validity for routing and for chat;
      - routing latency, median, p95 and maximum, against 1.5 s;
      - chat's first token against 2.0 s;
      - speech-to-text word error rate over the fixtures, and the time each transcription took.
    - **The criterion.** IS-26's criterion is shown in its own words. The screen marks which report it favours: of those within the latency budget, the best on right agent, mode and parameters, then on JSON validity. The speech-to-text figures are shown beside it for the owner to weigh, and are not part of that criterion.

    Reason: IS-26 is how the home model is chosen, and the console is where the owner chooses it. A report that names its profile cannot be mistaken for another. The utterances and fixtures are committed, so no owner audio is involved (TS-5). Speech-to-text settings are installed with the rest of the profile (IC-8), so they are measured with it; until measured, a speech-to-text change is a guess.

### State and boundaries

19. **IC-19. The server stays stateless, and the console keeps no conversation.**
    - **Playground conversations** live in the browser page's memory only. The console process passes them through and never logs or writes them.
    - **The log.** The console writes one line per request to `run/console.log`: method, path, status and duration, and nothing of a prompt, a transcript or an answer.
    - **What the console writes.** It writes only `config/autobot.toml` (IC-10) and the evaluation reports under `run/` (IC-18).

    Reason: IS-5 keeps the server a stateless function of its requests. A conversation typed into the console is still the owner's words, and AG-11 keeps conversations in memory only.

20. **IC-20. The console configures the home layer only.**
    - The agents' definitions, their routes, their tools and every conversation stay on the NUC (IS-21, ADR 0002). The console reads the checkout's `config/autobot.toml` to render what the NUC would send, and edits only the keys of IC-10.
    - It never connects to the NUC.
    - It never edits `[[agents]]`, `[[routes]]`, `[[reflexes]]` or `[words]`.

    Reason: the NUC is the one place autobot's behaviour as an assistant is configured, through its terminal user interface. A second editor of agents on another host would drift from it.

21. **IC-21. No cloud, no credential, no audio.**
    - The console opens connections only to loopback: the server, and Ollama at `[server] runtime_url`.
    - It holds no Google Cloud credential and calls no Gemini model.
    - It downloads nothing itself. The one download it starts is the owner-requested pull of IC-12, which Ollama carries out. Otherwise `make server-fetch-models` stays the downloader on this host (IS-7, IS-25).
    - It accepts, sends and stores no audio, and has no speech-to-text or text-to-speech playground. Its Speech-to-text screen (IC-26) edits settings only. The speech-to-text evaluation of IC-18 is a subprocess that reads the committed fixtures, and the console shows only its text results.

    Reason: IS-7 keeps this host off every cloud service, and ADR 0007 keeps the one credential on the NUC. `CN-1`, `CN-2`, TS-5 and IS-6 keep the owner's voice off every path that does not need it, and the console needs none. A requested pull is the owner's choice, and it goes through Ollama on loopback, so the console itself still opens no outside connection.

### Runtime control and checks

22. **IC-22. Runtime control is `make`.** Each target runs on `GAMEON1` only (IC-1) and appears in the bare `make` summary:

    | Target | What it does |
    |---|---|
    | `console-up` | Starts the console: the systemd unit when it is installed, otherwise in the background with its pid in `run/console.pid`. |
    | `console-down` | Stops the console. |
    | `console-status` | Shows whether it runs, the address to open, the installed and candidate profiles, and whether a hold is in force. |
    | `console-logs` | Prints the end of `run/console.log`; `FOLLOW=1` keeps following it. |
    | `console-install` | Renders `deploy/autobot-console.service` into `~/.config/systemd/user/autobot-console.service`, enables it, makes sure lingering is on, and starts it. |

    Reason: `AGENTS.md` makes the `Makefile` the runtime control layer, with every target in the bare summary. These mirror the `server-*` targets, so the two programs on this host are run the same way.

23. **IC-23. The console's tests need no GPU, no model, no browser and no network.**
    - The console's dispatch is one pure function of method, path, headers and body, as the server's `App.handle` is (IS-12).
    - The tests drive it with fakes for Ollama, the server's `/health` and admin path, `systemctl`, the hostname, the clock and a configuration file in a temporary directory.
    - The JavaScript holds the page's conversation and draws what the dispatch answers, with no logic of its own beyond that, so the dispatch tests cover the behaviour.

    Reason: IS-12's reasoning holds as it stands. These tests run on both hosts, and the NUC has neither a GPU nor Ollama.

24. **IC-24. The console's tests run on both hosts.**
    - **The tests.** They live under `server/tests/`, so `make test` on the NUC runs them with the server's.
    - **The rendering test.** A test under `tests/`, run only on the NUC where both packages are installed, checks that the console's rendering of the core agent's system prompt, its tools and the route agent list equals what `autobot.agents.core` and `autobot.cascade` render from the shipped `config/autobot.toml`.
    - **Checks on `GAMEON1`.** Only `.venv-server` exists there, so the server package gains a `dev` extra (`pytest`, `ruff`, `mypy`) that `make server-setup` installs. `make server-test`, `make server-lint` and `make server-typecheck` run the three checks over `server/` there.
    - **Reporting.** Work applied on `GAMEON1` is reported once those three checks and the conformance check pass there. `make test`, `make lint` and `make typecheck` on the NUC cover the whole tree after a pull.

    Reason: the console reimplements two renderings it cannot import on `GAMEON1`, and a test that compares them against the NUC's code is what keeps them from drifting. That is the pattern the two configuration readers already follow. A check that cannot run on the host where the work is done would be skipped there.

25. **IC-25. A checkout with the console and no profile changes nothing.** Until a profile is installed, the server, the NUC and every existing target behave as they do today. Installing the console adds a loopback port and a user unit, and nothing else. Reason: the home model is serving the NUC now (`INFRASTRUCTURE.md`), and adopting the console must not be the change that breaks it.

### Speech-to-text

26. **IC-26. Speech-to-text: the server's transcription settings, each explained, with its safe range and a recommended value.**
    - **What it edits.** The screen edits the speech-to-text part of the candidate profile (IC-8): the settings the server's `stt` job transcribes `/v1/stt` with (IS-8, IS-16). They are installed and reverted with the rest of the profile (IC-9).
    - **How it explains.** As the Parameters screen does (IC-13), it shows each setting in plain words, with its safe range, its recommended value, the value the server uses today, and when a change takes effect.
    - **When a change takes effect:**
      - **Per request**: on the next `/v1/stt` request after an install.
      - **Speech reload**: the server loads and warms the speech model again (IC-6). Meanwhile `stt` reports not ready and the NUC transcribes with its own `tiny.en` (IS-4).

    | Setting | What it does, in plain words | Safe range | Recommended | Takes effect |
    |---|---|---|---|---|
    | Model (`[server] stt_model`) | Which faster-whisper model transcribes. A larger model hears unclear words better, but is slower and uses more of the card. | a directory under `models/` that holds `model.bin`, `config.json` and `tokenizer.json`, put there by `make server-fetch-models` | `models/faster-whisper/large-v3-turbo` | speech reload |
    | Compute type (`[server] stt_compute_type`) | The precision the model runs in on the card. `int8_float16` uses less memory with little loss; `float16` is full precision. | `int8_float16` or `float16` (IS-10) | `int8_float16` | speech reload |
    | Beam size | How many guesses at the sentence the model keeps open while it decodes. At 1 it takes its first guess. More guesses catch unclear words better, and take longer. | 1 to 5 | 5 | per request |
    | VAD filter | The model's own voice-activity filter, which cuts silence out before transcribing. The NUC's VAD has already cut the utterance (IS-8), so a second cut can clip a first or last word. | on or off | off | per request |
    | VAD speech threshold | Used only with the filter on. How sure the filter must be that a moment is speech before it keeps it. | 0.3 to 0.7 | 0.5 | per request |
    | VAD minimum silence | Used only with the filter on. How long a pause must be before the filter cuts it out. | 500 to 2000 ms | 2000 ms | per request |
    | Hotwords wording | How the hints the NUC sends with each utterance, autobot's own words (IS-16), are put to the model. It tells the model how those words are spelled without forcing them onto other speech. The hints themselves come from the NUC and are not edited here. | contains `{hints}` exactly once and no other brace | `Glossary: {hints}.` | per request |
    | Initial prompt | Text the model treats as if it had been said just before the utterance. It steers spelling, punctuation and style. A long or unusual prompt can make the model repeat it on near-silent audio. It shares the model's prompt of at most 224 tokens with the hotwords. | empty, or a sentence or two | empty | per request |
    | Language | The language the model transcribes. A short command is too little audio for the model to tell its language reliably. | fixed at `en` | read-only | — |
    | Temperature | One deterministic pass, bounded in time. | fixed at 0 | read-only | — |

    - **Refusals.** The console refuses to save or install a profile whose speech model directory lacks one of its three files, whose compute type is not one of the two, whose beam size is below 1, or whose hotwords wording does not contain `{hints}` exactly once or contains any other brace. Any other value outside the safe range is allowed, and is marked in the editor and in the install confirmation, as IC-11 does.
    - **Measuring.** A speech-to-text setting is measured by the evaluation of IC-18, from the committed fixtures. The install confirmation says so plainly when the candidate's report has no speech-to-text measurement (IC-9).
    - **No audio here.** The screen shows and edits settings only. It records, accepts and plays no audio, and transcribes nothing (IC-21). Audio capture and the NUC's listener are not configured here.
    - **The NUC is unaffected.** The NUC never names the server's model (IS-8). It keeps sending one whole utterance after a wake with its hints, and falls back to `tiny.en` when the server is away (IS-4, EC-14).

    Reason: server speech-to-text is permitted by `CN-1` item 3 and ADR 0007, and how the server transcribes is a setting of the home layer like the home model's. The owner cannot choose a setting he cannot read in plain words. The refusals are the values that would leave `stt` not ready or fail to render the hints. The recommended values are what the server uses today: `large-v3-turbo` at `int8_float16` (IS-10), beam 5, the filter off because the NUC already cut the utterance, and the glossary wording that tells the decoder how words are spelled. The VAD values are faster-whisper's own defaults. Handling no audio keeps the owner's voice off the console (`CN-1`, TS-5, IS-6).

## Not in this spec

- **Moving agents to tools.** Moving `weather`, `time` and `camera` from keyword agents to tools of the core agent, and demoting the keyword rules to offline reflexes, is the next spec. Removing `[[routes]]` today would also remove the agents' modes from tier 3, because `autobot.cascade` takes the modes from the routes (`agent_modes`).
- **Cancel and the reflexes.** How the NUC cancels, the reflexes and barge-in are the edge control plane spec's (`autobot-edge-control-plane-SPEC.md`, EC-1 to EC-19). The console only uses the server's side of cancel, the closed connection of EC-11 (IC-16).
- **Text-to-speech.** Any change to text-to-speech.
- **Audio capture.** The microphone, the echo canceller, the VAD that cuts utterances on the NUC and the NUC's listener.
- **The NUC.** Anything on the NUC beyond reading the checkout's configuration to render what the NUC would send. That includes how the format text of IC-15 would reach the NUC.

## Settled without asking

- **Derived (EC-11, IS-5).** The Playground stops a chat or dry run by closing its connection, since the server has no cancel endpoint, and a non-streamed route request finishes and is discarded (IC-16).
- **Derived (IS-8, IS-4).** The NUC never names the server's speech model, so changing it from the console needs nothing on the NUC, and a speech reload is covered by the NUC's fallback to `tiny.en` (IC-26).
- **Derived (IS-10).** The compute types offered are the two the server accepts, `int8_float16` and `float16` (IC-26).
- **Derived (IS-7, IC-21).** The console never downloads a speech model. A model directory comes from `make server-fetch-models` (IC-26).
- **Resolved.** IC-21 said the console handles no audio. The Speech-to-text screen edits settings only, so that stays true, and IC-21 now names the screen.
- **Resolved.** IC-6 said a reload always leaves `stt` loaded. It now reloads `stt` only when a profile changes the speech model or compute type, which keeps IC-6's reason for not reloading it otherwise.
- **Resolved.** ADR 0007 is cited as the owner states it is written. It was not yet present in `docs/decisions/` when this revision was made, and ADR 0008 still follows it in number.
- **Resolved.** Speech-to-text settings are measured by adding a speech-to-text evaluation to `make server-eval`, run from the committed synthesised WAV fixtures in `tests/fixtures/audio/` and not from the owner's voice (IC-18, IC-26).
- **Default.** The speech-to-text settings are part of the profile, so they are installed and reverted with the home model's (IC-8, IC-9). This reuses the candidate, install and revert pattern rather than adding a second one.
- **Default.** The recommended speech-to-text values are what the server uses today, and faster-whisper's own defaults for the VAD settings (IC-26).
- **Default.** Language and temperature are shown read-only, as `keep_alive` is on the Parameters screen (IC-26).

## Changes needed elsewhere

- **docs/decisions/0008-web-console-on-gameon1-ADR.md (new).** Record a web console on `GAMEON1`, loopback-only, for the home model and the server's speech-to-text settings. It follows ADR 0007 and supersedes ADR 0006's "no web interface is built" for that host only. The NUC's application is still the terminal user interface, and no web interface is built on the NUC.
- **docs/specs/active/autobot-gpu-inference-server-SPEC.md.**
  - IS-1: allow a second loopback caller, the console, and the admin path of IC-6.
  - IS-7: name the console as reaching no cloud service itself, and as pulling a model only through Ollama when the owner asks, showing the size as soon as Ollama reports it (IC-12, IC-21).
  - IS-9 and IS-10: the route prompt, the sampling options per job and the model tag come from the installed profile; without one, from `[server] llm_model`, `llm_context` and `ROUTE_SYSTEM`.
  - IS-8, IS-10 and IS-16: the speech model, compute type, beam size, VAD filter and its settings, hotwords wording and initial prompt come from the installed profile; without one, from `[server] stt_model`, `stt_compute_type` and the server's built-in values (IC-26). A reload reloads `stt` only when the model or compute type changes (IC-6).
  - IS-11: `/health` reports the installed profile, its context and any hold.
  - IS-25: add the `console-*` targets and `server-test`, `server-lint`, `server-typecheck`.
  - IS-26: the evaluation runs against a profile, writes `run/server-eval-<profile>.json`, and the winner is installed from the console rather than written to `llm_model` by hand. It also measures speech-to-text from the committed WAV fixtures in `tests/fixtures/audio/`, reporting word error rate and transcription time, outside the winner criterion (IC-18).
  - Its "Changes needed elsewhere" entry for `CONSTRAINTS.md` is done for `CN-1` (amended 2026-11-28). `CN-2` is unchanged, so text-to-speech on `GAMEON1` stays open.
- **AGENTS.md.** Describe the console, its targets and its tests. Say how checks run and are reported on `GAMEON1` (IC-24). An agent never reads the server token file, never runs a command the console shows (`ollama rm`, `systemctl edit ollama.service`), never pulls a model through the console or otherwise, never binds the console off loopback, never adds a firewall rule for it, and never writes the owner's playground text anywhere. Pulling a model is the owner's own act.
- **docs/context/CONVENTIONS.md.** "Control layers": no web interface is built on the NUC, and the one web interface is the console on `GAMEON1` (ADR 0008). "Layout": the `[[server.profiles]]` keys are read on `GAMEON1` only. The pending "one package per host" change still stands.
- **docs/context/ARCH.md.** Add the console as a part on `GAMEON1`, with its seams: `/health` and the admin path to the server, loopback to Ollama, and a read of the checkout's configuration.
- **docs/context/SCHEMA.md.** Add `[[server.profiles]]` and its fields (IC-8), including the speech-to-text fields of IC-26, `[server] profile`, `previous_profile`, `candidate_profile` and `console_port`, and the profile settings written into an evaluation report, with its speech-to-text measurement (IC-18).
- **docs/context/GLOSSARY.md.** Add "edge layer", "home layer", "home model", "cloud layer", "console", "profile", "candidate profile" and "installed profile". Restate "tier" as the IS-17 routing tier only. Split "agent" (runs a spoken session on the NUC) from "tool" (a function the home model asks for and the NUC runs), and reconcile the existing "agent tool" entry. Update "STT", "TTS" and "faster-whisper", which still say speech-to-text is always on the local machine.
- **docs/context/INFRASTRUCTURE.md.** Under "The server as it runs on `GAMEON1`", record the console's unit, port, loopback bind and log, and that it is reached from Windows over mirrored networking with no firewall rule.

## Open Questions

None.
