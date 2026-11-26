---
type: brief
title: "autobot — Product Brief"
authority: prescriptive
status: active
---

# autobot — Product Brief

autobot is a personal, always-on, voice-first assistant that runs in one person's house: a local listener for configurable wake phrases, local speech-to-text and text-to-speech, a rules layer that validates and routes what was said, and a growing set of persistent AI agents built on Gemini ADK whose memory stays on the local machine. This brief says what it is, who it is for, and what it is not. Every other document builds on it.

## What it is

autobot is an agent like "hey google", built for its owner's own use and under the owner's control. A process on a dedicated PC listens to a microphone all the time, looking for wake keywords. The wake phrases are configurable, and the system is designed so that new kinds of wake phrases can be added over time. The main listener is a local process, deliberately, for cost: nothing is paid per second of listening.

When a wake phrase is heard, the speech that follows is transcribed locally. The transcript is then validated by classic rules before any AI call is made: a rule decides whether the query is understood, what kind of AI call it feeds, and which agent it goes to. This is what the owner calls **rules-first AI agents**. Simple rules can do simple things, such as routing to a persistent list-making agent that remembers its lists. Over time the owner wants to build up a synthesized, procedural set of matching rules that form conversational pathways to a planned set of voice-interactive agent tools. Rules that can answer locally do so with low latency; rules that call an agent connect, through Gemini ADK, to a Gemini agent served from Google Cloud, while the agent's state and memory are persisted locally. Creating new agents that serve from configured wake words or activation phrases, and saving their state locally, is a core purpose of the server.

The interaction is two-way. Speech is turned into text locally, and the response is spoken back with a local text-to-speech engine in a configurable voice. The owner has had good results with faster-whisper for speech-to-text and piper-tts for text-to-speech. The interface is screen-backed: a web UI on the wall shows what the assistant is doing, but it is driven by voice, not by keyboard and mouse. The first use is interrogatory: ask, and be answered. The owner intends to build several applications on this modality; a secondary goal is to direct the assistant to do things with the desktop of the PC it runs on.

## Who it is for

One person: the owner, Charles Schiele (`charles.schiele@gmail.com`), at home. There are no other users. He reaches it mostly locally, by voice, on a PC that sits beside him around the clock, and uses the gcloud tools and Cloud Identity for the cloud side. He has a Google Cloud organization, `cambiumplatform.com`, available for the Gemini project. Development starts local, and starts with just him. When needs conflict, the owner's own experience of low-latency, low-cost, private voice interaction wins.

## What it is not

- It is not a cloud speech product. Speech-to-text, wake-word modeling and text-to-speech are never done by cloud services; see `CONSTRAINTS.md`.
- It is not a multi-tenant or multi-user service. It serves one person in one house, with no account system for anyone else.
- It is not a keyboard-and-mouse application. The wall UI is a display driven by voice; it does not need to be operated by hand.
- It is not an agent whose memory lives in the cloud. The agents reason in Google Cloud through Gemini ADK, but their state is kept on the local server.
- It is not, yet, a desktop automation tool. Directing the desktop is a stated secondary goal, after the initial interrogatory use.
- It is not a cost-per-second listening service. The listener runs locally precisely so that always-on costs nothing beyond the hardware.

## Open Questions

- Which applications will be built on the voice modality after the first interrogatory use? Q1 says several are intended but none are planned.
- What does "directing the desktop" cover, and when does it enter scope? Q1 names it only as a secondary goal.
- Will Cloud Identity be used for anything beyond the owner's own access to the Google Cloud project? Q2 mentions it without a purpose.
