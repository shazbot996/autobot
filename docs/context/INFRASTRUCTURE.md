---
type: infrastructure
title: "autobot — Infrastructure"
authority: descriptive
status: active
provenance:
  - run: schiele/0017
    at: 2026-11-25T23:01:23.211Z
    mode: revise
---

# autobot — Infrastructure

This document records the environments and accounts autobot runs in and talks to, with their identifiers. The NUC's details were read from the machine during the headless wake-word prototype build, which ran on it. The Google Cloud side is still what the owner described in the Smart Start intake (Q2, Q4 and Q5): nothing is provisioned there. An identifier that is not known is an open question, not a guess.

## One section per environment or account: what exists there, with its identifiers

### Production host: Intel NUC

- A dedicated Intel NUC, sitting beside the owner and on 24x7. Hostname `branch`. Ubuntu 24.04. Intel Core i7-10710U (12 threads), 31 GB of memory, no GPU.
- autobot is designed for this machine alone, and builds are run on it. `config/autobot.toml` names it in `host.nuc_hostname`; the listener, `make audio-check` and the service installer refuse to run on a machine with any other hostname.
- Microphone: a USB "AIRHUG 21", which captures only and has no playback side. Speaker: the built-in analog output (HDA Intel PCH, ALC256).
- Audio is opened at 16 kHz mono through the `pipewire` device, not through the hardware devices, which reject 16 kHz; PipeWire resamples. Which microphone and speaker `pipewire` means is PipeWire's default source and sink, set with `wpctl set-default` or from the Audio screen of the terminal user interface.
- Runs the always-on listener today, as the systemd user unit `autobot-listener.service`, installed to `~/.config/systemd/user/` and enabled, with lingering on for the owner's user so that it starts at boot with nobody logged in. Its runtime state (pid file, log, socket) is under `run/` in the repository checkout.
- Holds the model files under `models/` in the checkout: the Silero VAD model, the faster-whisper `tiny.en` model, the piper voices, openWakeWord's feature models and the trained `autobot` wake-word model. They are not committed. `make fetch-models` downloads the ones that are published; `make train-wakeword` trains the wake-word model on this machine's CPU, which takes hours, and keeps the voices, data sets and cached work it trains from under `models/wakeword/` (about 35 GB).
- Is intended to run the rest as it is built: local STT and TTS for queries and responses, the rules layer, the agent host, the wall UI (probably full-screen Chrome on the same machine), and the local store of agent state.

### Test environment: WSL on the Windows 11 dev server

- The owner's Windows 11 dev server has a WSL layer, but autobot does not support it. Support is limited to the Intel NUC, and testing happens locally on the NUC.

### Google Cloud: `cambiumplatform.com` organization

- The owner has a Google Cloud organization, `cambiumplatform.com`, and intends to create a project in it to serve the Gemini models that the agents, built with Gemini ADK, call.
- Identity: the owner, `charles.schiele@gmail.com`, using the gcloud tools and Cloud Identity.
- The project does not exist yet and has not been built; it will be created soon. No project id, billing account, region or service account was given.
- Authentication while testing locally uses Application Default Credentials, which keeps configuration simple. On deployment the same default-credentials approach applies, but Cloud Run authenticates as a dedicated service account rather than the default Compute Engine account.
- Agent state and memory are not stored here; they are persisted on the NUC (ADR 0002). Speech is never processed here (`CN-1`, `CN-2`).

## What defines it

Two files define what runs on the NUC:

- `config/autobot.toml` is the listener's configuration: the NUC's hostname, the audio devices, the wake phrases and the acknowledgement. `SCHEMA.md` gives its fields.
- `deploy/autobot-listener.service` is the template of the systemd user unit. `python -m autobot.listener.control install`, which the Service screen of the terminal user interface runs, replaces `@REPO_ROOT@` in it with the checkout's path and writes the result to `~/.config/systemd/user/autobot-listener.service`. It is not copied by hand.

`pyproject.toml` defines the Python package and its dependencies, and `make setup` builds the `.venv` the unit runs from. `make fetch-models` fetches the model files into `models/`, and `make train-wakeword` trains the wake-word model there. The rest of the NUC is set up by hand: the operating system, PipeWire and its default source and sink, and lingering. Nothing defines the Google Cloud side: there is no infrastructure-as-code and no deployment configuration, and the project will be created by the owner with the gcloud tools. When any of that is written down as code or configuration, this section names it.

## Open Questions

- What is the id of the Google Cloud project for the Gemini agents, and in which region will the models be served?
  The project has not been created yet; the owner expects to create it soon.
- Which Gemini model or models will the agents use?
  Not decided. The owner will test candidate models for speed and accuracy, depending on the task.
- What is the name of the dedicated service account that Cloud Run authenticates as, and what does Cloud Run run, given that the agent host and its state live on the NUC?
- The trained wake-word model exists only under the git-ignored `models/` on the NUC. Should it, or its training caches, be backed up anywhere? Retraining from nothing takes most of a night.
