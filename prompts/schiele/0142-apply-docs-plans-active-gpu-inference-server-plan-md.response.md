---
run: schiele/0142
at: 2026-11-27T21:52:15.564Z
mode: apply
agent: apply
model: claude:claude-opus-5-5
target: docs/plans/active/gpu-inference-server-PLAN.md
template: apply
prompt-sha: bee93d3305ca
template-sha: bf7cd0cb8062
commit: d4613d4+
attachments: []
read:
  - AGENTS.md
  - docs/plans/active/gpu-inference-server-PLAN.md
  - pyproject.toml
  - server/contract/contract.json
  - server/contract/health.json
  - server/contract/stt-request.json
  - server/contract/stt-response.json
  - server/contract/route-request.json
  - server/contract/route-response.json
  - server/contract/chat-request.json
  - server/contract/chat-response.json
  - server/contract/chat-stream.txt
  - src/autobot/server/__init__.py
  - src/autobot/server/contract.py
  - tests/server/__init__.py
  - tests/server/test_contract.py
  - docs/specs/active/autobot-gpu-inference-server-SPEC.md
  - src/autobot/config.py
  - server/src/autobot_server/app.py
outcome: success
usage:
  input: 4245219
  output: 56364
  peak: 142951
  window: 1000000
cost: 0
harness:
  provider: claude
  kind: sdk
  workspace: self
  access: build
  turns: 52
  sandbox: true
  changed:
    - Makefile
    - pyproject.toml
    - server/pyproject.toml
    - server/src/autobot_server/__init__.py
    - server/src/autobot_server/__main__.py
    - server/src/autobot_server/app.py
    - server/src/autobot_server/auth.py
    - server/src/autobot_server/config.py
    - server/src/autobot_server/contract.py
    - server/src/autobot_server/gpu.py
    - server/src/autobot_server/health.py
    - server/src/autobot_server/hostcheck.py
    - server/src/autobot_server/http.py
    - server/src/autobot_server/job.py
    - server/tests/__init__.py
    - server/tests/server_fakes.py
    - server/tests/test_app.py
    - server/tests/test_auth.py
    - server/tests/test_config.py
    - server/tests/test_contract.py
    - server/tests/test_health.py
    - server/tests/test_job.py
---

## Result

I applied step 3, the only step this run was asked to do. Steps 4 to 12 were not started. The step is done and its "Done when" holds: the server's 97 new tests pass on fakes with no GPU, the type check passes, and lint passes everywhere except one unreadable file this change doesn't touch.

## Steps

### 1–2: already done (runs schiele/0138 and schiele/0140)
Not touched.

### 3. The server package: health, token, hostname, jobs: done, verified
- **Packaging:**
  - `server/pyproject.toml` defines `autobot-server`. Its base needs only the standard library; the `gpu` extra holds faster-whisper, cuBLAS and cuDNN 9.
  - The root `pyproject.toml` adds `server/src` and `server/tests` to ruff, mypy and pytest. It also adds `server/src` to pytest's `pythonpath`, so the tests run before the package is installed.
  - `make setup` now also installs `./server` as an editable package, without the `gpu` extra.
- **`config.py`** reads only the `[server]` table, with `tomllib`, and honours `AUTOBOT_CONFIG`. It never imports `autobot`, and a test checks the source for such an import.
  - It knows every key in the table and refuses unknown ones. It checks only the keys the server uses: the hostname, the port in `url`, the models, the runtime and the context size.
  - It finds the checkout root as the nearest folder above it that holds `server/pyproject.toml`.
- **`contract.py`** is the server's own copy of the NUC's shapes, as plan choice 2 requires. It reads the same `server/contract/` examples, and its own test reads every one back.
- **`hostcheck.py`:** `require_server_host` gives a one-line refusal and ignores case.
- **`auth.py`:**
  - It reads the token from `~/.config/autobot-server/token`. It refuses a missing file, an empty one, or any mode but `0600`, with a one-line message that never contains the token.
  - The bearer check uses `hmac.compare_digest`.
- **`gpu.py`:**
  - The budget figures of IS-10 add up to 10,752 MiB.
  - `NvidiaSmi` reads used and free memory with `nvidia-smi`, behind a `Gpu` protocol.
  - `check_budget` stops the server with one line when the budget exceeds the card's total memory (used plus free).
- **`job.py`:** a job is `loading`, `ready` or `not ready` with a reason.
  - It warms up once after loading, and admits one running request and one waiting; a third gets `Busy`.
  - An out-of-memory or other CUDA error marks the job not ready. `tick()` retries the warm-up every 30 s, without reloading or restarting. `keep_warm` runs that loop.
- **`health.py`** builds the `/health` body without touching a model, with `tts: false`.
- **`app.py`** is the dispatch function `handle(method, path, headers, body)`:
  - The bearer token is checked first on every path (`401`).
  - `GET /health` answers; `/v1/tts` gives `501`; unknown paths `404`; wrong methods `405`.
  - From job endpoints: `Busy` gives `503` with `Retry-After: 1`, not ready gives `503`, a malformed body `400`, anything else `500`.
  - Each request writes one log line: method, endpoint, size, duration, status. No body and no token.
- **`http.py`** runs a `ThreadingHTTPServer` on `0.0.0.0` at the configured port. It refuses bodies over 16 MiB with `413` and can stream a response body, ready for chat's events in step 5.
- **`__main__.py`** checks config, host, token, budget and contract version before binding the port, then warms the jobs in the background.
- **Checks:**
  - `pytest server/tests tests/server`: 130 passed.
  - `make typecheck`: no issues in 181 files.
  - `ruff check` and `ruff format --check` over everything except `.claude/`: all pass.
  - "Done when": `handle("GET", "/health", …)` on fakes equals `server/contract/health.json`, a missing bearer gets `401`, and a third concurrent request gets `503`. Each is a passing test (`test_app.py`, `test_job.py`).
  - `make test`: 1574 passed and 15 failed. These are the same 15 recorded before this change: 13 tests that open sockets the sandbox refuses, plus `test_the_committed_config_is_a_fresh_install` and `test_the_committed_definitions_seed_what_the_agents_need`.
  - `make lint` still exits non-zero, only because ruff cannot read the untracked `.claude/loop.md`.

**Things to know about this step:**
- **Jobs report not ready on purpose.** Until steps 4 and 5 provide the models, the server's three jobs say "this server version has no … model yet" in `/health`. This is real behaviour, not a stub.
- **Steps 4 and 5 will touch two files they don't list.** Their endpoints are added through `App(endpoints=…)`, so they will need a small change to `__main__.py`. Step 5's streamed chat also holds the job's lock only until its iterator is returned, which `job.py` may need to account for.
- **`/health` still answers if `nvidia-smi` fails.** It reports 0 MiB used and 0 free, since the contract has no field for the reason.
- **Error bodies are `{"error": "<one line>"}`.** The contract examples define no error shape.

### 4 to 12: not started
Outside this run's scope.

## Files changed

- `server/pyproject.toml`: new; the server package and its `gpu` extra.
- `server/src/autobot_server/__init__.py`: new; package docstring and `__version__`.
- `server/src/autobot_server/__main__.py`: new; start-up checks, binding, background warm-up.
- `server/src/autobot_server/config.py`: new; the server's own reader of `[server]`.
- `server/src/autobot_server/contract.py`: new; the server's copy of the contract shapes.
- `server/src/autobot_server/hostcheck.py`: new; refuses any host but `[server] hostname`.
- `server/src/autobot_server/auth.py`: new; token file and bearer check.
- `server/src/autobot_server/gpu.py`: new; VRAM budget, `nvidia-smi` reader, start-up check.
- `server/src/autobot_server/job.py`: new; job states, admission, GPU-loss handling, retry.
- `server/src/autobot_server/health.py`: new; the `/health` body.
- `server/src/autobot_server/app.py`: new; the dispatch function and the log line.
- `server/src/autobot_server/http.py`: new; the HTTP server.
- `server/tests/__init__.py`: new, empty; not named in the plan. Without it, mypy and pytest see two modules named `test_config` (this one and `tests/test_config.py`).
- `server/tests/server_fakes.py`: new; fake GPU, engine, clock, token file and app.
- `server/tests/test_config.py`, `test_contract.py`, `test_auth.py`, `test_health.py`, `test_job.py`, `test_app.py`: new; the step's tests.
- `pyproject.toml`: ruff, mypy and pytest now cover `server/`.
- `Makefile`: `setup` also installs `./server` as an editable package. Step 3 calls for this, though the plan lists the Makefile under step 6.

## For you to do

- Run `make setup` once; the sandbox has no network for pip. Then run `make test` and `make lint` outside the sandbox to clear the socket tests and the `.claude/loop.md` read.
- The plan's frontmatter still shows steps 2 and 3 as pending, and the spec's `describes:` should gain `server/**` now that step 3 has landed. I was not allowed to edit either file.
