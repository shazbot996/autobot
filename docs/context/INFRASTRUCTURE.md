---
type: infrastructure
title: "autobot — Infrastructure"
authority: descriptive
status: active
provenance:
  - run: schiele/0017
    at: 2026-11-25T23:01:23.211Z
    mode: revise
  - run: schiele/0101
    at: 2026-11-27T17:22:03.312Z
    mode: revise
  - written: schiele/gpu-inference-server-host-on-wsl
    at: 2026-11-27T20:49:06Z
    mode: revise
  - written: schiele/gpu-inference-server-plan-steps-5-to-12
    at: 2026-11-27T23:04:42Z
    mode: revise
  - written: schiele/gameon1-bring-up-run
    at: 2026-11-28T00:38:00Z
    mode: revise
---

# autobot — Infrastructure

This document records the environments and accounts autobot runs in and talks to, with their identifiers: the NUC, the GPU inference server `GAMEON1` and the LAN between them, and the Google Cloud project. The NUC's details were read from the machine during the headless wake-word prototype build and the wake-word training studio build, which both ran on it. The Google Cloud details come from `config/autobot.toml`, the Google Cloud configuration screen spec (`docs/specs/active/autobot-google-cloud-configuration-screen-SPEC.md`) and the code in `src/autobot/gcp/`. The screen was corrected against the real project on 2026-11-27. So far it is used only to configure the project and test the setup; no agent calls Google Cloud yet. An identifier that is not known is an open question, not a guess.

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
- Is intended to run the rest as it is built: local STT and TTS for queries and responses, the rules layer, the agent host, and the local store of agent state. Since 2026-11-27 the heavier speech-to-text and the local language model are planned to run on the GPU inference server below, with the NUC keeping its own models as the fallback (IS-4); the NUC stays the front end and the only home of agent state.

### GPU inference server: `GAMEON1` (Windows 11 PC, Ubuntu in WSL2)

The owner's Windows PC, added on 2026-11-27 to be autobot's GPU inference server on the LAN. Its planned role is to serve speech-to-text, a local language model for routing and for the core conversational agent, and possibly text-to-speech, to the NUC over the LAN. The NUC stays the front end. The server's behaviour is specified in `docs/specs/active/autobot-gpu-inference-server-SPEC.md` (IS-1 to IS-14). The server's code is in the repository (`server/`, and `make server-*`), built on the NUC on fakes. **Since 2026-11-27 the server runs on this host**, as the systemd user unit `autobot-server.service`, on port 8300, with speech-to-text, routing and chat all ready ("The server as it runs" below). The NUC has not called it yet: no firewall rule, start-up task or address reservation is known to have been made, so the port is not known to be open to the LAN. "Setting the server up" below is the order it was done in. The hardware and Windows facts below were read from the machine on 2026-11-27 from inside WSL (`wsl.exe --version`, `nvidia-smi`, `lscpu`, `free`, `df`, `ip`, and read-only PowerShell `Get-*` and `powercfg` queries); nothing on the Windows side was changed.

- **Windows.** Windows 11 Home, version 10.0.26200.7623 (build 26200). Computer name `GAMEON1`, in the workgroup `WORKGROUP` (no domain). The Windows user profile is `charl`.
- **WSL.** WSL 3.0.5.0, WSL kernel 6.18.58.1-1 (`6.18.58.1-microsoft-standard-WSL2`), WSLg 1.0.79. One distribution, `Ubuntu-24.04`, WSL version 2, Ubuntu 24.04.5 LTS. Its default user is `schiele` and systemd is on (`/etc/wsl.conf`: `[boot] systemd=true`); `systemctl is-system-running` reads `running`.
- **CPU.** AMD Ryzen 7 5800X3D (the X3D model, not the plain 5800): 8 cores, 16 threads.
- **Memory.** 32 GB in the machine (Windows reports 34,264,817,664 bytes physical). WSL sees 15 GiB and 4 GiB of swap, which is WSL's default share of half the host's memory; `.wslconfig` sets no `memory` limit.
- **Disk.** The WSL root filesystem is a 1007 GB virtual disk with 954 GB free. `C:` (`/mnt/c`) is 931 GB with 849 GB free.
- **GPU.** NVIDIA GeForce RTX 2080 Ti, 11,264 MiB of VRAM (about 307 MiB already taken by the Windows desktop at idle), compute capability 7.5 (Turing), PCIe gen 3. Windows driver 610.88 (`32.0.16.1088`); `nvidia-smi` inside WSL is 610.57.01 and reports CUDA user-mode version 13.3. The GPU is reached from WSL through the driver's `/usr/lib/wsl/lib/libcuda.so`. No CUDA toolkit is installed in WSL (no `/usr/local/cuda`, no `nvcc`). Turing has no bfloat16 and is not supported by FlashAttention 2, which narrows the inference runtimes (IS-10).
- **Python and tools.** Python 3.12.3, the distribution's, with `python3.12-venv`, `python3-pip`, `make` and `node` (v18.19.1) installed by the owner with `apt`, so the `server-*` targets and the cr2 conformance check (`node .agents/skills/cr2-conformance/scripts/check.mjs`) run here. `.venv-server` exists; the NUC's `.venv` does not, so `make test`, `make lint` and `make typecheck` are not run here, and the server's own tests are run with `.venv-server/bin/python -m pytest server/tests` (after `pip install pytest` into it). No `docker` or `uv` is installed.
- **Ollama.** 0.40.19, installed by the owner, as the system unit `ollama.service`, enabled, listening on `127.0.0.1:11434` only, with `OLLAMA_NUM_PARALLEL=1` set in a drop-in. One model is pulled: `qwen3:8b` (5.2 GB on disk).
- **Networking.** Mirrored networking is active: `C:\Users\charl\.wslconfig` holds `[wsl2] networkingMode=mirrored`, and WSL's `eth0` carries the Windows adapter's own address. WSL and Windows therefore share one LAN address and one port space.
  - LAN address `192.168.86.53/24`, gateway `192.168.86.1`, on the Windows adapter `Ethernet` (MAC `A0-36-BC-B8-C2-71`). The address is from **DHCP** (`Dhcp: Enabled`); no reservation is known to exist (see Open Questions). IPv6 unique-local addresses in `fd67:429b:36e0:d20::/64` are also assigned.
  - Hostname inside WSL is `GAMEON1`. The LAN's DNS resolves `gameon1.lan` to `192.168.86.53`.
  - Windows classifies the LAN as a **Public** network. The Windows Firewall is on for all three profiles.
  - The Hyper-V firewall that guards WSL under mirrored mode (VM creator `{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}`) is enabled with `DefaultInboundAction: Block`, `DefaultOutboundAction: Allow`, `LoopbackEnabled: True` and `AllowHostPolicyMerge: True`. Its only WSL-specific inbound rules today allow some ICMP and mDNS; nothing lets TCP into WSL from the LAN.
  - From WSL the NUC answers: `branch.lan` resolves to `192.168.86.200` and replies to ping in about 1 ms. Whether the NUC can reach this host is not tested: the server listens on port 8300, but `make server-ping` has not been run on the NUC.
- **Power.** The active plan is `Balanced`. Sleep after idle and hibernate after idle on AC are both `0` (never). The machine supports S3 standby, hibernate and Fast Startup. Whether its network adapter can wake the machine (Wake-on-LAN) could not be read: `Get-NetAdapterPowerManagement` failed with "A device attached to the system is not functioning".
- **Start-up.** No scheduled task starts WSL. WSL starts only when something on Windows runs `wsl.exe`, so after a reboot nothing of the distribution runs until the owner opens it. Once the distribution starts, the server comes back by itself: after a `wsl.exe --shutdown` on 2026-11-27 and the distribution being opened again, `autobot-server.service` and `ollama.service` started with nobody running a command, and all three jobs were ready within about two minutes.
- **What does not apply here.** No microphone or speaker is used here; the NUC-only refusals (TS-11) stay as they are, because this host's hostname is not `host.nuc_hostname`. The `assistant` project does not exist on this host, so the `camera` agent cannot run here. No voice dataset, Google Cloud key or `gcloud` login exists here, and none is to (IS-6, IS-7).

### The server as it runs on `GAMEON1`

Read from the running server on 2026-11-27, during the bring-up (`docs/plans/active/gpu-inference-server-bring-up-on-gameon1-PLAN.md`).

- **Unit.** `~/.config/systemd/user/autobot-server.service`, installed and enabled, with lingering on for `schiele` (turned on without `sudo`). `make server-status` reads `stt large-v3-turbo: ready`, `route qwen3:8b: ready`, `chat qwen3:8b: ready`. `make server-check` answers `ok` on all four endpoints against the unit.
- **Speech-to-text.** faster-whisper `large-v3-turbo` on the card with CTranslate2 4.9.1, compute type `int8_float16` (`[server] stt_compute_type`), chosen by measurement on the 3.74 s fixture `tests/fixtures/audio/hey_autobot.wav`, which both types transcribed as "Hey, autobot.":

  | Compute type | `/v1/stt` once warm | VRAM used, whole card, speech model only |
  |---|---|---|
  | `int8_float16` | 0.22 to 0.25 s (0.40 s on the first call) | 1,741 MiB |
  | `float16` | 0.23 to 0.26 s | 2,765 MiB |

- **Language model.** `qwen3:8b` (`[server] llm_model`), written by the owner's choice; it is the only candidate measured so far. Ollama holds one copy, 6.3 GB, wholly on the GPU, with an 8,192-token context, kept loaded. `make server-eval MODEL=qwen3:8b` (`run/server-eval-qwen3-8b.json`, not committed) gave, on the 60 utterances of `server/eval/utterances.jsonl`: the right agent 93.3%, the right agent, mode and parameters 71.7%, valid JSON 100% for routing and for chat; routing median 0.45 s, p95 0.71 s, maximum 1.82 s against the 1.5 s budget; chat's first token median 0.03 s, p95 0.19 s against the 2.0 s budget. Both are within budget. Ollama accepted the request fields the code sends, `"think": false` among them, with no change to `ollama.py`.
- **VRAM.** With both models resident, 7,946 to 7,959 MiB used of 11,264 MiB, about 3 GB free, under the 10,752 MiB budget.
- **Two faults the real host showed, both fixed in the code.** The cuBLAS and cuDNN wheels' `lib` directories are not on the loader's path, so the speech model failed to load with "libcublas.so.12 is not found": `make server-up` and the installed unit now both set `LD_LIBRARY_PATH` to them. And `nvidia-smi` is in `/usr/lib/wsl/lib`, which a login shell's `PATH` has and a systemd user unit's does not: the server looks there when the `PATH` has none.

### Setting the server up on `GAMEON1`

What the repository gives this host, and the order to use it in. Steps 1 and 2 are the owner's, by hand, with rights no `make` target has (IS-14). Everything from step 3 runs inside the `Ubuntu-24.04` distribution, in the checkout, and every `server-*` target refuses on any host whose name is not `[server] hostname`.

1. **Windows and the router, by hand.** The DHCP reservation, both firewall rules and the start-up task, as "The LAN between them" lists them.
2. **Inside WSL, with `sudo`, by hand.** `sudo apt install python3.12-venv python3-pip make nodejs`, and the Ollama install. Ollama is to listen on `127.0.0.1` only (its default), with `OLLAMA_NUM_PARALLEL=1` set on its service, so that one request at a time reaches the model (IS-10). Both are done. A quantised KV cache (`OLLAMA_KV_CACHE_TYPE`) has not been tried on this card; the models fit without it.
3. **`make server-setup`.** Builds `.venv-server`, the server's own virtual environment, and installs the `autobot_server` package from `server/` with its `gpu` extra: faster-whisper, and cuBLAS and cuDNN 9 from pip wheels, with no CUDA toolkit. The NUC's `.venv` is not used here and `autobot` is not installed.
4. **`make server-fetch-models`.** The only downloader on this host. It fetches the CTranslate2 files of faster-whisper `large-v3-turbo` into `models/faster-whisper/large-v3-turbo/` (`[server] stt_model`), and prints the `ollama pull` for the language model, which the owner runs himself: the server never pulls.
5. **`make server-token`.** Makes the shared bearer token, 32 random bytes as text, in `~/.config/autobot-server/token` at mode `0600`, when none is there, and prints the file's path, never the token. The owner opens the file himself and enters its one line at `make server-token` on the NUC, which asks at a hidden prompt and writes `secrets/server-token` at `0600` (git-ignored). Nothing copies the token across the LAN, and an agent never reads either file.
6. **`make server-install`.** Renders `deploy/autobot-server.service` for this checkout, with the NVIDIA wheels' `lib` directories of `.venv-server` as its `LD_LIBRARY_PATH`, writes it to `~/.config/systemd/user/autobot-server.service`, enables it, turns lingering on (`loginctl enable-linger`) and starts it. From then on `make server-up`, `server-down`, `server-status` and `server-logs` drive the unit; without the unit they run the server in the background with its pid in `run/server.pid`. Both ways the log is `run/server.log`: one line per request (method, endpoint, size, duration, status) and nothing of what was said.
7. **`make server-check`.** A loopback smoke test of `/health`, `/v1/stt` (with the committed synthetic fixture `tests/fixtures/audio/hey_autobot.wav`), `/v1/route` and `/v1/chat`, with the server's own token.
8. **`make server-eval MODEL=<name>`**, once for each candidate language model. It writes `run/server-eval-<model>.json`. The owner writes the winner to `[server] llm_model` (IS-26). Until then `/health` reports routing and chat not ready, with that reason, and speech-to-text alone is served.
9. **On the NUC, `make server-ping`.** One `/health` call with the NUC's token: `up` and the contract version, or the one-line reason.

The unit starts the server when the WSL distribution starts. It exits with status 2, and is not restarted, when the configuration, the token, the host name or the VRAM budget is wrong; a model that fails to load or a GPU taken by a game never ends the process (IS-24).

### The LAN between them (planned)

Nothing below is in place yet; it is what IS-2, IS-3 and IS-13 ask for, recorded here so that the NUC side and the owner's Windows steps agree. The commands that need the owner's rights are listed, not run.

- **Addressing.** The two hosts are on the same `/24`, `192.168.86.0/24`, behind the router at `192.168.86.1`. The NUC is `192.168.86.200` (`branch.lan`); how that address is held (static or reserved) is not recorded. This host is `192.168.86.53` by DHCP. The planned approach is a **DHCP reservation on the router** for MAC `A0-36-BC-B8-C2-71` at `192.168.86.53`, rather than a static address set in Windows, because a reservation lives in one place and survives a Windows reinstall. The NUC is to name the server by address in `config/autobot.toml` (IS-3), not by `gameon1.lan`, so that a DNS lapse cannot break it.
- **Port.** One TCP port on this host, proposed `8300` (IS-2), carrying the whole HTTP API. It avoids the ports the `assistant` project uses on the NUC (8099, 8123, 8199). Under mirrored mode a server bound to `0.0.0.0:8300` inside WSL is reached at `192.168.86.53:8300` once the firewalls let it in; no `netsh portproxy` is needed.
- **Firewall.** Two layers must let the NUC in, and only the NUC (IS-13). The owner runs these himself, in PowerShell **as Administrator**; neither has been run or tested:

  ```powershell
  # Hyper-V firewall: lets the NUC's TCP into WSL on the server's port
  New-NetFirewallHyperVRule -Name "autobot-inference-in" -DisplayName "autobot inference server (from NUC)" `
    -Direction Inbound -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' `
    -Protocol TCP -LocalPorts 8300 -RemoteAddresses 192.168.86.200 -Action Allow
  # Windows Firewall: the same, on every profile, since the LAN is classed Public
  New-NetFirewallRule -Name "autobot-inference-in" -DisplayName "autobot inference server (from NUC)" `
    -Direction Inbound -Protocol TCP -LocalPort 8300 -RemoteAddress 192.168.86.200 -Profile Any -Action Allow
  ```

  Whether the second rule is needed alongside the first, given `AllowHostPolicyMerge: True`, is to be confirmed when the port is first tested from the NUC. Changing the LAN's category to Private (`Set-NetConnectionProfile -InterfaceAlias Ethernet -NetworkCategory Private`) is an alternative to `-Profile Any`, left to the owner.
- **Start on boot.** Planned: a Windows scheduled task that starts the distribution at boot and keeps a `wsl.exe` process alive so WSL does not shut the VM down when idle, and inside WSL a systemd unit for the server (systemd is already on). The task needs the owner's password to run with nobody logged in; the owner registers it **as Administrator**. Not run or tested:

  ```powershell
  $a = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-d Ubuntu-24.04 -u schiele --exec sleep infinity"
  $t = New-ScheduledTaskTrigger -AtStartup
  $s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit 0 -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
  Register-ScheduledTask -TaskName "autobot-wsl" -Action $a -Trigger $t -Settings $s -User "GAMEON1\charl" -Password (Read-Host "Password for charl")
  ```

  Inside WSL the server's unit is a user unit, `autobot-server.service`, with lingering on, as the NUC's listener is; it is installed and starts with the distribution.
- **When this host is off or asleep.** The NUC must not depend on it (IS-4): its wake word, microphone, speaker and face never pass through this host. A request to the server is bounded by a short timeout, the NUC keeps a health check of the server, and while it is unreachable the NUC falls back to what it already has: faster-whisper `tiny.en` on its CPU for speech-to-text, piper on its CPU for text-to-speech, and its rules and Gemini agents for the rest. Today the plan sleeps the machine never on AC, but Windows Update restarts and a user's sleep or shutdown still take it away, and after a restart nothing runs until the start-up task above exists. Waking it from the NUC (Wake-on-LAN) is unverified.

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

Four things in the repository define the GPU inference server:

- `server/` is its package, `autobot_server`, with its own `server/pyproject.toml` and its GPU dependencies as the `gpu` extra. It does not import `autobot`. `make server-setup` builds `.venv-server` from it on `GAMEON1`; on the NUC `make setup` installs it without the extra, so that `make test`, `make lint` and `make typecheck` cover it on fakes.
- The `[server]` table of `config/autobot.toml` holds both hosts' settings: the NUC reads the address, the token file's path, the timeouts, the poll intervals and the routing thresholds; the server reads its hostname, its models and its runtime.
- `deploy/autobot-server.service` is the template of the server's systemd user unit, installed by `make server-install` and not copied by hand.
- `server/contract/` holds the contract's version and one example of every request and response, which both hosts' test suites read.

The token files are defined by nothing in the repository, on purpose: `~/.config/autobot-server/token` on `GAMEON1` and the git-ignored `secrets/server-token` on the NUC, each made by `make server-token` on its host. `GAMEON1`'s Windows side (the `.wslconfig`, the firewall rules, the start-up task, the power plan), the router's DHCP reservation, the `apt` packages and the Ollama install with its models are set by hand by the owner.

The Google Cloud side has no infrastructure-as-code. Two files declare what it should hold:

- The `[gcp]` table of `config/autobot.toml`, with its `[[gcp.quotas]]` entries, declares the project, the region, the service account, the key's path, the Gemini API and model, the buckets and the intended caps.
- `src/autobot/gcp/needs.py` catalogues the APIs, roles, permissions and expected secrets.

The Google Cloud screen builds the `gcloud` commands from those two files and checks the project against them. The owner runs the commands himself, by hand or from the scripts under `run/gcp/`, so the project's actual state can drift from them. When the Google Cloud side is written down as code, this section names it.

## Settled without asking

- **Resolved.** The earlier text said Cloud Run would authenticate as a dedicated service account. The spec and the code have the agents running on the NUC as `autobot-agent`, through its key. The document now says that nothing runs on Cloud Run, and the question about what Cloud Run runs is gone.
- **Resolved.** The earlier text said the Windows dev server's WSL layer is not supported and is no test machine. The owner has made that machine, `GAMEON1`, the GPU inference server (2026-11-27). The section now records it as such; it is still not a place where the listener, the studio or any audio path runs.
- **Corrected.** The owner described the CPU as a Ryzen 7 5800; the machine reports a Ryzen 7 5800X3D.
- **Resolved.** The earlier text said autobot uses the owner's Application Default Credentials while testing locally. GC-4 has autobot point Application Default Credentials at the service account's key and refuse any other identity. The owner's own login and default credentials are now recorded as being for his shell only.

## Changes needed elsewhere

- **docs/specs/active/autobot-google-cloud-configuration-screen-SPEC.md.** Drop the `docs/context/INFRASTRUCTURE.md` line from its Changes needed elsewhere, because this document now records the project, region, service account, key path, model, caps and the owner's credentials on the host.

## Open Questions

- **GPU server: address reservation.** `GAMEON1` holds `192.168.86.53` by DHCP with no known reservation. Recommended: reserve it on the router for MAC `A0-36-BC-B8-C2-71`. Which router it is, and whether the NUC's `192.168.86.200` is itself reserved or static, are not recorded.
- **GPU server: firewall.** Neither rule above has been made, and whether the Hyper-V rule alone is enough under `AllowHostPolicyMerge` is untested. The owner decides between `-Profile Any` and reclassifying the LAN as Private.
- **GPU server: start-up and idle shutdown.** No start-up task exists. The unit comes back by itself when the distribution starts (seen after a `wsl.exe --shutdown`), but something on Windows must still start the distribution. Whether a held `sleep infinity` is the right way to keep the WSL VM up with systemd services, or whether WSL 3.0.5 keeps a systemd distribution running on its own with no terminal open, is still unverified. Running the task with nobody logged in needs the owner's stored password on a Home edition machine.
- **GPU server: sleep and wake.** Sleep is off on AC today. Is the machine also used as the owner's own PC (it is named `GAMEON1`), so that games or Windows Update restarts take the GPU and the host away at times? Can it be woken from the NUC (Wake-on-LAN could not be read)?
- **GPU server: WSL memory.** WSL gets 15 GiB of the 32 GB. Is that enough for the server's CPU-side work, or should `.wslconfig` set `memory=`? That is a Windows-side change for the owner.
- **GPU server: not yet called from the NUC.** The server runs on `GAMEON1` and answers over loopback. From the NUC, `make server-token` and `make server-ping` against it are still to be done, and they need the firewall rules above.
- **GPU server: other language models.** Only `qwen3:8b` has been measured. `llama3.1:8b` and a Gemma 4, the other candidates of IS-10, are neither pulled nor measured; `OLLAMA_KV_CACHE_TYPE` is untried.
- **GPU server: routing details.** `qwen3:8b` picks the right agent 93.3% of the time but the right mode and parameters only 71.7%: the misses are nearly all weather, where it answers `forecast` for "today" questions and `day: "tomorrow"` for the weekend or the week. Whether that is the model, the routing prompt or the test set is not settled.

- **Which resources are in place today.** This document records what the configuration names and the screen checks for. It does not record the screen's readings: whether every API is enabled, every role granted, the key created, the secret holding a version, and the fourteen caps accepted. Recommended: after the next round of checks, mark here anything that is not *ready*, since a descriptive document should say what a person would find.
- **Billing account.** The billing account `schiele-autobot-dev` is linked to is not recorded. Recommended: record its id here (it is an identifier, not a secret), so that the spend on the uncapped Gemini API can be traced.
- The trained wake-word model exists only under the git-ignored `models/` on the NUC. Should it, or its training caches, be backed up anywhere? Retraining from nothing takes most of a night.
- The voice dataset will exist only under the git-ignored `data/voice/` on the NUC, and TS-5 says it never leaves the machine. Is a second copy kept on the NUC itself, and where?
- Why did the AIRHUG 21 deliver digital silence on 2026-11-26 with its software volume and capture switch normal? The device's own mute is suspected and not confirmed.
