# Autonomy, and the structure that allows it

*The design idea this repo exists to test.*

---

## The claim

Most systems that act on your behalf are built around a human approval queue. The AI drafts, you
approve, it sends. That shape is safe, familiar, and it caps the system's usefulness at your
attention span.

This repo is built around the opposite question:

> **What can a system do on its own, and what is the structure that allows that to happen?**

The answer to the second half is the whole engineering problem. Remove the human from the loop and
something else has to carry the load — otherwise you have not built autonomy, you have built an
unsupervised process and hoped.

## What it asks permission for

One thing.

```yaml
must_ask_first:
  - "spend more than $50 in one action"
```

Not "send anything to a customer." Not "publish." Not "commit to a date." It researches, drafts,
sends, publishes, schedules, and follows up. It asks before a **noticeable** spend, and before an
action whose **blast radius** is large — and that second one is a measured property, not a
category.

## Why blast radius instead of a category

"Touches a human" is a category. One email to one prospect and a mass send to your entire list are
the *same category* and wildly different consequences.

`blast_radius` — *the scope of impact if the action goes wrong* — is a property the system can
reason about per action. So is `reversibility` — *whether it can be undone, and the cost of
undoing it.* Gating on those keeps the system acting on everything ordinary and pausing only where
being wrong is expensive and unrecoverable.

**A category flag is a proxy. A measured property is the thing itself.** That distinction runs
through this entire codebase, and it is the most common way systems here have been wrong.

## The eight structures that carry the load

None of these is a human. All of them are in the loop.

1. **A measure with a ceiling.** The mission states the number that means done, and
   `reach_and_maintain` means the loop holds it rather than growing it forever. A measure with no
   ceiling gets run to infinity.
2. **The overshoot tripwire.** If the measure is being satisfied by *volume* rather than served,
   the loop halts and asks for a human. This exists because a measure once ran to 50,082 while
   every cycle looked productive. "Did it produce something" is necessary and not sufficient.
3. **The escalation ladder.** A loop that stops being productive is *told* it is stuck, loudly, in
   its own prompt. A stuck loop that is not told it is stuck tries the same thing again with more
   determination.
4. **A hibernation floor.** Below the bottom of the ladder it stops entirely and survives restart,
   rather than thrashing.
5. **A budget cap that cannot be argued past.** A hard cap that can be reasoned around is not a
   cap, and *"just this once"* is how an autonomous system quietly spends someone's month.
6. **The gate fires before the act.** Everything that could stop the work happens while stopping is
   still free.
7. **Record and learn are one transaction.** There is no path where the system acts and does not
   record what happened. An unrecorded act is an act nobody can audit and the loop can repeat.
8. **Nothing auto-promotes.** New concepts and new principles enter as proposals. Promotion to
   authoritative status requires evidence the system produced itself, including a negative
   control — *what did you run that could have proved this wrong?*

Plus the one that matters most for autonomy:

**The system predicts before it acts, and its predictions can be wrong.** Every plan step asserts a
direction. After execution, the assertion is checked. A refuted direction invalidates the plan *at
that step* and triggers a re-plan from there. **A wrong approach is caught by its own predictions
failing, not by a human noticing.**

## The benchmark: running a business

Running a business is not the product. It is the **benchmark for the level of autonomy we are
aiming at**, because of what it demands:

- marketing that fits the product
- product that fits sales
- sales that fit profitability
- and support for the humans on the other end

Read that list again. **Every item is a *fit* relation, not a task.** The difficulty is not doing
marketing well or doing sales well. It is that a decision good for marketing can be wrong for
profitability, a product change good for sales can break support, and **none of those conflicts is
visible from inside the concern that caused it.**

A system that executes each concern competently and independently produces a business that fails:
locally optimal, globally incoherent.

So the claim being tested is not *"the AI did the tasks."* It is:

> **The system held several interdependent concerns coherent at once, without a human arbitrating
> between them — and when they conflicted, it noticed and resolved rather than optimising one and
> silently damaging another.**

That is falsifiable, which is the point. Show a decision that was good for one concern and bad for
another, and ask whether the system caught it. **A benchmark that cannot fail is not a benchmark.**

## The honest state

Written 2026-08-09. Some of the above is built and running; some is specified and not yet built.
This document describes the design under test, not a finished system. The repo's Blackboard
carries which is which, with the evidence for each — including the negative results, of which
there are several.

We hold ourselves to the same standard we ask of the loop: **a claim needs a way to check it, and a
check that cannot come back negative is not evidence.**
