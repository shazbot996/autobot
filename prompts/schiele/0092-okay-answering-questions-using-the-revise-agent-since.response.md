---
run: schiele/0092
at: 2026-11-27T15:12:46.949Z
mode: revise
agent: revise
model: claude:claude-opus-5-5
target: docs/specs/active/autobot-google-cloud-configuration-screen-SPEC.md
template: revise
prompt-sha: a8bd12a62c00
template-sha: 80089d1722f6
commit: 19f1108+
attachments: []
read:
  - AGENTS.md
  - docs/specs/active/autobot-google-cloud-configuration-screen-SPEC.md
outcome: success
usage:
  input: 27323
  output: 8227
  peak: 27323
  window: 1000000
cost: 0
---

---
type: spec
title: autobot — Google Cloud configuration screen
authority: prescriptive
status: active
describes:
  - src/autobot/gcp/**
  - src/autobot/tui/**/gcp*
  - tests/**/*gcp*
provenance:
  - run: schiele/0089
    at: 2026-11-27T15:03:18.250Z
    mode: create
  - run: schiele/0090
    at: 2026-11-27T15:06:22.982Z
    mode: revise
---

# autobot — Google Cloud configuration screen

The **Google Cloud** screen is a full-screen editor in the configuration menu, the same kind of screen as the Agent editor. It walks the owner through connecting autobot to the Google Cloud project `schiele-autobot-dev` in this order: set the project, check the APIs, create a dedicated service account with its IAM bindings, create that account's key, set up Secret Manager and the secrets autobot expects, choose the Gemini API, set rate limits, and show the Cloud Storage buckets. At each step the screen checks what is already in place. When something is missing it shows the commands that fix it, and the owner copies them and runs them himself. autobot never configures itself in Google Cloud: the owner's account is privileged (owner, with billing rights), so autobot and its agents run only as the service account, and the one value the screen sends to the cloud itself is a secret the owner pastes in. Rules GC-1 to GC-13 govern the screen. It builds on the agent tree spec (AG-6, AG-8, AG-10, AG-12), the agent sessions stub (AS-1, AS-2), the studio spec (TS-10, TS-13), ADR 0002 and `CN-1` and `CN-2`.

## Numbered rules, each with its reason

1. **GC-1. The Google Cloud screen is a full-screen screen of the configuration menu, walked one step at a time.** The main menu in configuration mode (AG-8) gains a **Google Cloud** entry. It opens a full-screen screen that lists the steps in order: **Project**, **APIs**, **Service account**, **Key**, **Secret Manager**, **Gemini**, **Rate limits**, **Cloud Storage**. Each step shows one of four states: *ready*, *missing*, *unchecked* (it cannot be checked yet, and the screen says why) or *not used yet*. Up and Down move between steps. Enter opens a step: what it is for, what was checked and found, and, when something is missing, the commands that fix it. A step that depends on an earlier step that is not *ready* says which one. `R` checks every step again. Reason: the owner wants "another full-screen application editor screen that we invoke from the configuration menu", like the agent tree, and wants it to "walk me through each step", because setting up a service account and its key "is a tricky process". The order is the order the setup works in: the project comes first, the APIs must be on before the service account can be created, and the account must exist before its key can be made or its permissions checked.

2. **GC-2. The application shows commands. It never runs a command that changes Google Cloud.** Every change to the project is a `gcloud` command shown on the screen, filled in with the configured project, region and account. This covers enabling an API, creating the service account, granting a role, creating the key, creating a secret, and setting a quota cap. Each command is drawn whole on its own lines, with nothing beside it, so that selecting it with the mouse copies it cleanly. `W` writes the open step's commands as a shell script under `run/gcp/`, for the owner to read and run with `bash`. The application never runs those commands or the script, by itself or through a subprocess. The only change it makes is adding a secret value the owner pastes in (GC-9). Reason: the owner said "I don't want the agent to be able to configure itself, but I would like it to be able to offer me the script to do it". Commands the owner runs in his own shell run with his own credentials, which never enter autobot. `run/` is git-ignored runtime state.

3. **GC-3. The project and region are settings in `config/autobot.toml`.** The Project step sets `[gcp] project`, which is `schiele-autobot-dev`, in the Google Cloud organization `cambiumplatform.com`, whose domain and organization the owner owns, and `[gcp] region`, which is `us-east5`. They are typed into fields on the screen and written to the file at once. Every command and check uses them. The step also shows the commands the owner uses to keep his own `gcloud` login and Application Default Credentials up to date (`gcloud auth login`, `gcloud auth application-default login`), as commands for his shell only. Reason: the owner said "the first thing this application should do is allow you to set the GCP project". Configuration lives in one file, and the interface holds none of its own (TS-10, `CONVENTIONS.md` "Layout").

4. **GC-4. autobot and its agents use Google Cloud only as the dedicated service account, never as the owner.** autobot finds its credentials through Application Default Credentials, which the application always points at the service account's key file (GC-7) for itself and the agents it runs. It does not fall back to the owner's own default credentials. Before any cloud call, the application checks which identity the credentials resolve to. If that identity is not the configured service account (`[gcp] service_account`), the call is refused, and the reason is one line on the screen or in the summary panel. Until the key exists, every check that needs the account reads *unchecked: no service account key yet*, and the step shows the read-only `gcloud` command the owner can run to check it himself. The application does not run those checks with the owner's own login. Reason: the owner wants "to just use application default credentials", but wants "this application to [run] its commands through a service account that we create because this account is privileged, it's the owner, and it has billing rights to everything, and so I don't want the agent running with those credentials". Default credentials pointed at the account's key give both. Refusing any other identity stops a missing key from quietly falling through to the owner's login. Before the key exists, the screen guides the owner through setting the application up and only shows him commands, so his credentials never pass through the application.

5. **GC-5. The APIs step checks every API autobot needs and shows the commands to enable the ones that are missing.** The step lists the APIs that autobot's steps and agents use, shows whether each one is enabled in the project, and for the missing ones shows one `gcloud services enable` command for the owner to run with his own credentials. The first set is below. An agent that starts calling a new Google API adds it to the list.

   | API | Needed for |
   |---|---|
   | `serviceusage.googleapis.com` | checking which APIs are enabled, and reading quotas |
   | `cloudresourcemanager.googleapis.com` | the permission check (GC-8) |
   | `iam.googleapis.com` | creating the service account and its key |
   | `secretmanager.googleapis.com` | the secrets (GC-9) |
   | `aiplatform.googleapis.com` | Gemini on Vertex AI, when that is the chosen API (GC-11) |
   | `weather.googleapis.com` | the `weather` agent's lookup (AG-6) |
   | `apikeys.googleapis.com` | creating and restricting the weather API key |
   | `cloudquotas.googleapis.com` | setting the caps (GC-10) |

   Reason: the owner asked for "an API check, and if we're missing APIs, again, we should show [commands] that we can cut and paste with privileges to enable those APIs". The owner enables the APIs, because the service account is not allowed to (GC-6).

6. **GC-6. The service account gets the narrowest roles that cover what autobot does, and never a broad one.** The Service account step shows the commands that create the account `[gcp] service_account` (by default `autobot-agent@schiele-autobot-dev.iam.gserviceaccount.com`) and grant its roles, one at a time, each with a line saying what the role lets autobot do. Where a role can be granted on a single resource, such as one secret, it is granted there rather than on the project. The first set:

   | Role | Granted on | Lets autobot |
   |---|---|---|
   | `roles/serviceusage.serviceUsageViewer` | project | see enabled APIs and quotas |
   | `roles/serviceusage.apiKeysViewer` | project | check how the weather key is restricted |
   | `roles/aiplatform.user` | project | call Gemini on Vertex AI (GC-11) |
   | `roles/secretmanager.viewer` | each expected secret | see that the secret exists and has a version |
   | `roles/secretmanager.secretAccessor` | each expected secret | read the secret's value at run time |
   | `roles/secretmanager.secretVersionAdder` | each expected secret | store a pasted value (GC-9) |

   The step reads the account's roles and flags any role that would let it configure the project or reach billing (`roles/owner`, `roles/editor`, any IAM, service-usage admin or billing role) as *too broad*. The fix it shows is the command that removes that role. Reason: the whole point of the account is that the agent does not run with the owner's privileges. Giving it a role that lets it enable APIs, grant itself roles or spend money would defeat that, and would let the agent configure itself, which the owner does not want (GC-2).

7. **GC-7. The key is created by a script the owner runs, kept git-ignored, and only its path is in the configuration.** The Key step shows a script, also offered through `W` (GC-2). The script creates the account's JSON key at the path in `[gcp] credentials_file` (by default `secrets/service-account-autobot-agent.json`, which the existing `service-account*.json` pattern in `.gitignore` already covers) and sets its permissions to `0600`. The step reports *ready* only when the file exists, `git check-ignore` confirms that git ignores the path, and the file is readable by its owner alone. If git does not ignore the path, the step reports *missing* with the reason, and the application does not use the key. The application never shows the key's contents, never copies the key, and never writes it anywhere else. Reason: the owner called "conveying of the key" "the dangerous part" and asked for "scripting the download of the JSON, keep it in the git-ignore". A key that git does not ignore is one `git add` away from being committed, and `AGENTS.md` keeps credentials out of the repository. A key file is used rather than default credentials that impersonate the service account, because impersonation keeps the owner's refresh token where the application could reach it. The key is the only way the agent never holds the owner's credentials.

8. **GC-8. The permission check proves that the account can do everything autobot needs.** Once the key is *ready*, the screen asks Google Cloud which of the permissions autobot needs the account actually holds (`testIamPermissions` on the project and on each secret and bucket). It lists each needed permission as held or missing. For a missing permission it shows the GC-6 role that grants it. Every step that uses the account takes its state from this check as well as from its own. Reason: the owner wants the screen to "test that it has the privileges" so "that it is comprehensive in the APIs and permissions that it has". Asking for the permissions themselves finds a missing grant before an agent fails on it in the middle of a session.

9. **GC-9. Secret Manager holds every secret autobot expects, and a pasted secret goes nowhere but Secret Manager.** The Secret Manager step first checks that the API is on and the account can reach it. If not, it shows the commands from GC-5 and GC-6. It then lists every secret that autobot's configuration expects, each one named by the agent definition that needs it. The first is the `weather` agent's API key (AG-6). For each secret it shows whether the secret exists and has an enabled version, and never shows the value. A missing secret gets the `gcloud secrets create` command and its per-secret role grants, for the owner to run. Enter on an existing secret opens a masked field. The owner pastes the value, the application adds it to Secret Manager as a new version through the service account, and then clears it from memory. A pasted value is never written to `config/autobot.toml`, `run/`, a log, the screen or any other file. The Agent editor's edit of the weather API key (AG-10) uses this same path. For the weather key, the step also shows the command that restricts the key to `weather.googleapis.com`. Reason: the owner wants the screen to "configure a secret manager once it's working, so again if it's not working, show me the code to enable it", to manage "the secrets that we expect to store in secret manager … from here", and to "paste in the credentials without ever persisting them anywhere except for secret manager". Restricting the key to one API limits what a leaked key could be used for.

10. **GC-10. Every billed API that an agent calls has a cap in Google Cloud, and the screen shows that the cap is in place.** The configuration records the intended cap for each billed API as `[[gcp.quotas]]` entries: the API, the quota metric, and the limit per day or per minute that the API offers. The Rate limits step reads the live quota for each entry and shows *ready* when the live limit is at or below the intended one. It shows *missing* when there is no cap or the cap is higher, with the command that sets the cap, for the owner to run. A billed API that an agent calls but that has no entry is listed as *missing*. The first entries are the `weather` agent's API, capped at 100 requests a day and 10 a minute, and Gemini on Vertex AI (`aiplatform.googleapis.com`), capped at 10 requests a minute. The owner expects about 50 weather lookups a day, so 100 a day is twice his expected use, and the per-minute caps stop a runaway agent within minutes, at the cap and not at the bill. The cap in Google Cloud is the backstop behind the agent's own rate control (AG-6), not a replacement for it. Reason: the owner wants "real constraints on the rate limit" as "a safeguard" around any API "this application's agents ever might use", Maps above all because it is "very commonly abused" and his use is "just home use". He cannot translate his usage into a limit, so the screen's job is to show plainly that a limit exists and what it is.

11. **GC-11. The Gemini step shows which Gemini API autobot uses and checks that the account can call it.** `[gcp] gemini_api` names the API the `question` agent's ADK agent calls (AS-1, AG-5). It is set to Vertex AI (`aiplatform.googleapis.com`, authenticated as the service account, in `[gcp] region`, `us-east5`). The other choice is the Gemini Developer API (`generativelanguage.googleapis.com`, authenticated with an API key kept as a secret under GC-9). The step shows the choice, checks that the API is enabled (GC-5) and that the account holds what that API needs (GC-8), and shows the commands that fix whatever is missing. Discovery Engine (`discoveryengine.googleapis.com`) serves search and grounding, not Gemini models, and is not on the list unless an agent comes to need it. Reason: the owner said "we have to pick the Gemini API we're going to use and also show that here and check the permissions", and asked for help telling the APIs apart. He chose Vertex AI in `us-east5`. Vertex AI fits GC-4, because the service account needs no extra API key.

12. **GC-12. The Cloud Storage step shows the configured buckets and checks the account's access to them. Nothing stores state there yet.** `[gcp] buckets` lists the buckets autobot is meant to use. It is empty today, and the step reads *not used yet*. For each bucket listed, the step checks that it exists, that the Cloud Storage API is enabled, and that the account holds the permissions autobot needs on it (GC-8). For anything missing, it shows the commands. This spec does not move agent state or memory into a bucket. They stay on the NUC (AS-2, ADR 0002) until a later decision says otherwise. Reason: the owner expects "a cloud storage bucket or two" for state and says "that's a future. We're not doing that yet. But this application will need to at least show it. And test that it has the privileges". There is no defined use for Cloud Storage today. The step stays in scope so the screen is ready for future agents that may use it. ADR 0002 and `AGENTS.md` keep agent memory and state on the local machine, and only a new decision can change that.

13. **GC-13. Only configuration and text reach Google Cloud. Failures are one line, and the logic is testable without a terminal or a network.**
    - **Speech.** Nothing on this screen sends audio, transcripts for recognition, or text for synthesis to any cloud service (`CN-1`, `CN-2`).
    - **Network.** In configuration mode, the screen connects only to the Google Cloud APIs named in GC-5, as the service account, to run its checks and to add a pasted secret.
    - **Failures.** A failed check, a refused identity (GC-4), a missing key or an unreachable API is one line naming the cause, never a traceback (`CONVENTIONS.md` "Language and formatting").
    - **Testable.** The step model, the checks, the generated commands and scripts, and the identity and key-file checks live in modules that do no drawing. They are tested with a fake Google Cloud, and never with a real key or a network connection (`CONVENTIONS.md` "Testing", "Control layers").

    Reason: the constraints forbid any cloud speech path. The interface keeps its logic out of the drawing code so that it can be tested. A test that reached Google Cloud would need a credential, which `AGENTS.md` forbids an agent to read.

## Settled without asking

- **Derived (GC-11, GC-9).** Because Gemini is called through Vertex AI as the service account, it needs no secret, and the weather API key is still the only expected secret.
- **Derived (GC-11, GC-6).** Because Vertex AI is the chosen API, `aiplatform.googleapis.com` stays on the GC-5 list and `roles/aiplatform.user` stays on the GC-6 list. `generativelanguage.googleapis.com` is not added to either list.
- **Resolved.** GC-10 used to cap the weather API per day only. It now has a daily cap and a per-minute cap, recorded as separate `[[gcp.quotas]]` entries, and the Gemini cap is per minute.

## Changes needed elsewhere

- **docs/context/INFRASTRUCTURE.md.** Record the project `schiele-autobot-dev` in the organization `cambiumplatform.com`, which the owner owns along with its domain. Also record the region `us-east5`, the service account `autobot-agent`, the key path on the NUC, and that the owner's gcloud and Application Default Credentials are refreshed on this host. Answer the open question on the project id.
- **docs/context/SCHEMA.md.** Add the `[gcp]` table (`project`, `region`, `service_account`, `credentials_file`, `gemini_api`, `buckets`), the `[[gcp.quotas]]` entry (`api`, `metric`, `limit`), and the field on an agent definition that names the secret it expects.
- **docs/context/ARCH.md.** Add the Google Cloud screen to the interface's screens, and a seam between autobot and Google Cloud through the service account.
- **docs/context/GLOSSARY.md.** Add "service account", "key", "secret", "cap" and "Google Cloud screen".
- **docs/specs/active/autobot-agents-and-the-agent-tree-SPEC.md.** AG-12's network list gains the configuration-mode checks of GC-13. Close the open question on how the Agent editor reaches the weather key (GC-9), and say that the agent-side rate control sits behind the cap of GC-10.
- **docs/specs/active/agent-sessions-SPEC.md.** Answer the open question on how the server authenticates: as the service account, through default credentials pointed at a git-ignored key (GC-4, GC-7). Gemini is called on Vertex AI in `us-east5` (GC-11).
- **docs/decisions/.** A new ADR records the service-account identity and the key file, chosen over impersonation, and Vertex AI in `us-east5` as the Gemini API. It answers ADR 0002's open question on how the NUC authenticates. ADR 0002 itself is not edited.
- **.gitignore.** Add `secrets/`, so that any file in the key directory is ignored and not only files that match `service-account*.json`.
- **AGENTS.md.** Add the Google Cloud screen to the interface's screens, and name the key at `[gcp] credentials_file` as a credential file that agents never read.
- **CHANGELOG.md.** Record the Google Cloud screen under Unreleased.

## Open Questions

None.
