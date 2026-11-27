## About a brief

A brief says what the project is, who it is for, and what it is not. It is **prescriptive**: the brief
is the truth, and a feature that contradicts it is out of scope until the brief is changed on purpose.
It is the first document a stranger reads after the README, and the one every spec must agree with.

**It is short.** A brief that runs past a page has started to be a spec. Say what the thing is in a
paragraph a person outside the project can follow, then who uses it, then where it stops.

**"What it is not" is the working half.** Most scope arguments are settled by a sentence that says
what was left out and why. Write those sentences before they are needed.

**It belongs here:** the problem, the people, the boundary, and the name if the name carries meaning.

**It does not belong here:** how it is built (ARCH), the rules it obeys (CONSTRAINTS), behaviour in
detail (a spec), or how to run it (README).

**The failure to avoid** is a brief that grows to describe what was built. When the code and the
brief disagree, the code has left scope or the brief needs a decision; it is never quietly rewritten
to match.
