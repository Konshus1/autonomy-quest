# What this is

*Read this before you run the interview. It is the frame. If you carry the wrong frame into the
interview, you will ask the wrong questions and aim the instance at nothing.*

---

## It is not a DevOps system

The easy misreading is "a fancy CI/CD pipeline with an LLM in it." It isn't. Pipelines execute a
plan a human already made. They do not have a goal, they do not notice when the plan was wrong, and
they do not get better.

## It is an autonomous operations system that evolves toward a mission

Three parts, and all three are load-bearing:

**Autonomous operations.** It does the actual work — continuously, without being told each step.
Not "suggests"; *does*, inside limits you set.

**Toward a mission.** The work is aimed. A human supplies the goal and how winning is measured. The
system is not trying to be generally useful; it is trying to move *your* number.

**That evolves.** This is the part that makes it more than automation. The system observes what
happened when it acted, learns from it, and *changes its own behavior*. Next week's instance is not
the one you installed. The learning loop is the evolution engine; your mission is the fitness
function it evolves against.

Take away the mission and you have a machine that improves at nothing in particular. Take away the
learning and you have a cron job. You need both.

---

## The loop

```
   ┌─────────────────────────────────────────────────────┐
   │                                                     │
   │   observe  →  decide  →  act  →  record  →  learn   │
   │      ↑                                        │     │
   │      └────────────────────────────────────────┘     │
   │                                                     │
   │            all of it aimed at: YOUR MISSION         │
   └─────────────────────────────────────────────────────┘
```

The loop turning is the whole product. Everything else in this repo — the container, the database,
the model gateway, the UI — exists to keep that loop turning and to let you steer it.

This is why `verify.sh` refuses to report success on a system whose loop has never turned. A system
that installed cleanly and has never completed a cycle has not started being what it is.

---

## What it maps onto

The system does not know or care what domain it is in. It knows: a mission, a way to measure
progress, things it is allowed to touch, and a history it learns from. Whatever you can express in
those terms, it can be aimed at.

- **Running a business** *(flagship template)* — pipeline, delivery, cash, customers. It watches the
  numbers, does the work, learns which moves actually moved them.
- **Running a software product** — the reference instance. Ships, watches, learns, ships better.
- **Running a research program** — hypotheses, experiments, results, revised priors.
- **Running anything with a goal and a feedback signal.**

The interview is what performs the mapping. That is why it is mandatory and why **mission is the one
question with no default.** Every other decision has a sane default we chose for you. That one is
yours, and a machine cannot guess it.

---

## Why more than one of these should exist

An instance learns from its own history and nothing else. One history is a narrow education, and it
is *your* history — the failures you happened to hit, in the order you happened to hit them.

Every instance is aimed at a different mission and shaped by a different interview, so instances
**diverge**. They try different work decompositions, different model mixes, different degrees of
autonomy, different guards. Most of what an instance learns is parochial — true of its mission and
useless to yours. But some of it isn't:

- *this way of splitting up work beat that one,*
- *this guard caught a whole class of failure before it shipped,*
- *this budget shape produced more value per dollar,*
- *this model is worth its price for this kind of judgment, and this one isn't.*

Those transfer. They are facts about how autonomous operation works, not facts about your business.

So: run variants, keep what works, and — if you choose — share what generalized. Each instance
improves against its own mission, and — if the sharing bet pays off — the *field* could improve
faster than any single instance alone. Divergence is not a bug to be standardized away; it is how
the search gets done in parallel. That is the bet this project is making, not a result we've
measured yet.

**Sharing is opt-in and off by default.** Your mission and keys are stored on your box, and the
sharing layer sends nothing unless you turn it on and choose what goes. (What the executors and
model APIs you configure transmit is separate — their network behavior is theirs.)

---

## Setting one up for yourself

You do not need to have built this to run it. The interview is the mechanism: you answer questions
about *your* mission in your own words, and the coding agent shapes the instance around the answers
and installs only what those answers call for. That is what makes this a thing you can hand to
someone — not that it comes pre-configured for them, but that it **configures itself by asking.**

The corollary matters as much: an instance nobody sat through the interview for is aimed at nothing.
Don't skip it.

---

## What it will not do

- It stops once **recorded spend** reaches the budget you set. A single in-flight cycle can post past
  the line, and a model whose price it doesn't know is recorded as zero with a loud warning — so it's a
  stop-and-ask threshold, set with headroom, not a guarantee it can't be crossed. (Scope/blast-radius
  is the next line.)
- It will not act outside the **blast radius** you granted it. Beyond that line it asks.
- It will not share your data unless you turn sharing on and choose what goes. (The executors and
  model APIs you configure do transmit — that egress is theirs, not the system phoning home.)
- It will not tell you it succeeded when it didn't. Reporting a clean run on a broken system is the
  worst thing an autonomous system can do, because it destroys the only thing that makes autonomy
  tolerable: that when it says it's fine, it's fine.

---

*Next: [the four invariants](doctrine.md) that keep an autonomous loop honest — the rules underneath the interview, `verify.sh`, and the schema.*
