---
saved: schiele/0001
user: schiele
at: 2026-11-25T21:43:21.947Z
---

```smartstart-intake
Q1. What are you building?
pwa that locally processes an always on audio input from microphone looking for wake keywords, with some logic to validate and construct interactive sessions with one or more persistent AI agents using gemini adk.  The goal is to have an agent like "hey google" for my own use, with configurable wake phrases, and configurable to make more kinds of wake phrases.  The main listener needs  to be a local process, for pure cost purposes, and we validate queries via classic rules application to feed the kinds of AI calls we will build it to support.  Rules-first AI agents.  Simple things like making a rule to access a persistent list making agent that has a persistent memory, etc.  I want it to be two way - speech to text, and it responds with text to speech with a configurable voice - i have had good luck with faster-whisper for stt, and piper-tts for text to speech - the goal is to create an agent voice interactive interface that we have some rules for locally that can respond using these rules with low latency, and that we can build synthesized procedural set of matching rules over time to create conversational pathways to a set of planned agent tools that will be voice interactive, screen backed - web UI on the wall, but not keyboard and mouse interactive. I want  to build several applications using this modality but haven't planned all the applications yet.

Q2. Who uses it, and how do they reach it?
I will use this just for me at my house - charles.schiele@gmail.com and I will be using this mostly locally but using the gcloud tools as well as cloud identity - I have a google cloud organization called cambiumplatform.com that I can use as well, but we will start with local development, and just me.

Q3. Language and primary framework or SDK
I want a recommendation given the scope.  The always-on listener with a local model sounds right up python's alley.  I don't know what I will do with the interface yet - probably just full screen on chrome running on the local host, itself.  It will run on a PC that sits beside me 24x7.  It would be nice if I could direct it to do things with the desktop, too, but that is a secondary goal to initial intertogatory use.

Q4. Where will it run?
I will probably run this on a dedicated intel nuc running ubuntu, and it should have local audio hardware for use.  I'll be testing on my wsl layer on my windows 11 dev server too.   I can create a gcp project in my google cloud organization, cambiumplatform.com to use for the gemini models that I want to serve the queries.  I want to persist the actual memory for the agents we make locally on the server we create - that is a core purpose of the server - create new agents that serve from configured wake words or activation phrases, and save their state locally.

Q5. What does it talk to?
I want to use adk to connect to a gemini agent in gcp, but keep state locally

Q6. What must never happen?
build with locally processed speech to text and wake word modeling - do not use cloud services for this
same with text to speech - never use cloud services for these functions

Q7. What is already decided, or ruled out?
(not answered)

Q8. Material to paste
(not answered)
```
