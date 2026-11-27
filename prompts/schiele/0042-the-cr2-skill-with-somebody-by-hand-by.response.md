---
run: schiele/0042
at: 2026-11-27T03:47:51.854Z
mode: direct
agent: direct:ask
model: claude:claude-opus-5-5
target: null
template: direct/ask
prompt-sha: 458865204a2d
template-sha: 44af0dfef32e
commit: ecc2fb8
attachments: []
read:
  - AGENTS.md
  - .cr2/config.json
  - prompts/schiele/0041-the-cr2-skill-with-somebody-by-hand-by.prompt.md
  - .cr2/session.json
  - .run/cache/skills-mirror/8a85a5e8be3478a5/README.md
  - .gitignore
outcome: success
usage:
  input: 363924
  output: 2893
  peak: 58822
  window: 1000000
cost: 0
harness:
  provider: claude
  kind: sdk
  workspace: self
  access: read
  turns: 18
  sandbox: false
---

I can't point you to the CR2 files from here, because CR2 itself isn't in this repository. Only the files it creates for this project are. (Your question looks like it lost its first few words, so I've read it as "if I wanted to share CR2 with someone by hand, what would I send them?")

**CR2's own code isn't here.** `.cr2/config.json` points to files that don't exist in this checkout: `prompts/builtin/types/remediation.md`, `prompts/builtin/examples/publication.md`, and prompt templates like `direct/ask`. There's no `prompts/builtin/` directory and no CR2 source, so those ship with CR2 wherever it's installed. Nothing I can read names that location. You'd find it on your machine with something like `which cr2` or your package manager's list of installed packages, and that install directory is what you'd send. I can't tell how it was installed or what it needs to run.

**What CR2 keeps in this project:**

| Path | What it is | Worth sending? |
|---|---|---|
| `.cr2/config.json` | The project setup: board panels, document types and their sections, models for each agent, cost limits | Yes. It's the only part that carries over, but see below. |
| `.cr2/session.json`, `.cr2/transcript.json` | This session's conversation history | No. `.gitignore` marks them "local state, never shared". |
| `prompts/schiele/NNNN-*.prompt.md` / `.response.md` | Write-once records of each run | Only as examples. They're your history on autobot. |
| `.run/cache/skills-mirror/…` | CR2's clone of Google's public skills repo (`npx skills add google/skills`) | No. It's ignored by git and CR2 rebuilds it. |

**How portable `config.json` is:**
- **Portable:** the panel layout (`docs/context`, `docs/decisions`, `docs/specs`, `docs/plans`, `docs/publish`, `docs/remediations`), the document types and their required sections, and the agent definitions with their presets. That's essentially a reusable project template.
- **Tied to you:**
  - The `agy` provider names your Google Cloud project: `"project": "cbs-altostrat-dev"`, `"billing": "enterprise"`. It's disabled, but remove it before sending.
  - The model IDs (`claude-opus-5-5`, `gemini-4-argon` and so on) only work if the other person has access to those models.
  - The cost limits (`maxCostUsd`) are your budgets.
- **Specific to autobot:** the root slots `README.md`, `CHANGELOG.md` and `AGENTS.md`, and the Core slot files. These are generic names, not autobot content, so they'd work in any repo.

**What to send:**
1. CR2's install directory or package, from wherever it lives on your machine.
2. Optionally `.cr2/config.json` as a starting template, with the `agy` block removed.
3. Nothing from `prompts/schiele/`, `.cr2/session.json`, `.cr2/transcript.json` or `.run/`. They're your history or a cache.

Nothing secret is stored in `.cr2/`. Credentials come from your own logins (the Claude CLI, gcloud) and `.env` files, so the other person would sign in with their own accounts.
