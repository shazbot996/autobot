---
run: schiele/0028
at: 2026-11-26T16:41:23.305Z
mode: create
agent: create
model: claude:claude-fable-5-1
target: docs/specs/active/autobot-wake-word-training-studio-SPEC.md
template: create
prompt-sha: 4d134a7e6008
template-sha: d7922285ccf1
commit: 0bd3911
attachments: []
read:
  - AGENTS.md
  - prompts/builtin/examples/spec.md
  - docs/context/CONVENTIONS.md
  - docs/context/CONSTRAINTS.md
  - docs/specs/active/wake-word-listener-SPEC.md
  - tests/fixtures/audio/README.md
  - docs/context/GLOSSARY.md
  - docs/context/ARCH.md
  - docs/context/BRIEF.md
  - docs/context/SCHEMA.md
  - docs/context/INFRASTRUCTURE.md
  - docs/decisions/0005-openwakeword-with-a-custom-autobot-model-ADR.md
  - docs/plans/active/headless-wake-word-prototype-PLAN.md
  - src/autobot/tui/screens/audio.py
  - src/autobot/tui/screens/wake_phrase.py
  - src/autobot/tui/audio.py
  - src/autobot/tui/runner.py
  - src/autobot/wakeword/data.py
  - src/autobot/wakeword/train.py
  - config/autobot.toml
  - Makefile
  - .gitignore
  - src/autobot/tui/screens/main.py
  - src/autobot/listener/events.py
  - src/autobot/listener/service.py
  - src/autobot/listener/control.py
outcome: success
usage:
  input: 398804
  output: 13012
  peak: 117191
  window: 1000000
cost: 0
---

Okay, we're not quite heading the right direction. We need some better training, so I'm going to write a new spec here with help of Fable on another planning session who wrote a guideline to alter what we've built so far and focus first on the terminal user interface so we can build and actually train the model. Because we're very close with what we have. We just need to have some recordings of live organic captures to better train the model, and then this will work.

Holy crap, you know what? I just changed my mind, too. I want the entire application to be just a big full-screen terminal user interface. One that fits this PC. I'm going to run Terminal here in X-Windows. I'm going to run the Terminal full screen, and I want this to be an application that operates full-screen, and that this will be its first job. That will focus the architecture a little better, because I don't think we need any interface complexity at all here, and the rest of it's just going to be a waste. So let's capture all of this in a new spec, defining that Terminal application, and then we will write a plan to execute it next.

So let's incorporate the additional scope I mentioned above, as well as the feedback from Fable as far as how to build a terminal user interface to do the training and work with what we already have, because it has seen the code base.

fable said: 

"""
We're changing what the terminal user interface is for. Up to now it has been a control panel: audio devices, threshold, service install. Its first job is now to make the wake-word listener work in this house, by collecting my voice on the NUC and training the model from it. The listener is expected not to work well on a fresh install; the TUI is how it gets trained, and it should walk me through that. I'll sit with it and record.

Write a new spec, docs/specs/active/wake-word-training-studio-SPEC.md, in the same form as wake-word-listener-SPEC.md: frontmatter, an opening paragraph, numbered rules each with a reason, open questions. This is a superseding spec: its opening paragraph names which existing rules and plan steps it overrides. Do not edit wake-word-listener-SPEC.md, the plan, or any ADR in this run; I'll clean those up once the plan from this spec has worked. Do not write code.

Read first: AGENTS.md, docs/context/*.md, docs/decisions/0005-*.md (especially its Open Questions), docs/specs/active/wake-word-listener-SPEC.md, docs/plans/active/headless-wake-word-prototype-PLAN.md, tests/fixtures/audio/README.md, and the existing TUI and training code: src/autobot/tui/screens/audio.py, src/autobot/tui/screens/wake_phrase.py, src/autobot/tui/audio.py, src/autobot/tui/runner.py, src/autobot/wakeword/train.py, src/autobot/wakeword/data.py. The spec should build on what those already do, not replace them.

Why: three head-only retrains on cached features (runs A, B, B2 against run 3) showed that every number we have is for synthetic voices, that the validation stream is too short to rank models at 1 false accept an hour, and that the misses are concentrated in exactly what synthetic data does badly: accents, the bare word, slow renderings. Real recordings of my voice through the AIRHUG, in this room, will tell us more than any further synthetic run, and the TUI already has most of the recording machinery.

Rules the spec must capture, with reasons in your words:

1. Self-training. A fresh install runs the listener on the text_match kind and reports that no trained model is installed. The TUI leads the owner from that state to a trained, installed openwakeword model through a recording session, a training run, an evaluation and an explicit install. The owner can see which state the listener is in at any time.

2. Recording session. The TUI runs a scripted session that prompts the owner, one prompt at a time, and records each response on the NUC through the configured input device. The script covers at least: the bare phrase; the phrase with prefixes ("hey", "yo"); the phrase inside a sentence; slow, fast, quiet and raised delivery; near and far from the microphone. It also collects negatives: sound-alikes (the near-homophone list in data.py), ordinary sentences, and several minutes of hands-off room audio. Every clip is stored with what the prompt asked for, so it is labelled by construction. The session can be stopped and resumed, and a clip can be re-recorded.

3. The voice dataset. Recordings live on disk under a single directory (propose one, e.g. data/voice/), with a manifest that is the only record of what each clip is: prompt text, label (positive/negative/ambient), speaker, device, date. The directory is not committed. The recordings never leave the NUC: they are the owner's voice, and CN-1's reasoning applies to them. Committed fixtures under tests/fixtures/audio/ are a separate, small, hand-picked export, as the fixtures README already describes.

4. Training from the recordings. make train-wakeword uses the voice dataset: real positives and negatives are included in training, with synthetic clips as augmentation rather than the primary source. A held-out split of the real recordings is reserved for evaluation and is never trained on. Which clips went where is recorded with the model's evaluation.

5. The install bar is set on real recordings. A model's evaluation (autobot.json) reports recall and false accepts on the held-out real recordings and on the ambient room audio, separately from any synthetic figures. A candidate is installed only when it beats the installed model on the real set, and only when the owner confirms from the TUI. The previous model is kept so the install can be reverted from the TUI.

6. Live monitor and feedback. A TUI screen shows the running listener's detections and scores as they happen. From it the owner can mark a detection as right or wrong, and can mark that a wake was missed. Each mark captures the audio of that moment (the listener keeps a short rolling buffer for this purpose) into the voice dataset with its label, so the misses and false accepts of real use become training data for the next run. This is what "trains itself" means after the first session.

7. The TUI remains a front for config/autobot.toml and reaches the listener only through its control commands and event stream, per CONVENTIONS.md "Control layers". Recording and training logic lives in modules that do no drawing, so it is testable without a terminal, and the tests use fixtures, never a microphone (CONVENTIONS.md "Testing").

8. All of this runs on the NUC only (WW-9), because the point is to train on the hardware and in the acoustics the listener will run in.

Open questions I want the spec to raise rather than settle: whether a speaker-verifier stage trained on the owner's clips (openWakeWord's custom verifier model) belongs in this spec or a later one; whether an open-vocabulary keyword spotter (sherpa-onnx's KWS, which needs no training) should be added as a third kind, so there is a no-training baseline to compare the trained model against on the same microphone; how a second household voice is added to the dataset; how much real audio is enough before the first training run.

Settle without asking anything CONVENTIONS.md, CONSTRAINTS.md or the existing spec already decides. Where you make a choice the spec leaves open, say so in the rule's reason. Report what you wrote and which existing rules it supersedes, so the next run can write the plan from it.
"""
