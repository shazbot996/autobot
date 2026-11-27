---
run: schiele/0046
at: 2026-11-27T04:44:51.994Z
mode: direct
agent: direct:ask
model: claude:claude-fable-5-1
target: null
template: direct/ask
prompt-sha: 7b319b359dd4
template-sha: 44af0dfef32e
commit: "9105959"
attachments: []
read:
  - AGENTS.md
  - config/autobot.toml
  - src/autobot/wakeword/install.py
  - src/autobot/tui/state.py
  - src/autobot/tui/config_edit.py
  - src/autobot/listener/engines/__init__.py
  - src/autobot/tui/menu.py
  - src/autobot/tui/app.py
  - docs/decisions/0006-full-screen-tui-and-training-on-the-owners-voice-ADR.md
  - src/autobot/wakeword/evaluate.py
  - src/autobot/tui/screens/wake_phrase.py
  - src/autobot/tui/screens/candidate.py
outcome: success
usage:
  input: 221492
  output: 3581
  peak: 64332
  window: 1000000
cost: 0
harness:
  provider: claude
  kind: sdk
  workspace: self
  access: read
  turns: 18
  sandbox: false
---

Yes. That is how it is built, and the documents say so in those terms. Here is the evidence, part by part, plus the two places where the build is narrower than your description.

## Out of the box: a fallback that needs no training

`config/autobot.toml` ships with `kind = "text_match"`, `model = ""`, `threshold = 0.5`, and the comment on the line says why: *"The fallback: it needs no trained model, but transcribes all speech it hears"* and *"A fresh install starts on 'text_match': no trained model is installed yet."* The engine is `src/autobot/listener/engines/text_match.py`: faster-whisper `tiny.en` transcribes every stretch of speech and fires when the transcript contains the phrase. That is your "CPU expensive but works okay" default, and ADR 0006 (Decision, last paragraph) keeps it as "the bootstrap and fallback kind".

## The studio is self-contained and configures the model

The TUI is a front for `config/autobot.toml` and nothing else (`src/autobot/tui/config_edit.py`: `ConfigStore.update` edits the file in place, keeps comments, verifies the edit, writes atomically). The whole configuration of the wake-word engine is reachable from it:

- **Wake phrase screen** (`src/autobot/tui/screens/wake_phrase.py`): `choose_kind` lets you pick any of the three kinds (`text_match`, `openwakeword`, `sherpa_kws`) from a dialog and writes `kind` at once; `edit_threshold`/`nudge_threshold` write `threshold`; `edit_model` writes `model`; "Train the wake-word model" opens `TrainScreen`, which runs `make train-wakeword` as a `BackgroundJob` and tails its log. If you choose `openwakeword` without a model file, it warns: "train it first, or the listener cannot start" (`warn_if_model_missing`).
- **Recording session, Dataset, Live monitor** collect the training phrases (`screens/session.py`, `dataset.py`, `monitor.py`); the monitor turns real use into labelled clips, so the dataset grows even if you never run a formal session.
- **Candidate screen** (`screens/candidate.py`) is the refinement workflow: it scores the candidate and the installed model on the held-out real recordings, puts the `sherpa_kws` baseline beside them "to tell whether training is paying off" (TS-14), and offers install only when the candidate is at least as good (`OFFER_INSTALL` / `OFFER_JUDGMENT` / `OFFER_NONE`). Installing goes through `src/autobot/wakeword/install.py`: `install_candidate` moves `candidate.onnx` into place, keeps the old model as `<name>.prev.onnx`, and writes `kind = "openwakeword"`, `model` and `threshold` to the config. `revert` swaps back. Then it offers to restart the listener.

So the path you want is exactly the designed path: start on `text_match` → record → train → compare → install → (revert if worse). Training installs nothing; enabling the trained model is an explicit act in the interface (ADR 0006, "Let the training install its result" rejected).

## Two ways the build is narrower than your framing

1. **There are three ways to run, not "a few".** `text_match` (no training, whisper, expensive), `sherpa_kws` (no training, sherpa-onnx keyword spotter, cheap, ~87/140 on synthetic voices), and `openwakeword` (trained). All three are selectable from the Wake phrase screen, so you can also try `sherpa_kws` as a cheaper untrained option without training anything. That is the full set; there is no fourth.

2. **The main screen frames the untrained kinds as a stage, not a choice.** `src/autobot/tui/state.py` reports *untrained* whenever `kind != "openwakeword"`, with the sentence "no trained model is installed." Running on `text_match` by preference is fully supported by the code and the config, but the TUI's badge will read UNTRAINED (warning colour) indefinitely. That is cosmetic, and it is the one place where the built artifact assumes the fallback is temporary rather than a legitimate steady state. If you want the studio to present `text_match` as a deliberate configuration, that is a wording/state change in `state.py` and TS-2, not a structural one.

Everything else matches your goal as stated.
