---
run: schiele/0144
at: 2026-11-27T22:03:36.955Z
mode: apply
agent: apply
model: claude:claude-opus-5-5
target: docs/plans/active/gpu-inference-server-PLAN.md
template: apply
prompt-sha: 81034e5282e5
template-sha: bf7cd0cb8062
commit: a485716+
attachments: []
read:
  - AGENTS.md
  - docs/plans/active/gpu-inference-server-PLAN.md
  - server/pyproject.toml
  - server/src/autobot_server/__init__.py
  - server/tests/server_fakes.py
  - pyproject.toml
  - server/src/autobot_server/job.py
  - server/src/autobot_server/app.py
  - server/src/autobot_server/__main__.py
  - server/src/autobot_server/config.py
  - server/src/autobot_server/contract.py
outcome: success
usage:
  input: 1004792
  output: 17146
  peak: 88784
  window: 1000000
cost: 0
harness:
  provider: claude
  kind: sdk
  workspace: self
  access: build
  turns: 19
  sandbox: true
  changed:
    - server/src/autobot_server/__main__.py
    - server/src/autobot_server/stt.py
    - server/tests/test_stt.py
---

## Result

I applied step 4 only. Steps 5 to 12 were not started. On a fake speech model, both halves of the "Done when" hold:
- the committed fixture gets a reply shaped like `stt-response.json`;
- 8 kHz audio gets `400`.

The real faster-whisper path on the card has not been run, because that needs `GAMEON1`.

## Steps

### 1–3: already done (runs schiele/0138, 0140, 0142)
Not touched.

### 4. Speech-to-text: done, verified on fakes
- **`server/src/autobot_server/stt.py`** (new):
  - **Model loading:** `WhisperJob` is the engine behind the `stt` job. It loads faster-whisper from `[server] stt_model` with `device="cuda"`, the configured compute type and `local_files_only=True`.
  - **Offline:** the server's own copy of `_OFFLINE_ENV` is set by `enforce_offline()` before faster-whisper is imported, and that import sits inside the function that uses it.
  - **Missing model:** if `model.bin`, `config.json` or `tokenizer.json` is missing, the job is not ready. The reason names the file and points to `make server-fetch-models`; nothing is fetched.
  - **Warm-up:** it transcribes one second of silence.
  - **Audio:** `decode_wav` reads the WAV with the standard library `wave`, in memory. Anything but 16 kHz mono 16-bit is a `ContractError`, which the app answers with `400`.
  - **Hints:** they become one glossary entry, `Glossary: autobot, weather, ….`, in the same wording the NUC's `text_match` engine uses.
  - **Reply:** `stt_endpoint` builds the IS-8 body: text, language, segments with times, duration, time taken and model name. Nothing is written to disk.
- **`server/tests/test_stt.py`** (new): 26 tests covering:
  - the fixture `tests/fixtures/audio/hey_autobot.wav` gets the contract shape, and its exact frames reach the model;
  - the contract's example request works, and its hints arrive as hotwords;
  - 8 kHz, 44.1 kHz, stereo, 8-bit and non-WAV audio each get `400`, and malformed bodies get `400`;
  - each missing model file is a reason, with no load attempted, and requests then get `503`;
  - losing the GPU (CUDA out of memory) gives `503` and marks the job not ready;
  - the time-taken figure is right, and nothing is written to disk;
  - the offline environment equals the NUC's `_OFFLINE_ENV`, and the import comes after `enforce_offline()`.
- **Checks:**
  - `pytest server/tests tests/server`: 155 passed.
  - `make typecheck`: no issues in 183 files.
  - `ruff check`: passes. `ruff format --check`: 205 files already formatted.
  - `make test`: 1599 passed and 15 failed. These are the same 15 as before this step: 13 tests that open sockets the sandbox refuses, plus the two committed-config tests.
  - `make lint` still exits non-zero, only because ruff cannot read `.claude/loop.md`.

**Things to know about this step:**
- **I changed a file step 4 doesn't name.** In `server/src/autobot_server/__main__.py`, the `stt` job now runs `WhisperJob` and the server serves `/v1/stt` through `App(endpoints=…)`. Routing and chat still say they have no model yet. Without this change the endpoint would exist only in tests; the step 3 report said this change would be needed.
- **I fixed the language to English** (`LANGUAGE = "en"`) instead of letting the model detect it. Short commands are too little audio to detect a language reliably, and the NUC's `tiny.en` is English-only too. The reply still reports the language the model gives back.
- **The vocabulary file is not required.** The large-v3 family ships `vocabulary.json` rather than `vocabulary.txt`, so the server checks only `model.bin`, `config.json` and `tokenizer.json`. Step 6's fetch decides the exact files.
- **Constraints:** nothing on the NUC calls the server yet. Per the plan, the NUC sending audio here waits for the `CN-1` amendment (step 7 and "Changes needed elsewhere").

### 5 to 12: not started
Outside this run's scope.

## Files changed

- `server/src/autobot_server/stt.py`: new; the `stt` engine, WAV decoding, hint wording and the `/v1/stt` endpoint.
- `server/tests/test_stt.py`: new; the step's tests, on a fake speech model.
- `server/src/autobot_server/__main__.py`: the server now uses the real `stt` engine and serves `/v1/stt`; docstring updated to match.

## For you to do

- On `GAMEON1`, once steps 6's `make server-fetch-models` and `server-check` exist, confirm the real CUDA transcription on the fixture.
- Run `make test` and `make lint` outside the sandbox to clear the socket tests and the `.claude/loop.md` read.
