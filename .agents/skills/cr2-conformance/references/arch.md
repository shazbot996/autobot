## About an architecture document

An architecture document describes what the parts are, how they fit, and where the seams are. It is
**descriptive**. The code is the truth, and when they disagree, the document is stale.

**Describe the shape, not the files.** Architecture is invalidated by a new module, a deleted layer,
or a boundary moving. It is not invalidated by a one-line change inside an existing file, so it
should not be written at a level of detail that makes it so. Coarse and true beats fine and stale.

**It belongs here:** the major parts and what each is responsible for; how data and control move
between them; the boundaries that are deliberate (a module that must not import another, a layer
that must stay free of a framework); where state lives; and what runs where.

**Name the seams.** The most useful sentence in an architecture document is usually the one that says
"these two things talk only through here", because that is what a change will have to respect.

**It does not belong here:** rationale (that is a decision), rules for contributors (CONVENTIONS), or
an inventory of every file. A diagram in text is welcome when it is simpler than the prose it
replaces, and not otherwise.

**The failure to avoid** is describing the architecture the author meant rather than the one the code
has. When derived, write what the imports and calls actually show, and put the difference from what
the names suggest in Open Questions.
