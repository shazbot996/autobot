---
run: schiele/0020
at: 2026-11-25T23:11:05.980Z
mode: answer
agent: answer
model: claude:claude-sonnet-5-5
target: docs/context/ARCH.md
template: answer
prompt-sha: f2216a491e45
template-sha: 90976540f104
commit: 3fb1da8+
attachments: []
read:
  - AGENTS.md
  - docs/context/ARCH.md
outcome: success
usage:
  input: 11468
  output: 2402
  peak: 11468
  window: 1000000
cost: 0
---

Okay, I'm answering questions about the architecture here. I don't have the wake-word engine yet. In fact, I'm not sure. I've answered this in another document. I don't... I think we're trying to build a wake-word engine here. And the system should not be one giant process. I think the... the always-on listening service is definitely going to be a layer, and it needs to be fast and probably compiled somehow from the control plane layer. So there's like a data plane layer where it is running the actual audio process and running the machine learning module model that captures the words. And then the next level is equivalent to dialogflow in Google Cloud, where we manage the rules.

Right now, let's see how much we can build in python itself, and we will phase in how we build a proper user interface.  we will start headless - all voice.

We've eliminated WSL from supports. We're only going to scope this for Ubuntu on the NUC.

Forget Desktop Direction
