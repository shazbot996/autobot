## About a schema document

A schema document describes the shapes the system moves: records, files, messages, configuration. It
is **descriptive**. The code is the truth, and a schema document that disagrees with it is stale.

**Describe shapes, not code.** Each shape gets its fields, their types, which are required, what each
one means, and where the shape lives or travels. Name the file or type that defines it so a reader can
check, and so a drift check can.

**It belongs here:** persisted formats (files, frontmatter, databases), wire formats (API bodies,
events), and configuration, with an example of each that is small enough to read in one look.

**It does not belong here:** behaviour, which is a spec's job, or history, which is a decision's.
A field that used to exist is not part of the schema; if it still appears in old data, say so once,
as a compatibility note.

**The failure to avoid** is invention. A derived schema documents what the code declares and nothing
it merely implies. Where the code is loose, an `unknown` passed through, a field read but never
validated, say that plainly rather than guessing a shape the code does not enforce.
