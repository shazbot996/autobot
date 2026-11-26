---
type: spec
title: "autobot — Rules-First Routing"
authority: prescriptive
status: active
---

# autobot — Rules-First Routing

Rules-first routing is the layer between a local transcript and any AI call. Classic rules validate what was said, answer locally when they can, and otherwise decide which kind of AI call the query feeds and which agent receives it. The owner calls the result "rules-first AI agents" and wants the rule set to grow over time into conversational pathways toward planned agent tools. This stub records what the intake supports; everything else is an open question. It governs the **Rules-first routing** seam in `ARCH.md`.

## Numbered rules, each with its reason

1. **RR-1.** Every transcript passes through the rules layer before any AI agent is called; no query reaches an agent without a rule routing it there. Reason: the owner's stated model is to "validate queries via classic rules application to feed the kinds of AI calls we will build it to support".
2. **RR-2.** A rule may answer a query locally, without any AI call, and such answers are delivered with low latency. Reason: the owner wants rules "that can respond using these rules with low latency".
3. **RR-3.** A rule that routes a query names the kind of AI call it feeds and the agent that receives it. Reason: rules exist to feed "the kinds of AI calls we will build it to support", and each agent is a distinct persistent target (for example the list-making agent).
4. **RR-4.** Rules are data that can be added and composed over time, so that sequences of matching rules form conversational pathways toward agent tools. Reason: the owner wants to "build synthesized procedural set of matching rules over time to create conversational pathways to a set of planned agent tools".

## Open Questions

- What is the form of a rule: pattern, grammar, script, or something else? `SCHEMA.md` leaves the match expression open.
- Where are rules stored and how are they loaded and reloaded on the running server?
- What happens when no rule matches a transcript: silence, a spoken "I did not understand", or a default agent?
- What does "synthesized" mean for rules: are some rules generated (for example by an agent) rather than written by hand?
- How is latency for a local response measured and what counts as low?
