## About a decision record

An architecture decision record captures a decision that was made: the context, the decision, the
alternatives, the consequences. It is **historical**. It is never edited after it is accepted; a later
decision supersedes it, and says so.

**The alternatives are the most valuable part.** A decision without the options it rejected cannot be
revisited honestly, because nobody can tell whether a new idea was already considered. Name each
alternative and the reason it lost.

**It belongs here:** the situation that forced a choice, the choice in terms precise enough to check
against later, every serious alternative and why it was not taken, the consequences including the
bad ones accepted knowingly, and anything left open.

**It does not belong here:** the implementation (a plan), or ongoing rules (a spec). A decision says
what was decided and why; the spec is where it becomes something the code must do.

**The failure to avoid** is a decision record that only lists the upsides. The consequences section
exists for the costs that were accepted, and a record with no costs is a record that was not honest
about the choice.
