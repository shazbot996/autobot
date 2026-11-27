---
parent: docs/specs/active/autobot-google-cloud-configuration-screen-SPEC.md
type: plan
title: Google Cloud configuration screen
authority: prescriptive
status: active
describes:
  - pyproject.toml
  - .gitignore
  - config/autobot.toml
  - src/autobot/config.py
  - src/autobot/gcp/__init__.py
  - src/autobot/gcp/needs.py
  - src/autobot/gcp/commands.py
  - src/autobot/gcp/keyfile.py
  - src/autobot/gcp/client.py
  - src/autobot/gcp/checks.py
  - src/autobot/gcp/secrets.py
  - src/autobot/tui/app.py
  - src/autobot/tui/screens/gcp.py
  - src/autobot/tui/screens/main.py
  - tests/test_config_gcp.py
  - tests/gcp/__init__.py
  - tests/gcp/gcp_fakes.py
  - tests/gcp/test_gcp_commands.py
  - tests/gcp/test_gcp_keyfile.py
  - tests/gcp/test_gcp_client.py
  - tests/gcp/test_gcp_checks.py
  - tests/gcp/test_gcp_secrets.py
  - tests/tui/tui_fakes.py
  - tests/tui/test_tui_gcp_screen.py
  - AGENTS.md
  - CHANGELOG.md
provenance:
  - run: schiele/0095
    at: 2026-11-27T15:19:33.049Z
    mode: create
  - run: schiele/0096
    at: 2026-11-27T15:22:56.376Z
    mode: revise
---

# Google Cloud Configuration Screen — Implementation Plan

Implements the autobot — Google Cloud configuration screen spec (`docs/specs/active/autobot-google-cloud-configuration-screen-SPEC.md`).

Nothing in the spec is built yet. There is no `src/autobot/gcp/` package, no `[gcp]` table in `config/autobot.toml` or in `autobot.config.parse_config` (its top-level keys are `host`, `audio`, `wake_phrases`, `ack`, `tui`, `routes`), no Google Cloud dependency in `pyproject.toml`, and no masked text field in `autobot.tui.app`. The agent tree spec it builds on isn't built either. The main menu in `src/autobot/tui/screens/main.py` still has its **Face** item, there is no Agent editor and no Tab mode switch (AG-8, AG-9, AG-10), and there are no `[[agents]]` definitions (`ROUTE_AGENTS` is only `echo`). This plan therefore puts the screen on today's main menu, which is the configuration menu, and gives the Agent editor a ready-made path to store the weather key (GC-9) for when it is built.

## 0. The delta

| Spec Rule | Current Implementation | Planned Change |
|---|---|---|
| GC-1 Full-screen screen, eight steps, four states, Up/Down/Enter/R | Not built; the main menu has no Google Cloud entry | A step model in `autobot.gcp.checks`, a `GcpScreen` in `src/autobot/tui/screens/gcp.py`, and a **Google Cloud** main-menu item |
| GC-2 Show commands, never run them; `W` writes a script under `run/gcp/` | Not built | `autobot.gcp.commands` builds filled-in `gcloud` lines and scripts. Commands are drawn whole on their own lines, `W` writes `run/gcp/<step>.sh`, and no code path runs them |
| GC-3 `[gcp] project` and `region` in the config, typed on screen | No `[gcp]` table | `GcpConfig` in `config.py`, a seeded `[gcp]` table, Project-step fields written through `ConfigStore.update`, and the `gcloud auth` commands shown |
| GC-4 Service-account identity only, through ADC pointed at the key; refuse any other identity | Not built | `autobot.gcp.keyfile` points `GOOGLE_APPLICATION_CREDENTIALS` at the key and resolves the identity before any call. Without the key nothing is called and checks read *unchecked: no service account key yet* |
| GC-5 APIs step lists the needed APIs, shows `gcloud services enable` for missing ones | Not built | The API catalogue in `autobot.gcp.needs` (the eight APIs of the table) and an APIs check |
| GC-6 Narrowest roles, per-secret where possible; broad roles flagged *too broad* | Not built | The role catalogue in `needs.py`, the grant commands, and a broad-privilege check with removal commands |
| GC-7 Key made by a script, git-ignored, `0600`, only its path in config | `.gitignore` covers `service-account*.json` but not `secrets/` | The key script, a key-file check (exists, `git check-ignore`, owner-only mode), and `secrets/` added to `.gitignore` |
| GC-8 `testIamPermissions` on the project, each secret and each bucket | Not built | A permission check that every account-dependent step takes its state from |
| GC-9 Secret Manager step; a pasted secret goes only to Secret Manager | Not built; no masked field | The expected-secrets list, a Secret Manager check, `autobot.gcp.secrets.add_version`, a masked prompt in `App`, and the weather-key restriction command |
| GC-10 `[[gcp.quotas]]` caps, live limit at or below the intended one | Not built | Quota entries in the config, a Rate limits check, and `gcloud beta quotas preferences` commands. Billed APIs with no entry are listed as *missing* |
| GC-11 Gemini step: `[gcp] gemini_api`, Vertex AI in `us-east5` | Not built | `gemini_api` in the config (default `aiplatform.googleapis.com`) and a Gemini check built from the API and permission checks |
| GC-12 Cloud Storage step: `[gcp] buckets`, *not used yet* when empty | Not built | `buckets = []` in the config. The step reads *not used yet*, and checks a bucket only when one is listed |
| GC-13 No speech to the cloud, GC-5 APIs only, one-line failures, logic tested with a fake | Not built | A host allowlist in the REST client, `GcpError` one-line messages, and every check tested against a fake Google Cloud |

## 1. Configuration and dependencies

**Files:** modified `pyproject.toml`, `.gitignore`, `config/autobot.toml`, `src/autobot/config.py`; new `tests/test_config_gcp.py`.

- Add `google-auth>=2.29` and `requests>=2.31` to the runtime dependencies. Add a mypy override that ignores missing imports for `google.*` and `requests`. No Google Cloud speech or other client library is added (`CN-1`, `CN-2`).
- `config.py` gains `GcpConfig` and `QuotaConfig`, and `Config` gains `gcp: GcpConfig = GcpConfig()`. Fields and defaults:
  - `project = "schiele-autobot-dev"`
  - `region = "us-east5"`
  - `service_account = "autobot-agent@schiele-autobot-dev.iam.gserviceaccount.com"`
  - `credentials_file = "secrets/service-account-autobot-agent.json"`
  - `gemini_api = "aiplatform.googleapis.com"` (the other allowed value is `generativelanguage.googleapis.com`)
  - `buckets = ()`
  - `quotas`: a tuple of `QuotaConfig(api, metric, limit)`
- Validation errors are one line and name the key, as `parse_config` does today:
  - the project id must look like a Google Cloud project id;
  - the region must look like a region;
  - `service_account` must end in `.iam.gserviceaccount.com`;
  - `limit` must be a positive whole number;
  - `gemini_api` must be one of the two values.
- Seed a commented `[gcp]` table and three `[[gcp.quotas]]` entries in `config/autobot.toml`:
  - the weather API at 100 per day;
  - the weather API at 10 per minute;
  - `aiplatform.googleapis.com` at 10 per minute.
- Add `secrets/` to `.gitignore` under `# secrets`.

**Done when:** `load_config()` on the committed file returns a `Config` whose `gcp.project` is `schiele-autobot-dev` and whose `gcp.quotas` has three entries. `tests/test_config_gcp.py` shows that a bad project id, region, account, limit or `gemini_api` each give a one-line `ConfigError`. `git check-ignore secrets/anything` prints the path. `make test`, `make lint` and `make typecheck` pass.

## 2. What autobot needs, and the commands that fix it

**Files:** new `src/autobot/gcp/__init__.py`, `src/autobot/gcp/needs.py`, `src/autobot/gcp/commands.py`, `tests/gcp/__init__.py`, `tests/gcp/test_gcp_commands.py`.

- `needs.py` holds the catalogues as data:
  - **APIs:** the eight APIs of GC-5, each with its "Needed for" line.
  - **Roles:** the six roles of GC-6, each with where it is granted (`project` or `secret`) and its "Lets autobot" line.
  - **Permissions:** the permissions each role covers for GC-8, tested on the project and on each secret.
  - **Expected secrets:** one entry, `autobot-weather-api-key`, named by the `weather` agent (AG-6).
  - **Billed APIs:** the weather API and `aiplatform.googleapis.com`.
  - **Too-broad markers:** permissions only a broad role holds, such as `resourcemanager.projects.setIamPolicy`, `serviceusage.services.enable`, `iam.serviceAccounts.create`, `iam.serviceAccountKeys.create` and `billing.resourceAssociations.create`. Each marker is mapped to the roles that commonly grant it.
- `commands.py` turns a `GcpConfig` into `Command(text, purpose)` lines. Every value is filled in from the config and shell-quoted:
  - `gcloud config set project`;
  - `gcloud auth login` and `gcloud auth application-default login`;
  - `gcloud services enable`;
  - `gcloud iam service-accounts create`;
  - `gcloud projects add-iam-policy-binding` and `remove-iam-policy-binding`;
  - `gcloud secrets add-iam-policy-binding` and `gcloud secrets create`;
  - `gcloud services api-keys create` and `update`, with `--api-target=service=weather.googleapis.com`;
  - `gcloud beta quotas preferences create`;
  - read-only `list` and `describe` commands for each step.
- `commands.py` also renders the key script. It uses `set -euo pipefail` and `umask 077`, creates `secrets/` as `0700`, runs `gcloud iam service-accounts keys create <credentials_file> --iam-account=<service_account>`, then runs `chmod 600` on the key. It never prints the key.
- `write_script(step, commands, run_dir)` writes `run/gcp/<step>.sh` with a header comment, and returns the path. Nothing in the package imports `subprocess` to run `gcloud`.

**Done when:** `tests/gcp/test_gcp_commands.py` passes. It checks that every command for the seeded config contains `schiele-autobot-dev` and, where it applies, `us-east5` and the account. It checks that the secret role grants target the secret and not the project, that the key script ends in `chmod 600`, that `write_script` writes under a temporary `run/gcp/`, and that a search of `src/autobot/gcp/` finds no `gcloud` passed to a subprocess.

## 3. The key file, the identity and the client

**Files:** new `src/autobot/gcp/keyfile.py`, `src/autobot/gcp/client.py`, `tests/gcp/gcp_fakes.py`, `tests/gcp/test_gcp_keyfile.py`, `tests/gcp/test_gcp_client.py`.

- `keyfile.check_key(config, repo, git)` returns *ready* only when all three of these hold:
  - the file at `credentials_file` exists;
  - `git check-ignore -q <path>` exits 0;
  - the mode has no group or other bits and the owner is the current user.
  Any other result names its cause in one line, for example "git does not ignore secrets/…: the key is not used". The function reads the file's metadata only, never its contents. `git` is injected, so tests give a fake.
- `client.connect(config, repo)` refuses unless `check_key` is *ready*, and never calls `google.auth.default()` without the key, so it cannot fall back to the owner's login (GC-4). It then:
  1. sets `GOOGLE_APPLICATION_CREDENTIALS` in the process to the resolved key path;
  2. calls `google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])`;
  3. checks that the credentials are service-account credentials whose `service_account_email` equals `[gcp] service_account`. Anything else raises `GcpError("refused: credentials resolve to <identity>, not <service_account>")`.
- `client.py` defines a `GoogleCloud` protocol, which is the one seam every check uses:
  - `enabled_services()`
  - `test_permissions(resource, permissions)` for the project, a secret and a bucket
  - `secret_versions(secret)`
  - `add_secret_version(secret, payload)`
  - `api_keys()`
  - `quota_limit(api, metric, region)`
  - `bucket_exists(bucket)`
- `RestGoogleCloud` implements the protocol over `google.auth.transport.requests.AuthorizedSession` against the REST endpoints of the GC-5 APIs. It refuses any URL whose host is not in the allowlist built from `needs.py`. Every HTTP or transport error becomes a one-line `GcpError`.
- `gcp_fakes.FakeGoogleCloud` keeps enabled APIs, held permissions, secrets, keys and quotas in memory, and records every call.

**Done when:** `test_gcp_keyfile.py` passes. It covers a missing file, an ignored file at `0600`, a file at `0644`, and a path the fake `git` reports as not ignored. `test_gcp_client.py` passes. It shows that with no key file `connect` raises before any `google.auth` call (patched to fail if reached), that credentials for another identity are refused with one line, and that a URL on a host outside the allowlist is refused. None of these tests opens a network connection or reads a real key.

## 4. The step model and the checks

**Files:** new `src/autobot/gcp/checks.py`, `tests/gcp/test_gcp_checks.py`.

- `checks.run_all(config, cloud | None, key_state)` returns eight `Step` records, in the GC-1 order. Each record holds:
  - its state: *ready*, *missing*, *unchecked* with a reason, or *not used yet*;
  - its purpose;
  - its findings, as lines;
  - its fix commands, from step 2;
  - the earlier step it waits on, if any.
  A step whose prerequisite is not *ready* says which step that is. With no client, every account-dependent step reads *unchecked: no service account key yet* and shows its read-only command.
- What each step checks:
  - **Project:** the configured project and region. It reads *missing* when `service_account`'s domain is not `<project>.iam.gserviceaccount.com`. It always shows the `gcloud auth` commands.
  - **APIs:** each catalogue API, enabled or missing (GC-5). When `[gcp] buckets` is non-empty the list includes `storage.googleapis.com`. When `gemini_api` is the Developer API, the list includes `generativelanguage.googleapis.com`.
  - **Service account:** the GC-8 permission check on the project. Any too-broad marker held reads *too broad*, a finding of the *missing* state, with the removal commands for the roles mapped to that marker and the read-only `gcloud projects get-iam-policy` command filtered to the account.
  - **Key:** the result of `check_key` (GC-7) and the key script.
  - **Secret Manager:** the API is on, the permission check passes on each expected secret, and each secret has an enabled version. The value is never read. The step also checks that an API key labelled `autobot-weather` is restricted to `weather.googleapis.com` only.
  - **Gemini:** the chosen API is enabled and its permissions (`aiplatform.endpoints.predict` on the project for Vertex AI) are held.
  - **Rate limits:** each `[[gcp.quotas]]` entry's live limit compared with the intended one. Each billed API that has no entry is listed as *missing*.
  - **Cloud Storage:** *not used yet* while `buckets` is empty. Otherwise each bucket's existence, the API, and `storage.objects.get`, `storage.objects.create` and `storage.objects.list` through `test_permissions`.
- A `GcpError` from any call turns only the step that made the call into *unchecked: <the one line>*.

**Done when:** `test_gcp_checks.py` passes against `FakeGoogleCloud` for each of these cases:
- with no key, every account-dependent step is *unchecked: no service account key yet* and the fake records no calls;
- with everything in place, every step is *ready* and Cloud Storage is *not used yet*;
- one API disabled gives *missing* with that one `gcloud services enable` line;
- a held `serviceusage.services.enable` gives *too broad*;
- a weather daily quota of 1000 gives *missing* with the preference command for 100;
- a configured bucket that does not exist gives *missing*.

## 5. Storing a pasted secret

**Files:** new `src/autobot/gcp/secrets.py`, `tests/gcp/test_gcp_secrets.py`; modified `src/autobot/tui/app.py`.

- `secrets.store_secret(cloud, config, secret, value: bytearray)` first checks that the secret is in the expected list. It then calls `add_secret_version` through the connected service-account client and zeroes `value` in a `finally` block, whether the call worked or not. It does no logging and writes no file. Any `GcpError` it raises names the secret and the cause, never the value. This is the path the Agent editor will call (AG-10).
- `App` gains `prompt_secret(title, label)`:
  - a single-line field that draws one mask character per typed character and keeps the text in a `bytearray`;
  - Escape zeroes the buffer and returns `None`;
  - pasted text arrives as keystrokes and is masked in the same way;
  - `T` is a letter inside the field, as in `prompt` (TS-13).
- `Services` gains `gcp_connect: Callable[[Config, Path], GoogleCloud]`, which defaults to `client.connect`, and `git_check_ignore`, so tests give fakes.

**Done when:** `test_gcp_secrets.py` passes. It shows that after `store_secret` the fake holds the new version and the caller's buffer is all zero bytes, that a refused identity leaves the buffer zeroed with no call recorded, and that the text of the raised error does not contain the value. A scripted-screen test shows that typing into `prompt_secret` draws only mask characters.

## 6. The Google Cloud screen and its menu entry

**Files:** new `src/autobot/tui/screens/gcp.py`, `tests/tui/test_tui_gcp_screen.py`; modified `src/autobot/tui/screens/main.py`, `tests/tui/tui_fakes.py`.

- The main menu gains **Google Cloud** after **Agent routing**, with the hint "Connect autobot to Google Cloud as its own service account, one step at a time".
- `GcpScreen` is a full-screen view:
  - the list of steps with their state badges, then the open step's purpose, findings and commands;
  - each command is drawn alone on its own lines, unwrapped at the left edge with nothing beside it, and continues onto the next line when it is longer than the width;
  - the screen and its detail scroll.
- Keys:
  - Up and Down move between steps.
  - Enter opens a step.
  - `R` checks every step again. The checks run through `Services.spawn`, so the frame never blocks.
  - `W` writes the open step's commands and shows the script's path.
  - On the Project step, Enter on **Project** or **Region** opens `app.prompt` with a validity check, and the value is saved through `app.save(Change("gcp", ...))`.
  - On the Secret Manager step, Enter on a secret that exists opens `prompt_secret`, then `store_secret`. A secret that does not exist instead shows its create and grant commands.
- Every failure is one line in the message bar. Help lines list `R`, `W` and Enter.

**Done when:** `test_tui_gcp_screen.py` drives the real screen through the scripted fake screen and `FakeGoogleCloud`, and passes. It shows that:
- the menu reaches **Google Cloud**;
- the eight steps are drawn in order;
- with no key, the Key step shows the script and `W` creates `run/gcp/key.sh` in the test's temporary repository;
- typing a region writes `[gcp] region` to the temporary config file;
- pasting a secret records one `add_secret_version` and puts the value on no frame and in no file under the temporary directory;
- a `GcpError` shows as one line.

`make launch` in a pseudo-terminal opens the screen on a checkout with no key and shows every account-dependent step as *unchecked*.

## 7. Agent guide and changelog

**Files:** modified `AGENTS.md`, `CHANGELOG.md`.

- `AGENTS.md` adds **Google Cloud** to the interface's settings screens. It names the key at `[gcp] credentials_file` and everything under `secrets/` as credential files that agents never read, and says that agents never run the screen's `gcloud` commands.
- `CHANGELOG.md` records the Google Cloud screen under Unreleased.

**Done when:** both files carry those lines, `node .agents/skills/cr2-conformance/scripts/check.mjs` passes, and `make test`, `make lint` and `make typecheck` all pass.

## 8. Choices the spec leaves open

1. **Calls are plain REST through `google-auth`**, not one client library per Google API. That gives one small `GoogleCloud` seam to fake (GC-13), a host allowlist in one place, and only two dependencies.
2. **ADC is pointed at the key by setting `GOOGLE_APPLICATION_CREDENTIALS` in the process before `google.auth.default()`.** That is the ADC search the spec names (GC-4), and the agents autobot later runs in the same process inherit it.
3. **Too-broad roles are found with `testIamPermissions` on marker permissions**, not by reading the project's IAM policy. None of the GC-6 roles grants `resourcemanager.projects.getIamPolicy`. See Open Questions.
4. **The weather key restriction is checked by the key's display name `autobot-weather`**, which the create command sets. Checking it this way means the check never reads the secret's value.
5. **A pasted secret is held in a `bytearray` and zeroed after use.** A Python `str` cannot be cleared, and GC-9 asks for the value to be cleared from memory.
6. **The intended cap's period lives in the quota id** (per day, or per minute), so a `[[gcp.quotas]]` entry is exactly `api`, `metric` and `limit`, as the spec's SCHEMA change lists. Regional quotas are compared in `[gcp] region`.

## 9. Out of scope

- The Agent editor, the Tab mode switch and `[[agents]]` definitions (AG-8, AG-9, AG-10). This plan only gives the editor `store_secret` to call.
- The `weather` and `question` agents themselves, and the agents' own rate control (AG-6).
- Storing any agent state or memory in Cloud Storage (GC-12, ADR 0002).
- Switching `gemini_api` from the screen. The owner chose Vertex AI, and the value is edited in the file.
- Running any `gcloud` command or script from autobot.
- Edits to the context documents, the other specs and a new ADR, which the spec's own "Changes needed elsewhere" lists.

## Settled without asking

- **Default.** The **Google Cloud** item goes on today's main menu, because AG-8's configuration mode is not built and the main menu is the configuration menu it describes.
- **Default.** Until agent definitions exist, the expected secret `autobot-weather-api-key` is listed in `autobot.gcp.needs` under the `weather` agent. It moves to the agent definition's secret field once `[[agents]]` is built.
- **Default.** The screen edits only `project` and `region` (GC-3). `service_account`, `credentials_file`, `gemini_api`, `buckets` and `[[gcp.quotas]]` are edited by hand in the file, because `config_edit` writes one-line scalars only and the spec puts no other field on the screen.
- **Default.** The seeded quota ids are the per-day and per-minute request quota ids that `gcloud beta quotas info list --service=<api>` prints. The owner runs that command, because an agent never uses his credentials. An id that does not exist shows as *missing* with that read-only command beside it.
- **Default.** The OAuth token exchange to `oauth2.googleapis.com` is allowed as part of authenticating as the service account. It is the only host outside the GC-5 list.
- **Default.** `storage.googleapis.com` joins the APIs list only once `[gcp] buckets` is non-empty, so with no buckets nothing connects to it (GC-12, GC-13).
- **Derived (GC-11, GC-9).** With Vertex AI chosen, there is no Gemini secret, and `generativelanguage.googleapis.com` is added only if `gemini_api` names it.

## Changes needed elsewhere

- **docs/specs/active/autobot-google-cloud-configuration-screen-SPEC.md.** GC-5's table gains `storage.googleapis.com` ("the Cloud Storage step, once a bucket is listed"), and GC-13 names the OAuth token endpoint as part of authenticating as the service account.
- **docs/context/SCHEMA.md, ARCH.md, GLOSSARY.md, INFRASTRUCTURE.md, the agent-tree and agent-sessions specs, and a new ADR.** These are as the spec's own "Changes needed elsewhere" lists them. Neither the plan nor the code changes them.

## Open Questions

- **How the account's broad roles are found.** GC-6 says the step "reads the account's roles", but no GC-6 role lets the account read the project's IAM policy. Recommended: detect broad roles by testing marker permissions with `testIamPermissions`, and show the owner the read-only `get-iam-policy` command to see which role grants them. This needs no extra grant, whereas `roles/browser` would let the agent read every member's bindings.
