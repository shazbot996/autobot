## About a plan

A plan says how a spec will be built. It is **prescriptive** until the work lands, and then it is a
record of what was built, with a note at the top saying what landed and what deviated.

**Frontmatter & Provenance:**
- Frontmatter must include `describes:` listing the relative paths of files created or modified.
- The document body must begin with `Implements the <Spec Title> spec (\`docs/specs/<spec-file>.md\`).`

**It has a shape, and the shape is the point:**
1. A compact delta table (`| Spec Rule | Current Implementation | Planned Change |`) with no extra whitespace padding.
2. Numbered steps in dependency order, each naming its files, each ending in something visible on screen, each with a "Done when" a person could check.
3. Choices the spec left open and how they were made.
4. Out of scope boundaries.
5. Open questions.

**Every step ends in something that can be seen.** A step that ends in "the module is refactored" has
no way to be confirmed. A step that ends in "binding a document cycles Revise and Drift check" does.

**It belongs here:** the order of the work and why, the files each step touches, what each step makes
observable, and the decisions the work forces that the spec did not settle.

**It does not belong here:** the behaviour itself (that is the spec it implements), or rationale for
the design (a decision). A plan that re-argues its spec is a plan that has not accepted it.

**The failure to avoid** is a plan written after the fact that reads as a forecast. When the work has
already happened, say so at the top.
