## About a publication

A publication is an outbound, portable document that synthesizes repository context and source code for an external audience, presentation deck, document portal, or formatting agent.

It is **descriptive**: the repository's internal specifications, architectural decisions, and source code are the source of truth. When upstream context or implementation changes, the publication reflects drift and must be refreshed. It never prescribes code changes and never admits `Plan` or `Apply`.

**Self-contained and portable.** Everything an external reader or downstream publishing pipeline needs must be present in the document itself. Never rely on bare repository references (such as "see `lib/config.ts`" or "refer to `ARCH.md`") that require access to internal source files. Inline domain definitions, architectural narratives, workflows, state models, and summary tables.

**Media and attachments.** Visual assets under `docs/images/` and attachments under `docs/attachments/` may be embedded or linked via markdown paths relative to the publication document's directory (`![alt](../images/...)`, `[title](../attachments/...)` for documents in `docs/publish/`) so that links resolve in Git (e.g. on GitHub) as well as in CR2. Whenever an asset is referenced, the prose must explain what it depicts or establishes.

**Upstream tracking in `describes`.** The frontmatter must include a `describes:` list with globs pointing to the upstream context files (`docs/context/**`, `docs/specs/**`, `docs/decisions/**`) and source code paths (`lib/**`, `app/**`) that the publication synthesizes. This enables CR2's drift checking and staleness indicators.

**It belongs here:** outbound summaries, executive briefings, architecture narratives, end-to-end user workflows, domain schema views, and administrative runbooks written for external readers.

**It does not belong here:** internal developer notes, task plans, code-change specifications, or implementation step lists.

---

## Frontmatter and Subtypes

Every publication requires YAML frontmatter:

```markdown
---
type: publication # or publication:executive-summary, publication:workflow, publication:data-view, publication:admin-guide
title: Document Title
authority: descriptive
describes:
  - docs/specs/**
  - lib/**
---
```

### Required Section Schemas by Subtype

- **`publication` (baseline):**
  1. A one-paragraph summary directly beneath the title (`# Title`)
  2. One subject, treated completely and self-contained for external publication (`## ...`)
  3. `## Open Questions`

- **`publication:executive-summary`:**
  1. A one-paragraph summary under the title
  2. `## Strategic Context and Problem Statement`
  3. `## Value Proposition and Target Audience`
  4. `## Key Architecture and Capability Highlights`
  5. `## Key Metrics and Operating Model`
  6. `## Roadmap and Milestones`
  7. `## Open Questions`

- **`publication:workflow`:**
  1. A one-paragraph summary under the title
  2. `## End-to-End User Journeys and Workflows`
  3. `## Interaction Lifecycles and Trigger Events`
  4. `## Operational State Transitions`
  5. `## Error Recovery and Edge Cases`
  6. `## Open Questions`

- **`publication:data-view`:**
  1. A one-paragraph summary under the title
  2. `## Domain Entity Models and Schema Definitions`
  3. `## Data Flow Pipelines and Transformations`
  4. `## Storage Contracts and Retention`
  5. `## Query and Event Semantics`
  6. `## Open Questions`

- **`publication:admin-guide`:**
  1. A one-paragraph summary under the title
  2. `## Deployment Topologies and Architecture`
  3. `## Administration Workflows and Runbooks`
  4. `## Configuration Matrix and Environment Variables`
  5. `## Security, Access Permissions, and Compliance`
  6. `## Open Questions`
