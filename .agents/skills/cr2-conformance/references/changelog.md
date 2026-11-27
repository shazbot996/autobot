## About a changelog

A changelog says what changed between released versions, for someone using the project. It is
**historical**: a released entry records what shipped and is never edited afterwards. It is the one
historical document that grows in place, and it grows only by adding to **Unreleased** and, at
release time, turning that section into a dated version.

**Write for the person upgrading, not the person who did the work.** "Save keeps a prompt without
running it" is a change a user notices. "Refactored state persistence" is not, unless it changed what
they see, in which case describe what they see.

**Group each version's entries** under Added, Changed, Deprecated, Removed, Fixed and Security, and
leave out the headings that would be empty. Newest version first. Each version heading carries its
number and date.

**It belongs here:** every change a user of this version would notice or need to act on, in a line or
two each.

**It does not belong here:** commit-level detail (that is the git log), which prompt changed which
document (that is the run records), or rationale (that is a decision). A changelog that repeats the
commit log is a commit log nobody reads.

**The failure to avoid** is rewriting history. If a released entry was wrong, the correction goes in a
later version, saying what was wrong. Editing the old entry makes the record say something it did not
say when that version shipped.
