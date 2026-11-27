## About a spec

A spec says what the system must do. It is **prescriptive**: when the code disagrees, the code is
wrong, or the spec is amended deliberately and the amendment recorded.

**Rules are numbered and each carries its reason.** A rule you can cite is a rule you can be held to,
and a reason is what tells a reader whether a case the rule did not foresee falls under it. "IN-12.
An attachment is a fragment" is citable; the paragraph after it is why.

**It belongs here:** behaviour a person could observe, constraints on how it happens, what is
deliberately not done and why, and the questions still open. Small changes are recorded in place,
with a dated revision line, not as a new document.

**It does not belong here:** how the behaviour is built (a plan), why one design was chosen over
another at length (a decision), or the implementation's names, except where a name is itself part of
the behaviour.

**The failure to avoid** is a spec that is secretly a plan: a list of steps dressed as rules. If a
sentence would be obsolete the day the work lands, it belongs in the plan.
