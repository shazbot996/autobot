---
run: schiele/0018
at: 2026-11-25T23:00:46.376Z
mode: answer
agent: answer
model: claude:claude-sonnet-5-5
target: docs/context/INFRASTRUCTURE.md
template: answer
prompt-sha: 9e22e31393a9
template-sha: 90976540f104
commit: 0228e0e+
attachments: []
read:
  - AGENTS.md
  - docs/context/INFRASTRUCTURE.md
outcome: success
usage:
  input: 11145
  output: 2108
  peak: 11145
  window: 1000000
cost: 0
---

---
type: infrastructure
title: "autobot — Infrastructure"
authority: descriptive
status: active
---

# autobot — Infrastructure

This document records the environments and accounts autobot runs in and talks to, with the identifiers the Smart Start intake gave. Nothing is provisioned or defined in code yet; this is what the owner described in Q2, Q4 and Q5. An identifier the intake did not give is an open question, not a guess.

## One section per environment or account: what exists there, with its identifiers

### Production host: Intel NUC

- A dedicated Intel NUC running Ubuntu, sitting beside the owner and on 24x7.
- Has local audio hardware (microphone and speaker) for the listener and TTS.
- Runs the always-on listener, local STT and TTS, the rules layer, the agent host, the wall UI (probably full-screen Chrome on the same machine), and the local store of agent state.
- autobot is designed for this machine alone. Builds are run from the host itself, and the eventual plan supplies the hostname and the other host details when it runs.
- No hostname, Ubuntu version or hardware details are recorded yet.

### Test environment: WSL on the Windows 11 dev server

- The owner's Windows 11 dev server has a WSL layer, but autobot does not support it. Support is limited to the Intel NUC, and testing happens locally on the NUC.

### Google Cloud: `cambiumplatform.com` organization

- The owner has a Google Cloud organization, `cambiumplatform.com`, and intends to create a project in it to serve the Gemini models that the agents, built with Gemini ADK, call.
- Identity: the owner, `charles.schiele@gmail.com`, using the gcloud tools and Cloud Identity.
- The project does not exist yet and has not been built; it will be created soon. No project id, billing account, region or service account was given.
- Authentication while testing locally uses Application Default Credentials, which keeps configuration simple. On deployment the same default-credentials approach applies, but Cloud Run authenticates as a dedicated service account rather than the default Compute Engine account.
- Agent state and memory are not stored here; they are persisted on the NUC (ADR 0002). Speech is never processed here (`CN-1`, `CN-2`).

## What defines it

Nothing defines these environments yet. There is no infrastructure-as-code, no provisioning script and no deployment configuration. The NUC is set up by hand, and the Google Cloud project will be created by the owner with the gcloud tools. When any of that is written down as code or configuration, this section names it.

## Changes needed elsewhere

- **README.md.** It lists the WSL test machine among the environments `INFRASTRUCTURE.md` covers; WSL is not supported, so that reference needs to go.

## Open Questions

- What is the id of the Google Cloud project for the Gemini agents, and in which region will the models be served?
  The project has not been created yet; the owner expects to create it soon.
- Which Gemini model or models will the agents use?
  Not decided. The owner will test candidate models for speed and accuracy, depending on the task.
- What is the name of the dedicated service account that Cloud Run authenticates as, and what does Cloud Run run, given that the agent host and its state live on the NUC?
- What are the NUC's hostname, Ubuntu version and audio devices?
  Not yet known. The builds run from the host, so the eventual plan will supply the hostname and the other host details when it runs.
