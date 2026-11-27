## About an AGENTS file

AGENTS.md is read by coding agents before they touch the repository, and by nobody else with the same
attention. It is **prescriptive**: it says what an agent must do here, and an agent that does
otherwise is wrong, not the file.

**It belongs here:** the commands that build, check and test; the conventions an agent would
otherwise violate on its first edit; where things live when that is not obvious from the tree; what
must never be done (edit a generated file, commit a secret, reformat a file it was not asked to
touch); and the one or two facts about this codebase that contradict what an agent would assume.

**It does not belong here:** a tour of the architecture, the project's history, or anything a person
reads for understanding rather than an agent reads for instruction. An agent follows every line, so
every line costs something.

**Write imperatively and specifically.** "Run `npx tsc --noEmit` before reporting" is an instruction.
"Please make sure the code is high quality" is not, and an agent that reads it learns nothing it can
do differently.

**The failure to avoid** is length. Each rule an agent is given dilutes the others. If a rule would
not change what an agent actually does here, it does not belong in this file.
