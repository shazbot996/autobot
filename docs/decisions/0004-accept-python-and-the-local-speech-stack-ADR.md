---
type: decision
title: "autobot — ADR 0004: Accept Python and the local speech stack"
authority: historical
status: active
---

# autobot — ADR 0004: Accept Python and the local speech stack

This records the acceptance of two recommendations that were still `proposed`: ADR 0001 (Python for the listener and agent host) and ADR 0003 (faster-whisper and piper-tts for local speech). The owner did not accept them in so many words. The owner directed that the headless wake-word prototype plan (`docs/plans/active/headless-wake-word-prototype-PLAN.md`) be executed in full, and that plan is written in Python and uses both engines. Directing the build is the acceptance this ADR records. It also records `mypy` as the static type checker, which ADR 0001 left open.

## Context

ADR 0001 recommended Python as the one language for the listener, the speech bindings, the rules layer and the agent host, and said it stays `proposed` until a later, higher-numbered ADR accepts or rejects it. ADR 0003 recommended faster-whisper for local speech-to-text and piper-tts for local text-to-speech on the same terms. `CONVENTIONS.md` held its Python conventions "pending acceptance of ADR 0001" and asked which static type checker would be used.

The prototype plan could not wait for a separate acceptance step. It builds a Python package under `src/autobot/`, uses faster-whisper (`tiny.en`) inside the bootstrap `text_match` detector, and uses piper-tts for the spoken acknowledgement and for the synthetic training clips of the wake-word model. The plan stated this openly under "Settled without asking" and asked for an ADR to record the acceptance. The owner then asked for the plan to be executed in full, and the code for it now exists.

## Decision

ADR 0001 and ADR 0003 are accepted.

- **Python.** The listener, the terminal user interface and everything built so far are one Python package, `autobot`, under `src/`, defined by `pyproject.toml`, requiring Python 3.12 or later. The parts still to be built (the rules layer and the agent host) are written in Python as well, as ADR 0001 recommended.
- **faster-whisper** is the local speech-to-text engine. Its first use is the `tiny.en` model, on the CPU with int8 weights, inside the `text_match` wake-word detector.
- **piper-tts** is the local text-to-speech engine. Its first uses are the spoken acknowledgement, in the voice named by `[ack].voice` in `config/autobot.toml`, and the synthetic speech the wake-word model is trained on.
- **mypy**, in strict mode, is the static type checker. It is the mainstream, pip-installable default, and `make typecheck` runs it over `src` and `tests`.

ADR 0001 and ADR 0003 themselves are not edited: decisions are historical, and their `status` fields still read `proposed`. This ADR is where their acceptance is recorded.

## Alternatives

- **Wait for an explicit acceptance before building.** Rejected: the owner asked for code, and the plan named the dependency on the two proposed ADRs before it was run.
- **Reject either recommendation.** Not chosen. The owner leaned toward Python and reported good results with both speech engines, and the build has since confirmed that both run locally on the NUC's CPU.
- **`pyright` as the type checker.** Not chosen: it needs Node.js, and `mypy` installs with the other dev extras from `pyproject.toml`.

## Consequences

- The Python conventions in `CONVENTIONS.md` are in force, no longer pending.
- `AGENTS.md` names the real checks an agent runs before reporting: `make test`, `make lint` and `make typecheck`.
- Model files for faster-whisper and for the piper voices are local files under `models/`, which is not committed. Nothing downloads them at run time, and the listener and the tests fail with a clear message when one is missing.
- The wake-word engine, which ADR 0003 left for a later ADR, is decided in ADR 0005.
- The wall UI's language is still open, as ADR 0001 left it.

## Open Questions

- Is `tiny.en` the faster-whisper model for transcribing queries, once the speech after a wake phrase is captured? It was chosen for the bootstrap detector, where it only has to find one word; ADR 0003's question about the model size that balances latency and accuracy for real queries is still open.
- How are the model files under `models/` fetched onto a fresh checkout? Nothing in the repository does it yet.
- In what language is the wall UI written, and is it served by the Python process?
