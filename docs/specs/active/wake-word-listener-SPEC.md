---
type: spec
title: "autobot — Wake-Word Listener"
authority: prescriptive
status: active
---

# autobot — Wake-Word Listener

The wake-word listener is the always-on local process that reads the microphone on the NUC, detects configurable wake phrases, and hands the speech that follows, together with the target the phrase selects, to local speech-to-text. Rules WW-1 to WW-4 record what the Smart Start intake supports about it. Rules WW-5 to WW-9 record what the headless wake-word prototype settled: the first test wake phrase, detection by containment, what a "kind" is, the spoken acknowledgement, and behaviour off the NUC (ADR 0005). It governs the **Wake-word listener** seam in `ARCH.md`.

## Numbered rules, each with its reason

1. **WW-1.** The listener runs continuously as a local process on the host and detects wake phrases without sending audio off the machine. Reason: the owner wants a "hey google"-like always-on agent, and runs the listener locally "for pure cost purposes"; `CN-1` forbids cloud wake-word processing.
2. **WW-2.** Wake phrases are configurable: the set of phrases the listener watches for is read from configuration, not fixed in code. Reason: the owner requires configurable wake phrases, and each agent is served from its own configured wake words or activation phrases.
3. **WW-3.** The listener can be configured to recognise new kinds of wake phrases, not only new instances of one kind. Reason: the owner asks for it to be "configurable to make more kinds of wake phrases".
4. **WW-4.** On detecting a wake phrase, the listener captures the speech that follows and passes the capture, with the target that phrase is configured for, to local speech-to-text. Reason: the wake phrase selects which agent or rule set the query is for, and transcription must be local (`CN-1`).
5. **WW-5.** The first test wake phrase is `autobot`. Reason: people rarely say the word, so the listener can be tested in an ordinary room without waking by accident.
6. **WW-6.** Detection is by containment: a wake phrase is detected wherever it falls in an utterance, and one utterance of it yields one wake. "Hey autobot" and "yo autobot" both wake the `autobot` phrase. Reason: prefixes such as "hey" and "yo" are how people address an assistant, and they should not each need a configured phrase of their own.
7. **WW-7.** A wake phrase's kind is the detector that implements it, named by the `kind` field of the phrase's configuration. Two kinds exist: `openwakeword`, a model file trained for the phrase, and `text_match`, a test of whether the local transcript of a stretch of speech contains the phrase. A new kind is a new detector behind the same interface. Reason: WW-3 needs a concrete meaning for "kind", and making it the detector lets a new kind be added without changing the rest of the listener (ADR 0005).
8. **WW-8.** When the listener hears a wake phrase whose target is `ack`, it acknowledges aloud, with a short phrase synthesised locally, and does not listen to its own acknowledgement. Reason: the first builds are headless, so there is no screen to show that the phrase was heard; `CN-2` requires the synthesis to be local; and an assistant that hears itself would wake itself.
9. **WW-9.** The listener runs on the NUC only. On any other machine it refuses to start, with a clear message, and its tests run from recorded audio fixtures without a microphone, a speaker or a network connection. Reason: `INFRASTRUCTURE.md` limits support to the NUC, whose local audio hardware is what the listener is built for.

## Open Questions

- How does the listener decide when the speech after a wake phrase has ended? The prototype detects the end of speech only to feed its detectors; capturing the query that follows a wake (WW-4) is not built yet.
- What does a wake do when its target is not `ack`? `ack` is the only target built; targets that name an agent or a rule set wait for the rules-first routing and agent sessions specs.
- Is the acknowledgement still spoken once a wake leads into a captured query, or does it become a shorter tone?
