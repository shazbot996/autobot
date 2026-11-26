---
type: decision
title: "autobot — ADR 0003: faster-whisper and piper-tts for local speech"
authority: historical
status: proposed
---

# autobot — ADR 0003: faster-whisper and piper-tts for local speech

This is a recommendation made by the Smart Start agent. It follows the owner's stated experience ("I have had good luck with faster-whisper for stt, and piper-tts for text to speech", Q1), but the owner did not state those tools as decided. It stays `proposed` until a later, higher-numbered ADR accepts or rejects it.

## Context

`CN-1` and `CN-2` require that speech-to-text, wake-word modeling and text-to-speech run locally and never use a cloud service. The owner wants low-latency spoken responses in a configurable voice, on an Intel NUC under Ubuntu, and reported good results with faster-whisper for STT and piper-tts for TTS. The intake names no wake-word engine.

## Decision

Recommendation: use faster-whisper as the local speech-to-text engine and piper-tts as the local text-to-speech engine, with the TTS voice selected from piper's voice models by configuration. Both run entirely on the NUC and satisfy `CN-1` and `CN-2`. The wake-word engine is not recommended here because the intake gives no basis for one.

## Alternatives

- **Other local STT engines** (whisper.cpp, Vosk, and the like). Not recommended first because the owner already has working experience with faster-whisper.
- **Other local TTS engines** (Coqui, espeak-ng, and the like). Not recommended first for the same reason regarding piper-tts.
- **Any cloud STT or TTS.** Ruled out by `CN-1` and `CN-2`.

## Consequences

- The STT and TTS parts in `ARCH.md` are bound to these engines once accepted; the glossary carries entries for both.
- Model files for faster-whisper and piper voices live on the NUC and the WSL test machine; where they are stored and how they are fetched is open.
- The configurable voice becomes a piper voice model selection.
- A wake-word engine still has to be chosen, in a later ADR.

## Open Questions

- Does the owner accept these two engines? A later ADR records the answer.
- Which faster-whisper model size balances latency and accuracy on the NUC's hardware?
- Which local wake-word engine is used, and does it support configuring new kinds of wake phrases as Q1 requires?
