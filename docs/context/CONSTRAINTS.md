---
type: constraints
title: "autobot — Constraints"
authority: prescriptive
status: active
provenance:
  - written: schiele/gpu-inference-server-host-on-wsl
    at: 2026-11-27T20:49:06Z
    mode: revise
  - run: schiele/0147
    at: 2026-11-28T13:40:52.433Z
    mode: revise
---

# autobot — Constraints

Constraints are the non-negotiable rules of autobot. They are fewer and harder than conventions: a convention says how something is usually done, a constraint says what must never happen. They outrank every spec and plan; when one conflicts with a constraint, the constraint wins and the plan is what changes. A build run that would break a constraint stops and says so rather than working around it. The constraints below are exactly those the owner stated in the Smart Start intake (Q6), as amended on purpose by the owner since. Specs, plans and code comments cite them by number.

*Revised 2026-11-28: CN-1 amended by the owner. After the wake word fires, the NUC may send that utterance's audio to the inference server on `GAMEON1` over the LAN for speech-to-text. Audio before the wake word never leaves the NUC, and no audio ever goes to a cloud service. CN-2 is unchanged.*

## One section per constraint: the rule, why it holds, and how a violation would be noticed

### CN-1: Speech-to-text and wake-word modeling stay inside the house, never in a cloud service

**Rule.** All speech-to-text and all wake-word detection and modeling run on the owner's own machines on the house LAN, and never on a cloud service, hosted API or remote model outside the house. Within that, three limits hold:

1. **Wake-word detection and modeling run on the NUC only.** The wake path never waits on the network and never touches the inference server (IS-15).
2. **Audio captured before the wake word fires never leaves the NUC.** Nothing of it is sent anywhere, by any path.
3. **After the wake word fires, the NUC may send that utterance's audio over the LAN to `/v1/stt` on `GAMEON1`, the inference server, for transcription.** It is sent to no other host. `GAMEON1` holds that audio only in memory for the length of the request, and never logs it, writes it to disk or forwards it (IS-6, TS-5). When the server is away, the NUC transcribes with its local faster-whisper `tiny.en` (IS-4).

No audio, and no feature derived from audio for the purpose of recognising speech or wake phrases, is ever sent to a cloud service, hosted API or remote model outside the house. The owner's recordings in `data/voice/` stay on the NUC (TS-5).

**Why it holds.** The owner stated the original rule as a hard rule. The listener is always on, so cloud processing would be paid for continuously and would stream the sound of the house off the premises. Keeping it inside the house is the cost model and the privacy model of the project. The owner amended it so that a larger speech model on `GAMEON1`, a machine of his own on his own LAN, can transcribe commands better than the NUC's small model, which is the weakest link of routing today. Only the audio of a deliberate utterance after a wake crosses the LAN, and the always-on sound of the house before a wake still never leaves the NUC.

**How a violation would be noticed.** Code review of every dependency and network call in the listener and speech-to-text path:
- any import of or request to a speech or audio API outside the house is a violation;
- the only network destination for audio is the configured address of the inference server (`[server]` in `config/autobot.toml`), a private LAN address;
- any send of audio from the wake path, or of audio captured before the wake fired, is a violation. The test for the wake path runs with the server client absent and must pass;
- any write of audio, features or transcripts to disk, or any log line carrying them, in the server's `/v1/stt` handler is a violation (IS-6). The server's tests check that nothing is written.

Tests for the listener and STT run with no network available and must pass. A dependency audit that finds a cloud speech SDK in the project definition, in either package, is a violation.

### CN-2: Text-to-speech is processed locally, never by a cloud service

**Rule.** All text-to-speech runs on the local machine. No response text is sent to any cloud service, hosted API or remote model in order to be turned into audio.

**Why it holds.** The owner stated it as a hard rule, the same as for speech-to-text. Local synthesis keeps response latency low, costs nothing per utterance, and keeps what the assistant says inside the house.

**How a violation would be noticed.** Code review of every dependency and network call in the text-to-speech path: any request to a speech synthesis API outside the local machine is a violation. Tests for TTS run with no network available and must pass. A dependency audit that finds a cloud TTS SDK in the project definition is a violation.

## Changes needed elsewhere

- **docs/decisions/0007-home-models-beside-gemini-ADR.md (new).** "Home models beside Gemini". Context: the NUC's `tiny.en` and keyword rules are a weak front end, and the owner has a GPU on `GAMEON1`. Decision: a home model on `GAMEON1` serves routing, chat and speech-to-text, with Gemini kept as the handoff for hard requests, as an addition and not a replacement. Each agent definition names its model (`none`, `local` or `gemini`) and an ordered fallback (IS-18), and AS-1 stays true for every agent whose model is `gemini`. Post-wake utterance audio now goes to `GAMEON1` under the amended `CN-1`; pre-wake audio and the wake path stay on the NUC, and no audio goes to a cloud service. Alternatives: speech-to-text on the NUC only, a cloud speech service (forbidden by `CN-1`), and Gemini alone. Consequences: the NUC falls back to `tiny.en` and tiers 1 and 2 when the server is away. It should also say that ADR 0001 and ADR 0003 are not edited in place.
- **docs/specs/active/autobot-gpu-inference-server-SPEC.md.** IS-8's "Takes effect once `CONSTRAINTS.md` carries the amendment to `CN-1`" now holds, so the condition is dropped, and the summary's "IS-8 takes effect once..." sentence goes with it. IS-6's reason and IS-7's reason cite the old wording of `CN-1` and `CN-2`: IS-6 states that post-wake audio is sent here under `CN-1` item 3. Its "Changes needed elsewhere" entry for `CONSTRAINTS.md` is done and goes. The open question on whether the finished transcript may go to the cloud for reasoning stays open here.
- **config/autobot.toml.** `[server] stt_enabled` stays `false` until the owner turns it on; it was waiting for this amendment.
- **docs/context/CONVENTIONS.md.** "Local-only speech" becomes speech kept inside the house and off any cloud, and says that only post-wake utterance audio may go to `GAMEON1`.
- **AGENTS.md.** "A change to the listener's speech path keeps it offline" becomes "keeps it inside the house and off any cloud". The CN-1/CN-2 sentence in the context map and the "Never introduce a cloud service" line are still true as written.
- **docs/context/INFRASTRUCTURE.md.** Record that server speech-to-text is permitted by `CN-1`, and that it is switched on by `[server] stt_enabled`.
- **docs/context/ARCH.md and docs/context/GLOSSARY.md.** The listener-to-server seam is the post-wake utterance only. Add the "edge layer", "home layer" and "cloud layer" terms where the glossary is updated.
- **docs/specs/active/wake-word-listener-SPEC.md, rules-first-routing-SPEC.md and agent-sessions-SPEC.md.** Any rule that says speech-to-text runs "on the local machine" or "offline" reads "inside the house". The listener spec's wake path is unchanged.

## Settled without asking

- **Resolved.** The transcript says "stream" the utterance's audio. IS-8 sends the whole utterance as one WAV request and does not stream. CN-1 says "send" and leaves the transfer shape to IS-8.
- **Resolved.** CN-2 is not affected: the new rule covers speech-to-text only, and text-to-speech stays on the NUC's piper (IS-10). CN-2 is unchanged.
- **Derived (IS-15).** The server transcribes only utterances inside a session, after a wake: the command, the query and the answers to confirmations. CN-1 item 3 follows that.
- **Derived (IS-6, TS-5).** The `GAMEON1` handling of audio (memory only, never logged or written) is stated in CN-1 as those rules already state it, and they stay intact.
- **Resolved.** The earlier open question on speech-to-text on another house machine is answered by CN-1 and removed. The same question for text-to-speech remains, narrowed below.

## Open Questions

- The owner's intake also makes local persistence of agent state a core purpose (Q4) and names cost as the reason the listener is local (Q1). Neither was stated under Q6, so they are recorded as decisions (ADR 0002) and in the brief, not as constraints. Should either become a constraint?
- Does CN-1 permit sending the finished text transcript to the cloud for agent reasoning? The intake implies yes (Q5 connects to a Gemini agent in GCP), but Q6 does not say so in words.
- Does CN-2 allow text-to-speech on `GAMEON1`? The inference server spec reserves `/v1/tts` for a later revision (IS-10). CN-2 still says "on the local machine", so it stays NUC-only until the owner amends it on purpose. Recommended: amend it the way CN-1 was, with response text only, when a server voice is measured to sound better.
- When the owner speaks the wake phrase and a command in one utterance (possible with `kind = "text_match"`), does that utterance's audio count as before the wake word fires? Recommended: yes, it stays on the NUC and is transcribed by `tiny.en`, because that is the reading under which the rule can be checked, and the session then asks for the command by voice.
