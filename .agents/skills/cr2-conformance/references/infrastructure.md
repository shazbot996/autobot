## About an infrastructure document

An infrastructure document records what exists outside the repository for the project to run on:
accounts, cloud projects, domains, services, buckets, budgets, with their identifiers. It is
**descriptive**, and the truth it describes is the deployed environment, not the code. When they
disagree, the document is stale.

**Not every project has one.** A library or a command-line tool runs on nothing of its own. The slot
is optional for that reason, and an empty one is not a gap.

**No secret belongs in it.** Name where a secret is kept and what it is for. Never its value, and
never anything that would let a reader obtain it.

**Say what defines the environment.** If files in the repository declare it (infrastructure as code,
a desired-state folder, a deploy script), list them under `describes`, so a change to them marks this
document as possibly stale without anyone asking a model.

**It belongs here:** one section per account or environment, each resource with the identifier a
person would need to find it, what created it, and what checks it. Things that are planned and do not
exist yet are marked as such.

**It does not belong here:** why a platform was chosen (a decision), how the parts of the code fit
(ARCH), or steps to deploy (README or a plan).

**The failure to avoid** is a document that records what was intended. Write what a person looking at
the environment today would find, and put the difference from the intent in Open Questions.
