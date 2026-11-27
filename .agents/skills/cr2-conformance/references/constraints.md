## About a constraints document

Constraints are the guardrails: what must not change, what must always hold, what the system must
never do. They are **prescriptive**, and more strictly than anything else here. When the code and a
constraint disagree, the code is in violation, and the fix belongs in the code or in a deliberate,
recorded amendment to the constraint. Never in quietly rewriting the constraint to match.

**This is the document the authority model exists to protect.** A loop that reconciled documents to
the code would delete a guardrail at the exact moment it was violated and leave a green checkmark. A
constraint is only worth having if the code cannot talk it out of existence.

**Each constraint is testable.** "Nothing is written that has not been shown" can be checked.
"The system should be safe" cannot. State the constraint, then the reason, then how a violation would
be noticed.

**It belongs here:** invariants, limits, things that must never be automated, data that must never
leave a boundary, compatibility that must not break, and the failures that already happened once and
must not happen again.

**It does not belong here:** preferences, conventions, or a description of how the system currently
behaves. That is what the code says; this says what it is not allowed to say.
