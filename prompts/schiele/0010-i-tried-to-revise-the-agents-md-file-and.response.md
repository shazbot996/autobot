---
run: schiele/0010
at: 2026-11-25T22:42:55.420Z
mode: direct
agent: direct:ask
model: claude:claude-opus-5-5
target: null
template: direct/ask
prompt-sha: 0014185e07ce
template-sha: 44af0dfef32e
commit: b391d03+
attachments: []
read:
  - AGENTS.md
  - prompts/schiele/0007-as-far-as-the-exact-commands-the-agents.prompt.md
  - .cr2/config.json
  - prompts/schiele/0005-as-far-as-the-exact-commands-the-agents.prompt.md
  - prompts/schiele/0008-as-far-as-the-exact-commands-the-agents.prompt.md
  - prompts/schiele/0007-as-far-as-the-exact-commands-the-agents.response.md
  - README.md
  - CHANGELOG.md
outcome: success
usage:
  input: 84920
  output: 2753
  peak: 22197
  window: 1000000
cost: 0
harness:
  provider: claude
  kind: sdk
  workspace: self
  access: read
  turns: 10
  sandbox: false
---

Yes, this is a CR2 bug. The revise agent did its job correctly, and CR2 then threw the result away.

**Evidence from this repo**

- **Your two attempts both failed the same way.** In `prompts/schiele/0005-…prompt.md` and `prompts/schiele/0007-…prompt.md`, the run record shows `mode: revise`, `target: AGENTS.md`, `outcome: failed` and `error: no frontmatter title`.
- **The agent produced a good revision.** The revised `AGENTS.md` it returned is complete and has no frontmatter.
- **`AGENTS.md` has no frontmatter on purpose.** The file says so itself: "It carries no YAML frontmatter because agent tools read it as plain markdown." The Smart Start run checked for exactly this ("valid frontmatter (none on `AGENTS.md`)").
- **Every other Root document has frontmatter.** `README.md` and `CHANGELOG.md` both have a `title:`. So `AGENTS.md` (slot `agents`, type `agents` in `.cr2/config.json`) is the only file that trips the check.

So CR2 has two rules that disagree. The rule that writes documents leaves `AGENTS.md` without frontmatter. The check that accepts a revision demands a frontmatter `title` from every document. Any revise run on `AGENTS.md` will fail until that check is fixed. I can't see CR2's source code from here, so the prompt below describes what goes wrong rather than naming functions.

**Prompt for the CR2 code assist agent:**

```
Bug: revising AGENTS.md always fails with "The revision has no frontmatter title; it was not kept."

Reproduce: in a project whose Root panel has the slot `agents` (path AGENTS.md, type `agents`), run a
revise on AGENTS.md. The revise agent returns a correct full document, but CR2 rejects it. The run
record is written with `outcome: failed` and `error: no frontmatter title` (see autobot
prompts/schiele/0005 and 0007), and the UI shows the error instead of offering to write the revision.

Cause: CR2 deliberately writes AGENTS.md without YAML frontmatter, because agent tools (Claude Code,
Codex, etc.) read it as plain markdown. Smart Start produces it that way, and its own validation says
"valid frontmatter (none on AGENTS.md)". But the step that accepts a revise result requires a
frontmatter `title` from every document, so it rejects every AGENTS.md revision.

Fix:
1. Find where the revise result is checked for a frontmatter title and the error "no frontmatter
   title" is raised. Make it exempt documents that are meant to have no frontmatter: at least type
   `agents` / AGENTS.md. Don't hard-code a path if CR2 already has a better place to record this,
   such as the type definition or a shared "frontmatter-less types" list that Smart Start uses too.
2. For those documents, take the title from the first `# ` heading. If there is no heading, use the
   slot or file name. Use that wherever the title is needed (run record, board label, diff/offer UI).
3. Make sure the accept path doesn't add frontmatter to AGENTS.md when it writes the file. The
   revision should be written exactly as returned.
4. Check every other place that needs frontmatter (create, derive, drift, validate, archive) for the
   same assumption, and apply the same exemption so these documents behave the same everywhere.
5. Keep the current behaviour for every type that has frontmatter: a missing title should still be
   rejected there.
6. Add tests: (a) a revise of a type `agents` document with no frontmatter is accepted and offered
   for writing; (b) a revise of, e.g., a `readme` with no frontmatter title is still rejected with
   the existing error; (c) the AGENTS.md written after accepting a revision has no frontmatter.

Report the files changed and the tests added, and say whether any other built-in type is also meant
to have no frontmatter.
```

If you need the update before the fix lands, you can paste the revised text from run 0007 into `AGENTS.md` yourself. One catch: that revision added two sections the agent may have meant only as notes to you, "Settled without asking" and "Changes needed elsewhere". Check whether you want them kept in the file.
