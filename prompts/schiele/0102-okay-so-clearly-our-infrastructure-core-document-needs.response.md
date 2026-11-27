---
run: schiele/0102
at: 2026-11-27T17:20:35.213Z
mode: revise
agent: revise
model: claude:claude-opus-5-5
target: docs/context/INFRASTRUCTURE.md
template: revise
prompt-sha: 62c769e11174
template-sha: 80089d1722f6
commit: 2c5e495+
attachments: []
read:
  - AGENTS.md
  - docs/context/INFRASTRUCTURE.md
  - docs/specs/active/autobot-google-cloud-configuration-screen-SPEC.md
  - config/autobot.toml
  - src/autobot/gcp/needs.py
outcome: success
usage:
  input: 99384
  output: 7396
  peak: 41206
  window: 1000000
cost: 0
---

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

This document records the environments and accounts autobot runs in and talks to, with their identifiers. The NUC's details were read from the machine during the headless wake-word prototype build and the wake-word training studio build, which both ran on it. The Google Cloud details come from `config/autobot.toml`, the Google Cloud configuration screen spec (`docs/specs/active/autobot-google-cloud-configuration-screen-SPEC.md`) and the code in `src/autobot/gcp/`. The screen was corrected against the real project on 2026-11-27. So far it is used only to configure the project and test the setup; no agent calls Google Cloud yet. An identifier that is not known is an open question, not a guess.

## One section per environment or account: what exists there, with its identifiers

### Production host: Intel NUC

- A dedicated Intel NUC, sitting beside the owner and on 24x7. Hostname `branch`. Ubuntu 24.04. Intel Core i7-10710U (12 threads), 31 GB of memory, no GPU.
- autobot is designed for this machine alone, and builds are run on it. `config/autobot.toml` names it in `host.nuc_hostname`; the listener, `make audio-check`, the service installer and, in the terminal user interface, the recording session, the live monitor and the install of a model refuse to run on a machine with any other hostname.
- Microphone: a USB "AIRHUG 21", which captures only and has no playback side. Speaker: the built-in analog output (HDA Intel PCH, ALC256).
- On 2026-11-26 the AIRHUG 21 was observed delivering digital silence while its software volume and capture switch read normal. The likely cause is the device's own mute; it is not resolved.
- Audio is opened at 16 kHz mono through the `pipewire` device, not through the hardware devices, which reject 16 kHz; PipeWire resamples. Which microphone and speaker `pipewire` means is PipeWire's default source and sink, set with `wpctl set-default` or from the Audio screen of the terminal user interface.
- Runs the always-on listener, as the systemd user unit `autobot-listener.service`, installed to `~/.config/systemd/user/` and enabled, with lingering on for the owner's user so that it starts at boot with nobody logged in. Its runtime state (pid file, log, socket, and a snapshot when the live monitor asks for one) is under `run/` in the repository checkout.
- Runs the application, the full-screen terminal user interface, in a terminal the owner opens with `make launch`. It does not start itself at login.
- Holds the model files under `models/` in the checkout: the Silero VAD model, the faster-whisper `tiny.en` model, the piper voices (the acknowledgement's is `en_US-joe-medium`), openWakeWord's feature models, the sherpa-onnx keyword-spotting model under `models/sherpa-kws/`, and the wake-word models under `models/wakeword/`. They are not committed. `make fetch-models` downloads the ones that are published; `make train-wakeword` trains a wake-word candidate on this machine's CPU, which takes hours the first time and about half an hour once the synthetic speech is cached, and keeps the voices, data sets and cached work it trains from under `models/wakeword/` (about 30 GB).
- No model trained on the owner's voice exists yet. `models/wakeword/autobot.onnx` is the synthetic-only model of the runs before the studio. The configuration names `kind = "text_match"`, so that file is a leftover: kept, and not counted as installed.
- Is where the voice dataset lives: `data/voice/` in the checkout, the owner's recordings and their manifest. It is git-ignored, is never committed and never leaves this machine (TS-5). The directory is created by the first recording; none has been made yet.
- Holds the Google Cloud service account's key, the only credential autobot uses, at `[gcp] credentials_file`, which is `secrets/service-account-autobot-agent.json` in the checkout. `.gitignore` covers `secrets/` and the `service-account*.json` pattern. The key is created by the script on the Key step of the Google Cloud screen, which leaves the directory at `0700` and the file at `0600`, owned by the owner's user. autobot reads only the file's metadata, and Google's sign-in code reads the key itself (GC-7).
- The owner's own `gcloud` login and Application Default Credentials are kept up to date on this host for his shell, so that he can run the commands the Google Cloud screen shows. autobot never uses them (GC-4).
- Holds the scripts the Google Cloud screen writes with `W`, under `run/gcp/` (git-ignored). Today there are three: `service-account.sh`, `secrets.sh` and `quotas.sh`.
- Is intended to run the rest as it is built: local STT and TTS for queries and responses, the rules layer, the agent host, and the local store of agent state.

### Test environment: WSL on the Windows 11 dev server

- The owner's Windows 11 dev server has a WSL layer, but autobot does not support it. Support is limited to the Intel NUC, and testing happens locally on the NUC.

### Google Cloud: `cambiumplatform.com` organization

- **Organization.** `cambiumplatform.com`. The owner owns the organization and its domain.
- **Owner identity.** `charles.schiele@gmail.com`, with the gcloud tools and Cloud Identity. The account is privileged: it is the project's owner and has billing rights. Only the owner, in his own shell, runs the commands that change the project. autobot never does (GC-2).
- **Project.** `schiele-autobot-dev`, the project that serves the Gemini models the agents call. Its id is `[gcp] project` in `config/autobot.toml`. The billing account it is linked to is not recorded here.
- **Region.** `us-east5` (`[gcp] region`). Gemini on Vertex AI is called there, the secrets are replicated there, and regional quotas are compared there.
- **APIs.** The APIs autobot needs enabled in the project, catalogued in `src/autobot/gcp/needs.py` (GC-5): `serviceusage.googleapis.com`, `cloudresourcemanager.googleapis.com`, `iam.googleapis.com`, `secretmanager.googleapis.com`, `aiplatform.googleapis.com`, `weather.googleapis.com`, `apikeys.googleapis.com` and `cloudquotas.googleapis.com`. `storage.googleapis.com` is not needed while no bucket is configured.
- **Service account.** `autobot-agent@schiele-autobot-dev.iam.gserviceaccount.com` (`[gcp] service_account`). autobot and its agents use Google Cloud only as this account, through Application Default Credentials pointed at its key on the NUC. autobot refuses any credentials that resolve to another identity (GC-4). The account's roles are the narrowest set that covers what autobot does (GC-6):
  - On the project: `roles/serviceusage.serviceUsageViewer`, `roles/serviceusage.apiKeysViewer`, `roles/aiplatform.user` and `roles/cloudquotas.viewer`.
  - On each expected secret: `roles/secretmanager.viewer`, `roles/secretmanager.secretAccessor` and `roles/secretmanager.secretVersionAdder`.

  It holds no broad role (owner, editor, IAM, service-usage or billing admin). The Service account step flags any it finds as *too broad*.
- **Key.** One user-managed JSON key for the service account, held on the NUC only (see above). `gcloud iam service-accounts keys list` lists the account's keys.
- **Secret Manager.** One expected secret: `autobot-weather-api-key`, the `weather` agent's API key, with user-managed replication in `us-east5`. The account's three roles are granted on the secret itself, not on the project. Its value goes in only through the screen's masked field or a pipe from one `gcloud` command to another, and is never written anywhere else (GC-9).
- **API key.** `autobot-weather` (display name), restricted to `weather.googleapis.com`. It is the value stored in `autobot-weather-api-key`.
- **Gemini.** Vertex AI (`[gcp] gemini_api = "aiplatform.googleapis.com"`) in `us-east5`, called as the service account. The model is `gemini-3.5-flash-lite` (`[gcp] gemini_model`), and all agents use this one model. The Gemini Developer API is not used.
- **Quota caps.** The Weather API has no quota for the API as a whole, so each of its seven endpoints is capped in the project at 100 requests a day and 10 a minute: current conditions, daily, hourly and minutely forecasts, history, map tiles and public alerts. That makes fourteen `[[gcp.quotas]]` entries, set with `gcloud beta quotas preferences create`. The caps were sized for about 50 weather lookups a day. Gemini on Vertex AI has no cap, by the owner's decision of 2026-11-27: Google's own per-model limit in `us-east5` is already below the intended 10 a minute, so asking for 10 was refused as an increase.
- **Cloud Storage.** No buckets (`[gcp] buckets = []`).
- **Cloud Run.** Nothing runs on Cloud Run. The agents run on the NUC.
- **What checks it.** The Google Cloud screen of the terminal user interface (`make launch`, configuration menu) checks each of the above as the service account, and `R` checks again. Each step also shows read-only `gcloud` commands the owner can run himself. Google Cloud is never contacted by the tests, which run against `tests/gcp/gcp_fakes.py`.
- **What is not here.** Agent state and memory are persisted on the NUC (ADR 0002). Speech is never processed here (`CN-1`, `CN-2`).

## What defines it

Two files define what runs on the NUC:

- `config/autobot.toml` is the one configuration file: the NUC's hostname, the audio devices, the wake phrases, the acknowledgement and the interface's theme. `SCHEMA.md` gives its fields.
- `deploy/autobot-listener.service` is the template of the systemd user unit. `python -m autobot.listener.control install`, which the Service screen of the terminal user interface runs, replaces `@REPO_ROOT@` in it with the checkout's path and writes the result to `~/.config/systemd/user/autobot-listener.service`. It is not copied by hand.

`pyproject.toml` defines the Python package and its dependencies, and `make setup` builds the `.venv` the unit runs from. `make fetch-models` fetches the model files into `models/`, and `make train-wakeword` trains a wake-word candidate there, which becomes the installed model only through the interface. Nothing defines the voice dataset but the recordings themselves: it cannot be rebuilt from the repository. The rest of the NUC is set up by hand: the operating system, PipeWire and its default source and sink, and lingering.

The Google Cloud side has no infrastructure-as-code. Two files declare what it should hold:

- The `[gcp]` table of `config/autobot.toml`, with its `[[gcp.quotas]]` entries, declares the project, the region, the service account, the key's path, the Gemini API and model, the buckets and the intended caps.
- `src/autobot/gcp/needs.py` catalogues the APIs, roles, permissions and expected secrets.

The Google Cloud screen builds the `gcloud` commands from those two files and checks the project against them. The owner runs the commands himself, by hand or from the scripts under `run/gcp/`, so the project's actual state can drift from them. When the Google Cloud side is written down as code, this section names it.

## Settled without asking

- **Resolved.** The earlier text said Cloud Run would authenticate as a dedicated service account. The spec and the code have the agents running on the NUC as `autobot-agent`, through its key. The document now says that nothing runs on Cloud Run, and the question about what Cloud Run runs is gone.
- **Resolved.** The earlier text said autobot uses the owner's Application Default Credentials while testing locally. GC-4 has autobot point Application Default Credentials at the service account's key and refuse any other identity. The owner's own login and default credentials are now recorded as being for his shell only.

## Changes needed elsewhere

- **docs/specs/active/autobot-google-cloud-configuration-screen-SPEC.md.** Drop the `docs/context/INFRASTRUCTURE.md` line from its Changes needed elsewhere, because this document now records the project, region, service account, key path, model, caps and the owner's credentials on the host.

## Open Questions

- **Which resources are in place today.** This document records what the configuration names and the screen checks for. It does not record the screen's readings: whether every API is enabled, every role granted, the key created, the secret holding a version, and the fourteen caps accepted. Recommended: after the next round of checks, mark here anything that is not *ready*, since a descriptive document should say what a person would find.
- **Billing account.** The billing account `schiele-autobot-dev` is linked to is not recorded. Recommended: record its id here (it is an identifier, not a secret), so that the spend on the uncapped Gemini API can be traced.
- The trained wake-word model exists only under the git-ignored `models/` on the NUC. Should it, or its training caches, be backed up anywhere? Retraining from nothing takes most of a night.
- The voice dataset will exist only under the git-ignored `data/voice/` on the NUC, and TS-5 says it never leaves the machine. Is a second copy kept on the NUC itself, and where?
- Why did the AIRHUG 21 deliver digital silence on 2026-11-26 with its software volume and capture switch normal? The device's own mute is suspected and not confirmed.
