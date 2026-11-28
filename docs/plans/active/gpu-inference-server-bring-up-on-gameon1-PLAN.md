---
parent: docs/specs/active/autobot-gpu-inference-server-SPEC.md
type: plan
title: GPU inference server bring-up on GAMEON1
authority: prescriptive
status: active
steps:
  total: 8
  completed: [1, 2, 3, 4, 5, 6, 7, 8]
  status:
    1: { state: "completed", written: "schiele/gameon1-bring-up-run", at: "2026-11-27T23:58:00Z" }
    2: { state: "completed", written: "schiele/gameon1-bring-up-run", at: "2026-11-27T23:58:00Z" }
    3: { state: "completed", written: "schiele/gameon1-bring-up-run", at: "2026-11-28T00:16:00Z" }
    4: { state: "completed", written: "schiele/gameon1-bring-up-run", at: "2026-11-28T00:16:00Z" }
    5: { state: "completed", written: "schiele/gameon1-bring-up-run", at: "2026-11-28T00:16:00Z" }
    6: { state: "completed", written: "schiele/gameon1-bring-up-run", at: "2026-11-28T00:25:00Z" }
    7: { state: "completed", written: "schiele/gameon1-bring-up-run", at: "2026-11-28T00:26:00Z" }
    8: { state: "completed", written: "schiele/gameon1-bring-up-run", at: "2026-11-28T00:38:00Z" }
describes:
  - config/autobot.toml
  - Makefile
  - deploy/autobot-server.service
  - server/pyproject.toml
  - server/src/autobot_server/__main__.py
  - server/src/autobot_server/control.py
  - server/src/autobot_server/check.py
  - server/src/autobot_server/eval.py
  - server/src/autobot_server/fetch_models.py
  - server/src/autobot_server/ollama.py
  - server/src/autobot_server/stt.py
  - server/tests/test_chat.py
  - server/tests/test_control.py
  - server/tests/test_eval.py
  - server/tests/test_stt.py
  - docs/context/INFRASTRUCTURE.md
  - docs/plans/active/gpu-inference-server-PLAN.md
provenance:
  - written: schiele/gameon1-bring-up-plan
    at: 2026-11-27T23:52:45Z
    mode: create
  - written: schiele/gameon1-bring-up-run
    at: 2026-11-27T23:58:00Z
    mode: apply
---

# GPU Inference Server Bring-up on GAMEON1 — Implementation Plan

Implements the autobot — GPU inference server spec (`docs/specs/active/autobot-gpu-inference-server-SPEC.md`).

This plan is run **on `GAMEON1`**, inside its `Ubuntu-24.04` WSL distribution, by a session working in the checkout there. It finishes what `docs/plans/active/gpu-inference-server-PLAN.md` (the parent plan) could not finish from the NUC: its step 6, "Runtime control on `GAMEON1`", and its step 12, "The evaluation", are both stamped `failed` there because their "Done when" needs this host. All of the code exists (commit `a1153f8` on `cr2/schiele/working`). It was built and tested on the NUC on fakes, and over loopback HTTP with fake models. **None of it has ever met a real GPU, a real faster-whisper model or a real Ollama.** Expect the first real faults here, and fix them here.

Read before starting: `AGENTS.md`; the spec, rules IS-1 to IS-27; the parent plan, steps 6 and 12; and `docs/context/INFRASTRUCTURE.md`, the sections "GPU inference server: `GAMEON1`", "Setting the server up on `GAMEON1`" and "The LAN between them".

## 0. The delta

| Spec Rule | Current Implementation | Planned Change |
|---|---|---|
| IS-14 owner's hand steps | Not known to be done | Checked, not done, by the session; what is missing is listed for the owner (step 1) |
| IS-25 `server-setup` | Target exists; never run | `.venv-server` built; CUDA libraries load (step 2) |
| IS-7, IS-25 `server-fetch-models` | Target exists; never run | `large-v3-turbo` fetched; the owner pulls the language models (step 3) |
| IS-13 token | Target exists; never run | The owner makes the token; the session never reads it (step 4) |
| IS-8, IS-10, IS-11 speech-to-text on the card | Tested on a fake speech model | The `stt` job ready in `/health`; compute type and VRAM measured (step 5) |
| IS-10, IS-26 language model | `llm_model` empty; `server-eval` tested on a fake runtime | Each candidate measured; the owner writes the winner (step 6) |
| IS-9, IS-25 `server-check` | Tested on fakes | Passes against the real models (step 7) |
| IS-14, IS-25 `server-install` | Target exists; never run | The unit runs, with lingering (step 7) |
| Parent plan steps 6 and 12 | Stamped `failed` | Step 12 stamped completed here; step 6 left for the NUC's `make server-ping` (step 8) |

## 1. Check the host and the owner's hand steps

**Files:** none changed.

The owner's steps of IS-14 need rights no session has. Check each, change none, and stop with a list when one that blocks the next step is missing.

- `hostname` is `GAMEON1` (compared without case), the checkout is at or after commit `a1153f8`, and `git status` is clean.
- `python3 -m venv --help`, `pip`, `make` and `node` work (`sudo apt install python3.12-venv python3-pip make nodejs` is the owner's).
- `nvidia-smi` reports the RTX 2080 Ti and its memory.
- `ollama --version` works and `curl -s http://127.0.0.1:11434/api/tags` answers. Ollama listens on `127.0.0.1` only, and its service has `OLLAMA_NUM_PARALLEL=1`.
- Not blocking, to report: the DHCP reservation, the two firewall rules and the Windows start-up task of "The LAN between them". They are needed only when the NUC calls (step 8).
- `make test`, `make lint` and `make typecheck` need the NUC's `.venv` and models and are **not** run here. The server's own tests are: after step 2, `.venv-server/bin/python -m pip install pytest`, then `.venv-server/bin/python -m pytest server/tests -q` from the checkout's root.

**Done when:** the session has printed one line per item above, each *ok* or *missing: <what the owner runs>*, and nothing that blocks step 2 is missing.

## 2. The server's environment

**Files:** possibly `server/pyproject.toml`, `Makefile`, `deploy/autobot-server.service`, `server/src/autobot_server/control.py`.

- Run `make server-setup`.
- Prove the GPU libraries load from `.venv-server`: `import ctranslate2; ctranslate2.get_cuda_device_count()` is 1, and `ctranslate2.get_supported_compute_types("cuda")` lists `int8_float16` and `float16`.
- **Suspected fault.** cuBLAS and cuDNN come from pip wheels (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`), whose libraries are not on the loader's path by default. If loading the model in step 5 fails on `libcudnn` or `libcublas`, the fix is an `LD_LIBRARY_PATH` naming the wheels' `lib` directories, set where the server is started: in `deploy/autobot-server.service` and in `control.py`'s foreground start, so that both modes behave the same. Add a test for whatever is changed.
- Run the server's tests as step 1 says.

**Done when:** `.venv-server/bin/python -m pytest server/tests -q` passes on this host, and the two `ctranslate2` calls answer as above.

## 3. The models

**Files:** possibly `server/src/autobot_server/fetch_models.py`.

- Run `make server-fetch-models`. It downloads the five files of faster-whisper `large-v3-turbo` (about 1.6 GB) into `models/faster-whisper/large-v3-turbo/` and nothing else. If a URL fails, correct it in `fetch_models.py`.
- It prints the `ollama pull` lines. **The owner runs them**, as IS-10 has it: `qwen3:8b`, `llama3.1:8b`, and a Gemma 4 that fits in about 5 GB at `Q4_K_M`, whose tag he picks from Ollama's library. A session does not pull.

**Done when:** the five files are in `models/faster-whisper/large-v3-turbo/`, and `ollama list` shows the candidates the owner chose to measure.

## 4. The token

**Files:** none changed.

- **The owner runs `make server-token`** and keeps the value for the NUC. A session never runs it, and never reads, prints or copies `~/.config/autobot-server/token` (`AGENTS.md`, IS-13).
- The session only checks that the file exists and is mode `0600`: `stat -c '%a' ~/.config/autobot-server/token`.

**Done when:** that `stat` prints `600`.

## 5. Speech-to-text on the card

**Files:** possibly `server/src/autobot_server/stt.py`, `server/src/autobot_server/__main__.py`, `server/tests/test_stt.py`, `config/autobot.toml`.

- Run `make server-up`, then `make server-status`, and read `make server-logs`. With `[server] llm_model` still empty, `route` and `chat` report *not ready: no language model is chosen*: that is right at this step.
- The `stt` job must report ready. Fix what stops it. Things never tried on a real card: `WhisperModel(device="cuda", local_files_only=True)` on the fetched directory, the `hotwords` argument, and the warm-up on one second of silence.
- Measure both compute types of IS-10, `int8_float16` and `float16`, by setting `[server] stt_compute_type`, restarting, and reading the VRAM used in `/health` and the `elapsed_seconds` of a `/v1/stt` answer for `tests/fixtures/audio/hey_autobot.wav`. Keep the one that is faster within the 3 GB budget for speech-to-text.

**Done when:** `make server-status` shows `stt  large-v3-turbo: ready`, and the session has reported, for each compute type, the VRAM used and the time taken for the fixture.

## 6. The language model, measured

**Files:** possibly `server/src/autobot_server/ollama.py`, `server/src/autobot_server/eval.py`, `server/tests/test_chat.py`, `server/tests/test_eval.py`, `config/autobot.toml`.

- Run `make server-eval MODEL=<name>` once for each candidate of step 3. Each writes `run/server-eval-<model>.json` and prints route accuracy, JSON validity and latency against IS-4's budget.
- Things `ollama.py` assumes that no real Ollama has confirmed: the top-level `"think": false` of `/api/chat` (a model that does not think must not refuse it); `"format"` taking a JSON schema; `"keep_alive": -1`; the model's name in `/api/tags` as `name` or `model`; the token counts `prompt_eval_count` and `eval_count`; and tool calls under `message.tool_calls[].function`. Where Ollama differs, change `ollama.py` and its tests in `server/tests/test_chat.py` together.
- Report the candidates side by side: accuracy, exact, JSON validity, route p95, first-token p95, and whether each is within budget. Say which one IS-26's criterion picks: the best route accuracy and JSON validity that stays within the budget.
- **The owner writes the winner** to `[server] llm_model` in `config/autobot.toml`. Then `make server-down` and `make server-up`.
- With the winner loaded, check the VRAM budget of IS-10 against `/health`: used memory with both models resident at most 10.5 GB.

**Done when:** a report file exists for each candidate measured, `[server] llm_model` names the owner's choice, and `make server-status` shows `stt`, `route` and `chat` all ready.

## 7. The smoke test and the unit

**Files:** possibly `server/src/autobot_server/check.py`, `server/src/autobot_server/control.py`, `deploy/autobot-server.service`, `server/tests/test_control.py`.

- Run `make server-check`. All four lines must read `ok`.
- Run `make server-install`. It stops a foreground server, writes and enables the unit, turns lingering on and starts it. If lingering is refused, the message names the `sudo loginctl enable-linger` the owner runs.
- Run `make server-status` and `make server-check` again, against the unit.
- From Windows the owner may restart the distribution (`wsl.exe --shutdown`, then open it) to see the unit come back by itself; whether WSL keeps a systemd distribution running when idle is an open question of `INFRASTRUCTURE.md`, to be answered here.

**Done when:** `make server-status` reports the unit running with all three jobs ready, and `make server-check` ends with "the server answers every endpoint".

## 8. Record it and hand back to the NUC

**Files:** `docs/context/INFRASTRUCTURE.md`, `docs/plans/active/gpu-inference-server-PLAN.md`, and whatever steps 2 to 7 changed.

- Correct `INFRASTRUCTURE.md` from what this host showed. It is descriptive: it says what a person would find. The sentence that the server "has not yet been set up or run on this host" goes; the measured compute type, VRAM figures and the chosen model go in; the open question "GPU server: not yet run" is removed or narrowed to what is still unknown.
- In the parent plan's frontmatter, stamp step 12 `completed` once its "Done when" holds (a report per candidate, and the winner written by the owner). Leave step 6 as it is: its last clause, `make server-ping` printing `up` on the NUC, is the NUC's to prove.
- Add this run's hand-written prompt record and provenance entries, as `.agents/skills/cr2-conformance/SKILL.md` says, and run `node .agents/skills/cr2-conformance/scripts/check.mjs`.
- Commit and push to `cr2/schiele/working` **only when the owner asks**. The NUC needs the commit: both hosts read `[server]` from the same `config/autobot.toml`.
- Tell the owner what is left, on the NUC: pull; `make server-token` and enter the value at the hidden prompt; `make server-ping`. If the ping reads `down: the server is away`, the firewall rules and the reservation of "The LAN between them" are what to look at.

**Done when:** `INFRASTRUCTURE.md` describes the server as running, the parent plan's step 12 is stamped completed, the conformance check passes, and the owner has the three NUC commands.

## 9. Choices the spec leaves open

1. **Faults are fixed on this host, with their tests.** The code under `server/` is the server's own package and its tests need no GPU, so a fix and its test are made and run here (`.venv-server/bin/python -m pytest server/tests`), and reach the NUC by the commit of step 8.
2. **Foreground first, the unit last.** `make server-up` without the unit gives the quickest loop while things are being fixed; `make server-install` comes once `server-check` passes.
3. **The compute type is chosen by measurement on this card**, as IS-10 asks, and written to `[server] stt_compute_type`.

## 10. Out of scope

Anything on the NUC: `make server-token` there, `make server-ping`, `[server] stt_enabled`, the amendment of `CN-1` and `CN-2`, and example phrases for tier 2. The owner's hand steps themselves: the router's reservation, the firewall rules, the Windows start-up task, the `apt` install, the Ollama install and its pulls, the token, and the choice of the language model. Text-to-speech. Any change under `src/autobot/` or `tests/`, which this host cannot test: a fault found there is reported, not fixed.

## Open Questions

- **Which Gemma 4 tag.** The owner picks it from Ollama's library; the spec asks only that it fit.
- **Does WSL keep the distribution up?** Step 7 finds out whether a systemd user unit with lingering survives an idle distribution, or whether the start-up task's held process is needed.
