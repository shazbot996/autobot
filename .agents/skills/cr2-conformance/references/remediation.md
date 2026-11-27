## About a remediation

A remediation specifies the investigation, reachability validation, and resolution of a security vulnerability or dependency advisory.

It is **prescriptive**: when the repository code is vulnerable or fails verification, the code is in violation and must be repaired. Because remediations are prescriptive, `Derive` is never offered on a remediation document (`AC-2`, `CN-1`). Disagreements between an active remediation and repository code mean the vulnerability is unaddressed.

**Document identifier and numbering:**
- Remediation documents carry a stable identifier `id: REM-<NNNN>` (for example, `REM-0001`) and filename `REM-<NNNN>-<kebab-summary>-REMEDIATION.md`.
- Numbering `<NNNN>` is allocated monotonically across `drafts/`, `active/`, `archive/`, and loose files under `docs/remediations/`.
- Rule citations in remediation requirements carry their own specific prefixes or numbers, keeping the `REM-<NNNN>` document identifier distinct from rule citations (`RM-2`, `CN-10`).

**Lifecycle folders:**
1. `docs/remediations/drafts/`: Unconfirmed vulnerability hypotheses (`status: draft`, `validation.status: unconfirmed`). Visible on the board with a draft badge.
2. `docs/remediations/active/`: Validated, reachable vulnerabilities governing active fixes (`status: active`, `validation.status: confirmed_reachable`). Admits `Plan`.
3. `docs/remediations/archive/`: Immutable historical compliance records for resolved (`status: resolved`), non-applicable (`status: invalid`), or superseded (`status: superseded`) remediations. Immutable compliance records cannot be edited or restored (`RM-11`, `CN-1`).

**Frontmatter Schema (`RM-4`):**

Every remediation requires YAML frontmatter:

```markdown
---
type: remediation
id: REM-0001
title: Document Title
authority: prescriptive
status: draft # draft | active | resolved | invalid | superseded
hypothesis:
  origin: manual # manual | advisory | scanner (or scanner provider name)
  reference_ids:
    - CVE-2023-45853
  suspected_targets:
    - package: zlib
      installed: "1.2.11"
      fixed: ">=1.3.0"
      manifest: package.json
validation:
  status: unconfirmed # unconfirmed | confirmed_reachable | not_applicable
  confirmed_at: null # ISO-8601 string when validated, or null while unconfirmed
  call_sites:
    - file: lib/archive.ts
      lines: "45-62"
resolved_by:
  commit: null # Git commit SHA when status is resolved, else null
  author: null # commit author when status is resolved, else null
  at: null # ISO-8601 timestamp when status is resolved, else null
describes:
  - package.json
  - lib/archive.ts
---
```

**Field Invariants (`RM-5`):**
1. **`type` and `authority`**: `type: remediation` and `authority: prescriptive` are fixed.
2. **`status`**: Aligns with validation and lifecycle folder:
   - `draft`: unconfirmed hypothesis in `docs/remediations/drafts/` (`validation.status: unconfirmed`).
   - `active`: confirmed reachable in `docs/remediations/active/` (`validation.status: confirmed_reachable`).
   - `resolved`, `invalid`, `superseded`: closed compliance records in `docs/remediations/archive/`.
3. **`hypothesis`**:
   - `origin`: scanner-agnostic origin string (`"manual"`, `"advisory"`, `"scanner"`, or scanner identifier).
   - `reference_ids`: list of external/internal vulnerability identifiers (CVEs, GHSAs, OSV IDs).
   - `suspected_targets`: descriptors with `package`, `installed`, `fixed`, and `manifest` (relative path).
4. **`validation`**:
   - `status`: `"unconfirmed"`, `"confirmed_reachable"`, or `"not_applicable"`.
   - `confirmed_at`: ISO 8601 timestamp string when transitioned out of `"unconfirmed"`, or `null`.
   - `call_sites`: list of `{ file, lines }` where vulnerable symbols or code paths are invoked.
5. **`resolved_by`**: `commit`, `author`, and `at` are `null` while `draft` or `active`, populated upon resolution.
6. **`describes`**: The deduplicated union of explicit describes, suspected target manifests, and validation call site files so staleness and planning automatically inspect affected files.

**Required Body Sections (`RM-6`):**
1. A one-paragraph summary directly beneath the title (`# <Title>`)
2. `## Vulnerability hypothesis and suspected targets`
3. `## Reachability and call-site validation`
4. `## Remediation requirements and verification`
5. `## Open Questions`

**It belongs here:** vulnerability descriptions, suspected and confirmed package targets, call-site reachability evidence, upgrade and remediation requirements, verification steps, and resolution compliance details.

**It does not belong here:** multi-file implementation step lists (which belong in a paired plan in `docs/plans/`), architectural redesigns (which belong in ADRs), or feature specifications.
