---
type: constraints
title: "autobot — Constraints"
authority: prescriptive
status: active
---

# autobot — Constraints

Constraints are the non-negotiable rules of autobot. They are fewer and harder than conventions: a convention says how something is usually done, a constraint says what must never happen. They outrank every spec and plan; when one conflicts with a constraint, the constraint wins and the plan is what changes. A build run that would break a constraint stops and says so rather than working around it. The constraints below are exactly those the owner stated in the Smart Start intake (Q6). Specs, plans and code comments cite them by number.

## One section per constraint: the rule, why it holds, and how a violation would be noticed

### CN-1: Speech-to-text and wake-word modeling are processed locally, never by a cloud service

**Rule.** All speech-to-text and all wake-word detection and modeling run on the local machine. No audio, and no feature derived from audio for the purpose of recognising speech or wake phrases, is sent to any cloud service, hosted API or remote model for that purpose.

**Why it holds.** The owner stated it as a hard rule. The listener is always on, so cloud processing would be paid for continuously and would stream the sound of the house off the premises. Keeping it local is the cost model and the privacy model of the project.

**How a violation would be noticed.** Code review of every dependency and network call in the listener and speech-to-text path: any import of or request to a speech or audio API outside the local machine is a violation. Tests for the listener and STT run with no network available and must pass. A dependency audit that finds a cloud speech SDK in the project definition is a violation.

### CN-2: Text-to-speech is processed locally, never by a cloud service

**Rule.** All text-to-speech runs on the local machine. No response text is sent to any cloud service, hosted API or remote model in order to be turned into audio.

**Why it holds.** The owner stated it as a hard rule, the same as for speech-to-text. Local synthesis keeps response latency low, costs nothing per utterance, and keeps what the assistant says inside the house.

**How a violation would be noticed.** Code review of every dependency and network call in the text-to-speech path: any request to a speech synthesis API outside the local machine is a violation. Tests for TTS run with no network available and must pass. A dependency audit that finds a cloud TTS SDK in the project definition is a violation.

## Open Questions

- The owner's intake also makes local persistence of agent state a core purpose (Q4) and names cost as the reason the listener is local (Q1). Neither was stated under Q6, so they are recorded as decisions (ADR 0002) and in the brief, not as constraints. Should either become a constraint?
- Does CN-1 permit sending the finished text transcript to the cloud for agent reasoning? The intake implies yes (Q5 connects to a Gemini agent in GCP), but Q6 does not say so in words.
