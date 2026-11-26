---
run: schiele/0038
at: 2026-11-26T17:10:30.762Z
mode: revise
agent: revise
model: claude:claude-opus-5-5
target: docs/specs/active/autobot-wake-word-training-studio-SPEC.md
template: revise
prompt-sha: ab7a731fc645
template-sha: 80089d1722f6
commit: fb9bd92
attachments: []
read:
  - AGENTS.md
  - docs/specs/active/autobot-wake-word-training-studio-SPEC.md
outcome: success
usage:
  input: 26941
  output: 13322
  peak: 26941
  window: 1000000
cost: 0
---

I reviewed the plan that was written from this spec, and several of its problems trace back to rules in the spec itself, so I want to fix them here first and then regenerate the plan from the revised spec. The core issue: the real-set evaluation as specified is too small and too noisy to support the install decision TS-8 asks it to make. Please revise the following rules, keeping each rule's "Reason:" up to date, and record what you settle in "Settled without asking" as usual.

1. TS-6, the held-out split. Today it's one clip in five by a stable hash of the clip's file name. Two problems. First, re-recorded takes of the same prompt get different file names, so one take can be trained on while a near-duplicate take is held out, which inflates recall. Second, ambient is only five clips, so hashing by file name holds out zero, one or two minutes of ambient depending on luck, and with zero the false-accept figure doesn't exist at all. Change it so that:
   - positives and negatives are split by a stable hash of speaker + prompt id, so every take of a prompt lands on the same side (monitor clips, which have no script prompt, can keep a per-clip hash);
   - ambient audio is split by time, with a fixed share of every ambient recording (e.g. the last fifth of each, or fixed-length slices assigned by hash) held out, so held-out ambient always exists and grows with the dataset.
   The split must still never change between runs for a given clip or slice.

2. TS-3, the ambient amount. Five minutes can't measure a target of one false accept per hour. Keep five minutes as the floor that unlocks the first training run, but add a longer hands-off ambient prompt (30–60 minutes, "leave it running") that the session offers. The interface should say plainly when there is too little held-out ambient to judge false accepts per hour, rather than showing a number that means nothing.

3. TS-8, the install gate. "Recall no lower and false accepts no higher" will flip on noise when the held-out set is about eleven positives. Add:
   - a minimum held-out size (positives, negatives, ambient minutes) below which the interface shows the figures but gives no verdict and lets me install on my own judgment, with a warning;
   - a tie rule (equal on both measures counts as "at least as good");
   - a tolerance, so a difference of a single held-out clip isn't treated as worse. Pick sensible values, or leave them as Open Questions if you aren't sure.

4. TS-2, the five states. My NUC today has a synthetic-only models/wakeword/autobot.onnx from the earlier runs, but the fresh-install kind becomes text_match. That combination (text_match configured, a model file present) isn't one of the five states. Define it. My intent: a model file that isn't configured as kind = "openwakeword" is a leftover, the state is *untrained*, and it doesn't count as "installed" for the TS-8 comparison. The first real candidate therefore has nothing to beat. The leftover file should be kept, not deleted, so it can be compared by hand.

5. TS-9, the monitor buffer (use your judgment). A fixed 10 s wall-clock buffer means a slow press of "missed" can scroll past the moment. Consider having the listener keep the audio of the last few speech stretches (with their pre-roll) instead of, or as well as, the last ten seconds, so a mark captures the utterance it's about. Whatever you choose, the listener still writes nothing unless a mark asks for it.

Don't change anything else. In particular, keep TS-1, TS-10 to TS-15 and the "never leaves the NUC" rule as they are. Update "Changes needed elsewhere" if any of this adds a new item (e.g. SCHEMA.md for the split or the new state). After this I'll run a full revise of the plan, so you don't need to say anything about the plan here.
