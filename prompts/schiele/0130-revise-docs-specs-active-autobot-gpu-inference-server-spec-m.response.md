---
run: schiele/0130
at: 2026-11-27T21:15:13.494Z
mode: revise
agent: revise
model: claude:claude-opus-5-5
target: docs/specs/active/autobot-gpu-inference-server-SPEC.md
template: revise
prompt-sha: 59206cadd1da
template-sha: 80089d1722f6
commit: a38a0fd+
attachments: []
read:
  - AGENTS.md
  - docs/specs/active/autobot-gpu-inference-server-SPEC.md
  - docs/specs/active/autobot-agents-and-the-agent-tree-SPEC.md
  - docs/specs/active/agent-sessions-SPEC.md
  - docs/specs/active/rules-first-routing-SPEC.md
outcome: success
usage:
  input: 90089
  output: 18375
  peak: 39244
  window: 1000000
cost: 0
---

---
type: spec
title: "autobot — GPU inference server"
authority: prescriptive
status: active
describes:
  - config/autobot.toml
  - docs/context/INFRASTRUCTURE.md
provenance:
  - written: schiele/gpu-inference-server-host-on-wsl
    at: 2026-11-27T20:49:06Z
    mode: create
  - written: schiele/gpu-inference-server-host-on-wsl
    at: 2026-11-27T21:30:00Z
    mode: revise
---

# autobot — GPU inference server

The **inference server** is a second program of autobot. It runs on `GAMEON1`, the owner's Windows PC with an RTX 2080 Ti, inside its Ubuntu WSL2 distribution, and serves the NUC over the LAN. In the owner's words, it is "a robust, fast and fun local model layer" that serves the NUC, hands hard requests to the upstream Gemini ADK agents, and is programmable and extensible in its own right, so that new agents and tools are added without rewriting the server. It does three jobs that the NUC's CPU does slowly or not at all:

- **speech-to-text**, with a larger Whisper model than `tiny.en`;
- **routing**, the last tier of the rules layer's cascade, which fills an agent's parameters as well;
- **chat**, a local language model behind the core conversational agent.

**Text-to-speech** is reserved for a later revision. The NUC stays the front end: the wake word, the microphone, the speaker, the face, the rules layer, the agents and all agent state stay there, and every one of them keeps working when the server is away. The server is a stateless function of its requests: it keeps no session, no memory and no audio. Rules IS-1 to IS-27 govern it. The host itself is described in `docs/context/INFRASTRUCTURE.md`. The spec builds on `CN-1`, `CN-2`, TS-5, TS-11, RR-1, RR-3, AS-1, AS-2, AS-5, AG-1, AG-4, AG-11, AG-13, AG-14, FS-15, GC-4 and GC-10. It depends on amendments to `CN-1` and `CN-2`, RR-1, AS-1 and `CONVENTIONS.md`, listed under "Changes needed elsewhere". IS-8 takes effect once `CONSTRAINTS.md` carries the amendment to `CN-1`.

*Revised 2026-11-27: the owner's decisions recorded, and the status set to active. IS-1, IS-4, IS-8, IS-9, IS-10 and IS-12 are amended in place, the open choices of IS-2, IS-3, IS-7, IS-11, IS-13 and IS-14 are settled, and IS-15 to IS-27 are added.*

## Numbered rules, each with its reason

1. **IS-1. The server runs on `GAMEON1`, in WSL, and nowhere else; the NUC is its only remote client.** It runs inside the `Ubuntu-24.04` distribution on `GAMEON1` and uses the GPU through the WSL driver.
   - It serves the NUC and no other machine.
   - `GAMEON1` may call its own server on loopback, and only for `make server-check` (IS-25).
   - The NUC's front-end paths (the listener, the studio, the face, `make audio-check`) never run on this host: TS-11's hostname checks stay as they are.

   Reason: the owner added this host to be the GPU inference server on the LAN while the NUC "stays the front end (wake word, mic, speaker, face display)". One remote client keeps the server's surface and its firewall rule (IS-13) as small as they can be. The loopback smoke test needs no rule of its own.

2. **IS-2. The server speaks HTTP with JSON on one TCP port, `8300`, bound to the LAN address.**
   - Every endpoint is under that one port, given in `config/autobot.toml` (IS-3) and, for the firewall, in `INFRASTRUCTURE.md`.
   - The port is `8300` because the `assistant` project already uses 8099, 8123 and 8199.
   - No WebSocket is used. The one stream, chat tokens, is sent as server-sent events on the same port (IS-9).

   Reason: one port is one firewall rule and one thing to test from the NUC. HTTP and JSON need nothing on the NUC beyond the standard library or a small client, and are what local LLM runtimes already speak.

3. **IS-3. The NUC finds the server through `config/autobot.toml`, by address.** A `[server]` table holds:
   - the server's base URL (`http://192.168.86.53:8300`) and whether the server is enabled;
   - the path of the token file (IS-13);
   - the request timeouts (IS-4);
   - the poll intervals (IS-23);
   - the confidence threshold of each routing tier (IS-17).

   The same file holds the server's own settings, which are read on `GAMEON1`: the configured server hostname (IS-25) and the models it loads (IS-10). The NUC names the server by its reserved address, not by `gameon1.lan`. The table's exact fields are for `SCHEMA.md` when the client is built. Reason: `CONVENTIONS.md` keeps all configuration in that one file, and an address held by a DHCP reservation does not depend on the router's DNS staying up.

4. **IS-4. The NUC never depends on the server.** While the server is down, asleep, off, or answering slowly, the NUC falls back without being told to. Each job falls back on its own (IS-23):
   - speech-to-text to its local faster-whisper `tiny.en`;
   - routing to tiers 1 and 2 of the cascade (IS-17);
   - chat to the agent's ordered fallback (IS-18).

   Text-to-speech is always the NUC's local piper (IS-10). A wake is never lost, and the listener never waits on the network (IS-15).

   **Timeouts.** Every call to the server has a short timeout:

   | Call | Timeout |
   | --- | --- |
   | Health | 1.0 s |
   | Speech-to-text | 0.5 s plus 0.25 s per second of audio |
   | Route | 1.5 s |
   | Chat | 3 s to the first token, 30 s in total |

   On a timeout the NUC falls back at once and does not retry that request.

   **Latency budget.** Measured from the end of speech:

   | Path | Budget |
   | --- | --- |
   | A keyword-routed local answer starts speaking | within 1.0 s |
   | A command transcribed and routed by the server reaches its agent | within 1.5 s |
   | A local chat reply starts speaking (streamed) | within 2.0 s |
   | A Gemini handoff (IS-20) shows a visible "thinking" cue | within 0.5 s |

   Reason: the NUC is on 24x7 and the PC is not. Windows Update restarts and the owner's own use of the PC will take it away, and an assistant that goes deaf with it would be worse than one that is slower. A budget is what says whether the server is worth waiting for, and a request that is not retried keeps a slow server from doubling the wait.

5. **IS-5. The server is stateless and keeps no agent state or memory.** Each request carries everything the server needs: for chat, the agent's prompt and the conversation so far, which the NUC sends from its own store. The server keeps nothing between requests beyond the models loaded in memory, and writes no request or response to disk, except a log line of method, endpoint, size, duration and status. Reason: ADR 0002 and AS-2 keep agent state and memory on the local server, the NUC, and `AGENTS.md` forbids moving them off it. A stateless server also restarts and falls away (IS-4) without anything to lose.

6. **IS-6. Audio sent to the server is transcribed and dropped.** The server holds an utterance's audio only in memory and only while it transcribes it. It never writes audio, features or transcripts to disk and never forwards them anywhere. The owner's voice dataset (`data/voice/`) is never sent to this host, and no wake-word training runs here. Reason: TS-5 keeps the recordings and anything computed from them on the NUC; live utterances are not the dataset, but the same reasoning about the sound of the house applies, and a server that keeps nothing cannot leak it.

7. **IS-7. The server reaches no cloud service and holds no Google Cloud credential.** It opens no outbound connection at run time: models are loaded from local files, the speech libraries are imported behind `enforce_offline()` as `CONVENTIONS.md` "Local-only speech" requires, and telemetry is off. Downloading models is a separate, deliberate command, `make server-fetch-models` (IS-25), as `make fetch-models` is on the NUC. No service account key, `gcloud` login or Secret Manager access exists on this host; Gemini, when an agent uses it, is still called by the NUC as the service account (GC-4, IS-20). Reason: `CN-1` and `CN-2` forbid a cloud service in the speech path, and keeping the one credential on the NUC keeps GC-4's single identity in one place.

8. **IS-8. Speech-to-text: `POST /v1/stt`.**
   - **Request.** One utterance as WAV audio, 16 kHz, mono, 16-bit PCM, the format the listener already records, with the vocabulary hints of IS-16 beside it.
   - **Response.** JSON with:
     - the text and the language;
     - the segments, with their start and end times;
     - the audio's duration and the time taken;
     - the model that transcribed it.
   - **Model.** It is named in the server's configuration, not by the caller.
   - **No streaming.** The transcription is not streamed, because utterances are short and the NUC's VAD cuts them.
   - **Takes effect** once `CONSTRAINTS.md` carries the amendment to `CN-1`.

   Reason: the listener already cuts an utterance at the end of speech with Silero VAD, so a whole-utterance call is the simplest contract that gives the larger model's accuracy. The NUC's `tiny.en` takes the same input, which keeps the fallback of IS-4 a one-line switch. Naming the model in the response lets every transcript say where it came from (IS-15).

9. **IS-9. Routing: `POST /v1/route`; chat: `POST /v1/chat`.**
   - **Route.** The request carries the transcript and the list of agents the NUC can route to. Each agent comes with its name, description, commands, modes and parameter schema (AG-1, AG-7, IS-18, the `[[agents]]` and `[[routes]]` tables). The response names one agent from that list or `none`, with:
     - the agent's mode;
     - a confidence;
     - the parameters the model read from the transcript.

     The agent and the mode are constrained while the model generates them, by a JSON schema whose enums are built from that list plus `none`, and not only checked afterwards. The server never invents an agent outside the list. Routing is called only by the NUC's rules layer, as tier 3 of the cascade (IS-17), and the rules layer admits or refuses its answer, so RR-1 holds. Routing never streams.
   - **Chat.** The endpoint uses autobot's own shape, not `/v1/chat/completions`.
     - The request carries the system prompt, the messages, an optional `response_schema` (the two-part shape of AG-4) and optional `tools` (IS-21).
     - The response carries the model's answer, in the shape of `response_schema` when one was sent, or its tool calls, and the tokens used.
     - With `stream: true` the tokens come as server-sent events on the same port, so the NUC can speak sentence by sentence through piper.
     - The two parts of AG-4 are taken apart on the NUC, by the agent.
   - Both carry no state (IS-5).

   Reason: the owner wants "a local LLM for routing and the core conversational agent". Giving the model a closed list to choose from keeps routing a classification that the rules layer can check, as RR-3 requires each route to name its agent. A constrained enum means the model cannot even produce a name outside the list. AG-4 makes the agent, not the model's host, the builder of the response.

10. **IS-10. Model choices fit the 2080 Ti's 11 GB and its Turing architecture, within a budget the server checks.** Turing (compute 7.5) has float16 and int8 but no bfloat16 and no FlashAttention 2, so a runtime that requires either is out. The server checks this VRAM budget at start and reports it in `/health` (IS-11):

    | Part | Budget |
    | --- | --- |
    | Speech-to-text | at most 3 GB |
    | Language model weights | about 5 GB |
    | Language model KV cache | at most 1.5 GB |
    | CUDA contexts | about 1 GB |
    | **Total** | **at most 10.5 GB** of the card's 11,264 MiB, with the desktop's ~0.3 GB taken |

    - **Speech-to-text.** faster-whisper (CTranslate2) with `large-v3-turbo`, in `int8_float16` or `float16`, whichever measures better on Turing. It is the NUC's library already (ADR 0003), so only the model changes. faster-whisper's own README measures `large-v2` at 4,525 MB in fp16 and 2,926 MB in int8 (beam 5, on an RTX 3070 Ti), so the turbo figure is to be measured on this card. Current CTranslate2 needs CUDA 12 cuBLAS and cuDNN 9, which can come from pip wheels without a CUDA toolkit.
    - **Language model.**
      - One instruct model of 7 to 9 billion parameters, quantised to about 4 bits, serves routing, slot filling and chat, so that only one is resident.
      - It runs with a context of 8k tokens by default, one parallel slot, and a quantised KV cache where the runtime offers one.
      - It is chosen by measurement (IS-26) and not named in this spec. It is a value in `config/autobot.toml`, and changing it needs no code change.
      - The candidates to measure first, each at `Q4_K_M`, are a Qwen3 8B, a Gemma 4 that fits, and Llama 3.1 8B.
    - **Runtime.**
      - Ollama runs on `GAMEON1`, bound to `127.0.0.1` only, behind autobot's own server on port 8300, which is the only thing the NUC talks to.
      - The server reaches the runtime through one seam, so llama.cpp's `llama-server` can replace Ollama without a change to the contract.
      - The server never pulls a model. A missing model is a one-line "not ready" in `/health`, and models arrive only through `make server-fetch-models` (IS-25).
    - **Text-to-speech.** It is not in the first version: piper on the NUC stays the voice. `/v1/tts` is reserved, and `/health` reports `tts: false`. A later revision may add a neural voice (for example Kokoro), with piper as the fallback.

    Reason: the card has 11,264 MiB and every resident model must stay loaded (IS-24), because loading one per request would cost seconds that a voice reply cannot spare. A Whisper large-class model and a 4-bit 8B model together are the largest pair that fits. A budget checked at start fails visibly instead of out of memory mid-request. One seam to the runtime keeps the choice of runtime reversible.

11. **IS-11. `GET /health` says what the server can do.** It answers without touching a model. It reports:
    - the server's version and the contract version (IS-22);
    - which of `stt`, `route`, `chat` and `tts` are ready, `tts` being `false` in the first version (IS-10);
    - the model behind each job, with a one-line reason for any job that is not ready;
    - the VRAM budget of IS-10, and the GPU memory used and free.

    A job is ready only after its model has loaded and answered one warm-up request (IS-23). The NUC's health check (IS-23) reads it. Reason: the NUC needs to know not only that the host is up but which jobs it can hand over, since a model can fail to load while the server runs.

12. **IS-12. The server's code lives in this repository, in a `server/` folder at the root, and its tests need no GPU, no model and no network.**
    - `server/` has its own `pyproject.toml` and its own package, `autobot_server`, under `server/src/`, with its tests under `server/tests/`.
    - `make lint` and `make typecheck` cover `server/` as well.
    - The tests drive the endpoints with fakes for the speech model, the language model and the runtime, as `tests/agents/agent_fakes.py` does for the agents.
    - The request and response examples live once, under `server/contract/`. Both the NUC's test suite and the server's check against them.

    Reason: one repository keeps the client and the server's contract in step, and one set of examples checked from both sides catches a drift on either. The server has GPU dependencies that the NUC must never install, so it is a package of its own, and `CONVENTIONS.md` is amended to "one package per host". Tests that need a GPU could not run on the NUC, where `make test` runs.

13. **IS-13. Only the NUC can reach the port, and every request carries a shared token.**
    - **Address filter.** The Windows Firewall and the Hyper-V firewall each let TCP in on the server's port from the NUC's address only (`INFRASTRUCTURE.md` lists the rules). The server binds no port on the router's WAN side and is never exposed through the `assistant` project's Cloudflare tunnel.
    - **Token.** On top of the address filter, every request carries a shared bearer token. A request without the right token gets `401`.
    - **Where the token lives.** Outside the repository on each host, in a file with mode `0600`: on the NUC under the git-ignored `secrets/`, and on `GAMEON1` under `~/.config/autobot-server/`. The owner creates it with a `make` target that he runs himself. Agents never read or print it.
    - **Missing token.** A missing token on the NUC is a one-line reason on the main screen, and the server counts as down (IS-23).

    Reason: audio and transcripts from the house cross the LAN, and the address filter is a rule the owner can read in one line. The merge of the Hyper-V firewall and the host policy is untested, and Windows classes the LAN as Public, so the token makes a firewall mistake fail closed.

14. **IS-14. The server starts on its own when the PC boots.** A Windows scheduled task starts the WSL distribution at boot and keeps it running. Inside WSL, a systemd user unit, kept running by lingering (IS-25), starts the server and restarts it when it fails. The NUC does not start, stop or wake the server.

    The Windows, router and system steps are the owner's, done by hand before anything else:
    - the DHCP reservation;
    - both firewall rules;
    - the start-up task;
    - the `apt` install of `python3.12-venv`, `python3-pip`, `make` and `nodejs`;
    - the Ollama install;
    - lingering for the server's user unit.

    This host is the owner's gaming PC, so the server must expect to lose the GPU (IS-24). Reason: the NUC must be able to rely on the server whenever the PC is up, without the owner opening a terminal on Windows. The NUC has no rights on the PC, which is the owner's own machine.

15. **IS-15. The wake path never touches the server.** Wake detection, by every engine including `text_match`, stays on the NUC and never waits on the network. The server transcribes only utterances inside a session, after a wake: the command, the query and the answers to confirmations. Every transcript says whether it came from the server or from the NUC's `tiny.en`. Reason: `CN-1` keeps wake detection local, and IS-4 says a wake is never lost.

16. **IS-16. Speech-to-text is biased toward autobot's own words.** Each speech-to-text request carries hints, built on the NUC and capped in length, from:
    - the agents' names, with their commands, modes and aliases;
    - the yes and no words;
    - the global action keywords.

    The server passes them to the model as hotwords or as an initial prompt. Reason: transcription is the owner's weakest link today, and these words are what routing depends on.

17. **IS-17. Routing is a cascade inside the rules layer.** The global action keywords are checked first (AG-14). Then:
    - **Tier 1.** The `[[routes]]` keyword rules, as today (FS-15).
    - **Tier 2.** A semantic match on the NUC's CPU, with a small local embedding model, against each route's command and example phrases, above a configured threshold.
    - **Tier 3.** The server's language model, constrained to the closed list (IS-9).

    The rules layer admits a tier's answer only if all of these hold:
    - the agent exists;
    - the mode is one of its modes;
    - the parameters validate against its schema;
    - the confidence passes that tier's threshold.

    After that, AG-13's confirmations apply as for any route. When nothing is admitted, the core agent (IS-19) takes the request if the server is up. Otherwise autobot says it did not catch that. The tier that decided is shown on the face, where the edge on the map can mark it. With the server away, tiers 1 and 2 still work. Reason: RR-1 holds because a rule still admits every route, the NUC keeps most of its routing when `GAMEON1` is off, and the cheapest tier answers first.

18. **IS-18. Agents come in tiers.** An agent definition gains three fields:
    - `model`: `none`, `local` or `gemini`;
    - `fallback`: an ordered list of the same values, or a spoken line;
    - `parameters`: named slots, each with a type, its allowed values and whether it is required.

    The three kinds behave differently:
    - A `none` agent runs code only.
    - A `local` agent with parameters gets them filled by the route call (IS-9).
    - A `local` or `gemini` agent that reasons calls its model through the existing model seam, `autobot.agents.gemini.Model`, of which the local model is a second implementation.

    AS-1 stays true for every agent whose model is `gemini`. The definitions are data in `[[agents]]`, edited in the Agent editor (AG-10). Reason: the owner wants the layer programmable and extensible, so the choice of model is a setting of each agent and not code (AG-1). One model seam means an agent does not change when its model does, and the local model sits beside Gemini rather than replacing it.

19. **IS-19. The core agent.** A new agent, `core`, whose model is `local`, is autobot's conversational self.
    - Its personality is its system prompt, in its definition, edited in the Agent editor.
    - It is the default when no route is admitted (IS-17).
    - It is multi-turn within a session (AS-5). The conversation is kept on the NUC (AG-11), sent whole with each request (IS-5), and trimmed to the context budget by dropping the oldest turns.
    - When the server is away, it follows its fallback (IS-18).

    Reason: the owner wants a local language model behind "the core conversational agent". Keeping the conversation on the NUC and sending it whole is what lets the agent be conversational while the server stays stateless.

20. **IS-20. Hard requests are handed to Gemini by the NUC.** The core agent's structured answer may ask to hand the request to one upstream Gemini agent, chosen from a closed list it is given, with the query that agent should get. The NUC carries out the handoff:
    - as the service account (GC-4);
    - after AG-13's before-request confirmation, if that agent has it turned on;
    - within GC-10's caps.

    The server never calls a cloud service (IS-7). Reason: the owner's goal is a local layer that "hands hard requests to the upstream Gemini ADK agents". A closed list keeps the handoff a choice the NUC can check, and making the call from the NUC keeps the one Google Cloud identity and its cost guards in one place.

21. **IS-21. Tools are added without server changes.**
    - Tools are declared in the NUC's agent definitions and sent with each chat request as schemas.
    - The model may answer with tool calls. The NUC runs them and sends the results in the next request, for at most a configured number of rounds per turn.
    - The server executes no tool and holds no agent list. Adding an agent or a tool changes only the NUC's configuration and code.

    Reason: extensibility without touching the server, and the statelessness of IS-5.

22. **IS-22. The contract is versioned.** Endpoints sit under `/v1`, and `/health` reports the contract version. A NUC that finds a different major version treats the server as down, with a one-line reason on the main screen. Reason: the two hosts are updated separately, by a pull on each.

23. **IS-23. The NUC watches for the server.**
    - **States.** `unknown`, `up`, `degraded` (some jobs ready) and `down`.
    - **Polling.** The NUC polls `/health` every 5 s while the server is up, and backs off from 5 s to 60 s while it is down. Two failures in a row mark the server down, and one healthy answer marks it up.
    - **Fallback.** Each job falls back on its own (IS-4).
    - **Display.** The main screen shows the server's state beside the listener's, and the face reacts when the server comes or goes.
    - **Readiness.** The server reports a job ready only after its model has loaded and answered one warm-up request.

    Reason: the owner wants the NUC to "look for the API to come alive", and a model can fail while the process runs.

24. **IS-24. The GPU can be taken away.**
    - Every resident model loads at start and stays loaded.
    - A job that hits a CUDA out-of-memory error, or loses the GPU to a game, reports not ready rather than crashing or restarting in a loop. It recovers when memory frees.
    - A busy job holds at most one waiting request. It answers any further one at once with `503` and `Retry-After`, so the NUC falls back instead of waiting.

    Reason: `GAMEON1` is the owner's gaming PC, so a game can take the card at any time. A server that crashes or queues would break IS-4's promise that the NUC never waits on it.

25. **IS-25. Runtime control on `GAMEON1` is `make`.** The targets on `GAMEON1` are:

    | Target | What it does |
    | --- | --- |
    | `server-setup` | Sets up the server |
    | `server-fetch-models` | Downloads the models; the only downloader on that host |
    | `server-up` | Starts the server |
    | `server-down` | Stops the server |
    | `server-status` | Shows the server's state |
    | `server-logs` | Shows the server's log |
    | `server-install` | Installs a systemd user unit, with lingering |
    | `server-check` | Runs a loopback smoke test of each endpoint with a committed fixture |

    The server refuses to start unless the hostname equals the configured server hostname, as TS-11 does for the NUC. On the NUC, `make server-ping` checks the server from the NUC. Every target appears in the bare `make` summary. Reason: `AGENTS.md` makes the `Makefile` the runtime control layer and requires each target in the bare summary. A hostname check keeps the server off the NUC, as TS-11 keeps the front end off other machines.

26. **IS-26. The models are evaluated, not tested.** `make server-eval` runs on `GAMEON1` against the real models and writes a report file. It measures:
    - route accuracy, on a committed, labelled set of typed utterances, with no owner audio (TS-5);
    - JSON validity against the schema;
    - latency to the first token and to the full answer.

    It is how the language model of IS-10 is chosen. Reason: the tests of IS-12 use fakes and can say nothing about a real model's accuracy or speed. Typed utterances keep the owner's recordings out of the evaluation, as TS-5 requires.

27. **IS-27. Audio-native models are out of the first version.** Qwen2.5-Omni-3B's own README gives a theoretical minimum of 18.38 GB in BF16 (15 s of video), and Turing has no bfloat16, so the model cannot be resident beside the others on 11 GB. It may be tried later as an experiment profile, off by default, that unloads the other models and returns text only. Reason: the VRAM budget of IS-10 and the resident models of IS-24 leave no room for it.

## Settled without asking

- **Resolved.** Tier 2 of the cascade (IS-17) runs on the NUC's CPU. The earlier sketch had every model on the server. This revision keeps the owner's placement because it lets tiers 1 and 2 keep working while `GAMEON1` is away (IS-4).
- **Resolved.** IS-4 no longer lists a text-to-speech fallback, because text-to-speech does not move to the server in the first version (IS-10).
- **Default.** The `core` agent ships with the fallback `gemini`, then the spoken line "the server is away". This follows the chat fallback the spec named before IS-18 replaced it. The owner edits it in the Agent editor.
- **Default.** The `make` target that creates the token (IS-13) is not named here. The plan names it, and it appears in the bare `make` summary (IS-25).

## Changes needed elsewhere

- **This spec's `describes:`.** It gains `server/**` once the `server/` folder exists (IS-12).
- **docs/context/CONSTRAINTS.md.** Amend `CN-1` and `CN-2`, with a revision line. Speech-to-text and text-to-speech run on the owner's own machines on the house LAN (the NUC and `GAMEON1`), never on a cloud service, hosted API or remote model outside the house. Wake-word detection and modelling stay on the NUC only (IS-15). Answer the open question on whether the finished transcript may go to the cloud for reasoning: AG-5 already sends it, and IS-20 does so too.
- **docs/decisions/0007-…-ADR.md (new).** Record local models beside Gemini, as an addition and not a replacement. Each agent definition names its model (`none`, `local` or `gemini`) and an ordered fallback (IS-18). AS-1 stays true for every agent whose model is `gemini`.
- **docs/context/CONVENTIONS.md.** Change "one package" to "one package per host" (`autobot` under `src/` on the NUC, `autobot_server` under `server/src/` on `GAMEON1`, each with its own `pyproject.toml`). "Local-only speech" becomes speech kept inside the house and off any cloud.
- **AGENTS.md.** Describe the server, its `make` targets (IS-25), its tests under `server/tests/` and its contract under `server/contract/` (IS-12). Add the token files to what agents never read or print (IS-13). The sentence "a change to the listener's speech path keeps it offline" becomes "keeps it inside the house and off any cloud".
- **docs/specs/active/rules-first-routing-SPEC.md.** RR-1 changes so that a route is a rule, or a tier of the cascade that the rules layer admits (IS-17). Answer the open question on no match: the core agent takes the request if the server is up, otherwise autobot says it did not catch that. Answer the open question on latency with the budget of IS-4.
- **docs/specs/active/agent-sessions-SPEC.md.** AS-1 changes so that each agent names its model (IS-18). Answer the open question on which model each agent uses and whether it is configurable per agent.
- **docs/specs/active/autobot-agents-and-the-agent-tree-SPEC.md.** AG-1: the definition gains `model`, `fallback`, `parameters` and `tools` (IS-18, IS-21), and the `core` agent is added (IS-19). AG-12: speech stays inside the house, a transcript may come from the server (IS-15), and the face's network connections gain the inference server on the LAN. AG-3: the edge on the map can mark the routing tier that decided (IS-17).
- **docs/specs/active/autobot-the-face-SPEC.md.** FS-13's network exception gains the inference server on the LAN. The face reacts when the server comes or goes (IS-23). FS-15's keyword rules become tier 1 of the cascade (IS-17).
- **docs/specs/active/autobot-wake-word-training-studio-SPEC.md.** The main screen shows the server's state beside the listener's, including the one-line reasons for a missing token or a contract mismatch (IS-13, IS-22, IS-23).
- **docs/context/SCHEMA.md.** Add the `[server]` table: URL, enabled, token file, timeouts, poll intervals and the threshold of each tier (IS-3). Add the agent fields `model`, `fallback`, `parameters` and `tools`, and example phrases on routes (IS-17, IS-18, IS-21). Add the request and response shapes of `/v1/stt`, `/v1/route`, `/v1/chat` and `/health`.
- **docs/context/GLOSSARY.md.** Add "inference server", "tier", "cascade", "core agent", "handoff" and "tool".
- **docs/context/ARCH.md.** Add the server as a part, and the seams between it and the listener, the rules layer and the agents.
- **docs/context/INFRASTRUCTURE.md.** Confirm that the CPU is a 5800X3D, as it already says. Add the token files, the systemd user unit with lingering, and whether WSL idles a systemd distribution.

## Open Questions

- **Which model family wins `make server-eval`.** Recommended: the candidate of IS-10 with the best route accuracy and JSON validity that stays within IS-4's latency budget. Routing correctness is what the rules layer depends on.
- **Whether tier 2's embedding model stays on the NUC once measured.** Recommended: it stays on the NUC unless its latency on the CPU breaks the 1.5 s budget of IS-4. On the NUC it works while the server is away.
- **Whether Wake-on-LAN works.** Recommended: until it is tested, the NUC does not wake the server (IS-14) and treats a sleeping PC as down. Waking the owner's gaming PC is a change to IS-14 that he has to choose.
- **Whether a neural voice later moves to the server.** Recommended: piper stays the only voice until a neural voice is measured within the VRAM budget of IS-10. piper is fast for short replies, and it is the fallback in any case.
