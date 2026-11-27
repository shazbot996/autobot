---
name: cr2-conformance
description: For agents working in a cr2 project while cr2 is not running, so every file they write keeps the layout, frontmatter, authority and provenance rules cr2 enforces.
cr2SchemaVersion: 3
---

# cr2 conformance

This skill is a descriptive copy of cr2's prescriptive rules (RL, SCHEMA.md §2, CN, SP and PK), regenerated from them; where it disagrees with them, the rules win and this file is rewritten.

## Read first

Before you write anything, read this project's own context, in this order, wherever each file exists:

1. `AGENTS.md` (or `CLAUDE.md` where the project uses that name)
2. `docs/context/BRIEF.md`
3. `docs/context/GLOSSARY.md`
4. `docs/context/CONVENTIONS.md`
5. `docs/context/CONSTRAINTS.md`
6. `docs/context/ARCH.md`

Those documents hold the project's own rules. This skill holds only what cr2 imposes on every project, and the two never repeat each other.

## 1. Layout and naming

Documents live under `docs/`, one folder per kind:

| Folder | Holds | File name |
|---|---|---|
| `docs/context/` | Singletons, one per subject | `NAME.md`, upper case |
| `docs/decisions/` | Decision records | `NNNN-slug-ADR.md` |
| `docs/specs/active/`, `docs/specs/archive/` | Specs | `slug-SPEC.md` |
| `docs/plans/active/`, `docs/plans/archive/` | Plans | `slug-PLAN.md` |
| `docs/remediations/drafts/`, `active/`, `archive/` | Remediations | `REM-NNNN-slug-REMEDIATION.md` |
| `docs/publish/` | Publications, flat | `slug-PUB.md` |

- A new spec or plan goes in `active/`, never in `archive/`.
- `NNNN` is the next unused four-digit number across the whole folder, subfolders included.
- `slug` is lower-case kebab-case derived from the title.
- Nothing in `docs/attachments/` or `docs/images/` is a document, whatever its extension.
- When `.cr2/config.json` gives a collection panel its own `dir`, `suffix` or `numbered`, follow that instead.

## 2. Root documents

`README.md`, `CHANGELOG.md` and `AGENTS.md` live at the repository root. `README.md` and `CHANGELOG.md` carry frontmatter. `AGENTS.md` has no frontmatter, and you never add any.

## 3. Frontmatter

Every managed document under `docs/`, and `README.md` and `CHANGELOG.md`, starts with YAML frontmatter:

| Field | Required | Allowed values |
|---|---|---|
| `type` | yes | a type configured in `.cr2/config.json` `types`, or `publication:<subtype>` |
| `title` | yes | the document's title |
| `authority` | yes | `descriptive`, `prescriptive` or `historical` |
| `status` | yes | `active`, `draft`, `done` or `superseded` |
| `describes` | specs and descriptive documents | repository-relative paths or globs |
| `parent` | plans | path of the parent spec under `docs/specs/` |
| `skills` | no | skill ids the document needs |
| `provenance` | no | append-only list, see "Provenance without cr2" |
| `steps` | no, plans only | step progress, see "Plan progress without cr2" |

## 4. Authority sets which edits are allowed

- **descriptive**: the code is the truth. Rewrite the document to match the code.
- **prescriptive**: the document is the truth. Change the code to match it, or amend the document on purpose and add a dated revision line saying what changed and why.
- **historical**: never edit. A decision record in `docs/decisions/` is never edited in place; write a new, higher-numbered one that supersedes it. A released section of `CHANGELOG.md` is never edited, not even to correct it; new work goes under `## Unreleased`.

## 5. Required sections per type

Each type has required `## ` sections. Read them from `.cr2/config.json`, under `types[<type>].sections`. When the project has no such entry, use the matching guide in this skill's `references/<type>.md`. When the project has its own guide at `.cr2/types/<type>.md`, that guide wins over `references/`.

## 6. Rule IDs

Rules are written `XX-N`, with suffixes such as `XX-Na`. Each spec owns one prefix that no other spec uses. Before choosing a prefix for a new spec, check it against every spec under `docs/specs/`. Every `describes:` glob must match at least one file in the repository.

## 7. `.cr2/` is the application's furniture

`.cr2/` holds cr2's configuration, overrides and local state. It holds no documents. Do not edit anything in it unless the task is about it.

## 8. Never touch

- `.env` and `.env.*`: never read or write them.
- Anything inside `.git/`.
- The run records under `prompts/<author>/`: the numbered `NNNN-slug.prompt.md` and `NNNN-slug.response.md` files.

## Provenance without cr2

**Never write a run record.** Do not create `prompts/<author>/NNNN-slug.prompt.md` or `NNNN-slug.response.md`, and never write `run:` or `saved:` frontmatter anywhere. Those numbers are handed out only by cr2, at dispatch.

Leave provenance as a hand-written prompt instead:

1. Resolve the author (below).
2. Write the request you were given to `prompts/<author>/<slug>_prompt.md`. The file has no frontmatter. Under a `#` title, first say that it was written outside cr2 and name the harness, the model and the date, then quote the request verbatim. You may put a summary of what you did beside it as `prompts/<author>/<slug>_response.md`.
3. Append one entry to the `provenance:` list of every document you changed, and never rewrite an earlier entry:

   ```yaml
   provenance:
     - written: <author>/<slug>
       at: 2026-01-31T12:00:00Z
       mode: revise
   ```

   `at` is ISO 8601. `mode` says what you did to that document: `create`, `revise`, `apply`, and so on.
4. Once finished, neither prompt file is edited again.

**Resolving the author.** Take the first of these that gives a non-empty value:

1. the `CR2_USER` environment variable;
2. `author` in `$XDG_CONFIG_HOME/cr2/settings.json`, or `~/.config/cr2/settings.json` when `XDG_CONFIG_HOME` is unset;
3. `author` in the project's `.cr2/config.json`;
4. `git config user.name`.

Slugify it as cr2 does: lower-case, Unicode NFKD, every run of characters outside `a-z0-9` replaced by `-`, leading and trailing `-` removed, at most 80 characters. The name `builtin` is refused. If no layer gives a name, stop and ask the person; do not fall back to the OS user name or to a placeholder.

## Plan progress without cr2

When you finish a step of a plan and its `Done when` holds, stamp it in the plan's frontmatter:

```yaml
steps:
  total: 6
  completed: [1, 2]
  current: 3
  status:
    1: { state: "completed", written: "<author>/<slug>", at: "2026-01-31T12:00:00Z" }
    2: { state: "completed", written: "<author>/<slug>", at: "2026-01-31T12:40:00Z" }
```

- Set `steps.status[N]` to `{ state: "completed", written: <author>/<slug>, at: <ISO 8601> }`, add `N` to `steps.completed`, and move `steps.current` to the next pending step.
- A step you started but could not finish is stamped `state: "failed"` with the same fields, and `steps.current` stays on it.
- Use `written`, never `run`.

This stamp and your `provenance:` entry are the only changes you make to a plan. Never edit its body text to record progress.

## Before you report

Run the free check and fix every failure it names:

```bash
node .agents/skills/cr2-conformance/scripts/check.mjs
```

It needs only Node, makes no network call and calls no model.
