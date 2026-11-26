---
type: spec
title: "autobot — Wake-Word Listener"
authority: prescriptive
status: active
---

# autobot — Wake-Word Listener

The wake-word listener is the always-on local process that reads the microphone on the NUC, detects configurable wake phrases, and hands the speech that follows, together with the target the phrase selects, to local speech-to-text. This stub records what the Smart Start intake supports about it; everything else is an open question. It governs the **Wake-word listener** seam in `ARCH.md`.

## Numbered rules, each with its reason

1. **WW-1.** The listener runs continuously as a local process on the host and detects wake phrases without sending audio off the machine. Reason: the owner wants a "hey google"-like always-on agent, and runs the listener locally "for pure cost purposes"; `CN-1` forbids cloud wake-word processing.
2. **WW-2.** Wake phrases are configurable: the set of phrases the listener watches for is read from configuration, not fixed in code. Reason: the owner requires configurable wake phrases, and each agent is served from its own configured wake words or activation phrases.
3. **WW-3.** The listener can be configured to recognise new kinds of wake phrases, not only new instances of one kind. Reason: the owner asks for it to be "configurable to make more kinds of wake phrases".
4. **WW-4.** On detecting a wake phrase, the listener captures the speech that follows and passes the capture, with the target that phrase is configured for, to local speech-to-text. Reason: the wake phrase selects which agent or rule set the query is for, and transcription must be local (`CN-1`).

## Open Questions

- Which local wake-word engine is used, and does it support configuring new kinds of phrases (WW-3)?
- How does the listener decide when the speech after a wake phrase has ended?
- What does a wake phrase "kind" mean concretely: a trained model, a text phrase, a phonetic pattern?
- How does the listener behave on the WSL test machine if no microphone is reachable?
- Does the listener give any audible or on-screen acknowledgement when it hears a wake phrase?
