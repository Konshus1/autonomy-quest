# Curiosity — architecture + roadmap

> **Autonomy Quest roadmap item.** Curiosity is **built and proven-out in the engine today** — it has **not** been
> moved into Autonomy Quest yet. It stays in the engine until it is validated for the bootstrap agent, and then it
> moves here. This doc is the roadmap entry: it describes what curiosity does *now* and where it is *headed*.
>
> An honest-tier design doc. Every claim is marked by maturity —
> **[BUILT, demonstration-tier]**, **[MEASUREMENT-ONLY]**, **[DESIGNED, not wired]**, **[ROADMAP]** — so you can
> see exactly what runs today versus where we're headed. Curiosity is a self-improvement pass you **invoke**,
> **not** a continuously-running autonomous loop. Nothing below is "always-on," and we say so on purpose.

## The idea

Curiosity is a self-improvement drive that sends an agent to the places where it can *actually learn* — and it is
**grounded**: every proposed improvement is checked against real code, not merely plausible. The goal is signal,
not novelty for its own sake.

## Two modes

1. **Pattern generalization** — *"I see pattern X here — where else might X apply?"* Deterministic graph analysis
   over the system's structure. Zero model cost.
2. **Epistemic curiosity** — *"I don't know this area well — let me go look."* Scores uncertainty across the
   system and runs **cost-bounded** investigations where the payoff is highest.

## The one signal

```
curiosity_score = uncertainty × instrumental_priority
```

- **uncertainty** — how much is unknown or incomplete in a region of the system.
- **instrumental_priority** — a goal-conditioned gate. It targets the *learnable-but-not-yet-known* sweet spot —
  reward where understanding is *improving*, not where things are merely *new* — **gated by "does this matter for
  the goal."** The result: curiosity chases **signal, not noise.**

One nice property: the same signal that tells an *agent* where to learn also tells a *communicator* where to meet
a *reader* — at their own frontier. One signal, two jobs.

## What it does today  **[BUILT, demonstration-tier]**

- Detects structural gaps → scores them by the formula above → runs a **bounded** investigation → merges what it
  discovers back into a knowledge graph.
- **Runs on demand** — a self-improvement pass you invoke — **not** a continuously-running fleet loop. This honest
  tier matters: it's demonstration-tier, not autonomous-always-on.
- **Suppressed in "exploit" mode** — when the system should consolidate rather than explore, curiosity stands down.
- **Honesty gate:** every surfaced candidate must be **grounded to real code**; anything it cannot verify is
  **discarded**, not asserted.
- **Worked example:** a recent self-directed sweep surfaced **51 grounded improvement candidates** across ~10 areas
  of the system — each tied to real code — and its honesty gate **discarded 1 candidate it could not verify.** That
  discard is the point: a system that throws out what it can't prove is one you can trust the other 51 from.

## What measures, but does not yet act  **[MEASUREMENT-ONLY]**

Signals for prediction-error and learning-completeness currently **score** the system's health
but do **not** yet trigger action on their own. That gap — measure vs. resolve — is the honest
frontier, and it's exactly what the roadmap closes.

> **Model coherence has moved out of this section** — it is now wired to curiosity (below).

## Roadmap — from measure to resolve

- **First bounded "measure → resolve" step is built and locally gated** **[BUILT, gated]**: a validated anomaly
  deterministically opens **one** bounded, **human-reviewed** investigation with an immutable receipt — fail-closed,
  no autonomous action. **Extension path** **[ROADMAP]**: turn more health-signals into the same kind of gated
  investigation (learning-gap follow-up / re-evaluate predictions / ask-a-human-or-replan), each with rising
  evidence and authority requirements.
- **World-model incoherence triggers a curiosity probe** **[BUILT, gated]** (BB #2301 / G1): when the causal
  model's predicted_certainty is contradicted by the outcome (Gate 4 model-coherence fails), the loop now ALSO
  spends a bounded curiosity cycle staging a **falsifiable hypothesis** on the surprised edge's graph
  neighborhood. **Honest scope — read this before claiming it:**
  - **Additive to escalation, never a replacement.** The escalate verdict is unchanged — the human is still
    paged. The probe is a *bonus*, not a substitute for human escalation.
  - **Staged-inert only.** The proposal goes through the same `stage_curiosity_learning` path as any curiosity
    output: inert until it passes `share → review → promote` with a behavior-change test. It cannot change
    behavior directly.
  - **Budget/ratchet gated.** The probe consumes the same standing curiosity budget (`cycles_per_day`,
    `max_cost_usd_per_day`) and the same evidence-ratchet as the frontier path — one shared `_budget_gate`.
    Ratcheted to zero → no probe; the contradiction still escalates to the human.
  - **Ground-truth-seeded frontier (BB #587).** The probe's frontier is derived from *recorded surprise
    evidence* on the causal edges (where the world disagreed with the model), never from curiosity's own
    assertion of what's interesting.
  - **Best-effort, post-commit.** A probe hiccup (API down, fetch failure, explore failure) never fails the
    durable cycle and never alters the verdict.
  - **Backlog overshoot tripwire (BB #587) — BUILT.** The same guardian overshoot tripwire that
    bounds the frontier-driven path now bounds the coherence path's exploration metric: the
    **surprise backlog** — the uncapped count of low-coherence edges in the causal store (the
    capped probe frontier's length could never trip it, so the true total is fetched). Blown
    past **1.5× `curiosity.frontier.target`**, the probe is **refused** (`overshoot` in the
    reason) after the shared budget gate passes — a backlog runaway is treated as a measure
    being gamed by volume, not a system learning. The escalate verdict is untouched, a missing
    backlog count disables the tripwire for that cycle (never the escalation), and the floor
    below the tripwire stays budget+ratchet+cost.
- **Per-assertion belief-coherence tripwire** **[DESIGNED, not wired]**: before asserting a low-knowledge,
  high-consequence fact, verify it or hedge it. Fully designed; not yet wired into a live turn.

## The science it draws on

- **Schmidhuber** — intrinsic reward is the *rate of improvement* in compressing what you observe (learning
  progress), **not** raw novelty. Pure surprise rewards noise; a screen of static is maximally surprising and
  teaches nothing.
- **Chollet** — intelligence as *skill-acquisition efficiency* over a distribution of tasks, given priors and
  experience (a *measure* of intelligence, distinct from Schmidhuber's *reward mechanism*).
- **Vygotsky (Zone of Proximal Development)** — learners grow just beyond their current ability, with support.
- **Our synthesis** — compression-progress and the ZPD point at the *same* sweet spot; we operationalize it with
  goal-conditioned `instrumental_priority` gating so curiosity stays useful, not random.

## Honesty commitments (why the numbers are trustworthy)

- **Demonstration-tier, invoked** — not a live always-on loop. We won't imply otherwise.
- **Grounded** — every improvement is checked against real code; unverifiable candidates are discarded.
- **Roadmap is labeled** — designed/roadmap items are marked as such, never described as shipped.
