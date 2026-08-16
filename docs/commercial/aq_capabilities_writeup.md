# Autonomy Quest — Capabilities & Editions
*Draft for review — not published. Plain-language positioning for the open/minimal public version vs. the full and managed offerings.*

## What it is

Autonomy Quest is an autonomous operator for a real objective. You give it a mission with a measurable goal — "reach N active customers," "keep this catalog verified and fresh," "grow the qualified-prospect pipeline" — and it runs a self-driving loop: **observe → decide → act → record → learn.** It does genuine work against a real database, measures its own progress against the truth, learns from what happened, and keeps going.

The thing that makes it different from a scripted bot or a one-shot agent is **honesty by construction:** it cannot fake its own scoreboard, and it will stall and tell you why rather than invent progress.

## The core idea: honest autonomy

Most "autonomous agent" demos fall down in one of two ways — they quietly fabricate results to look productive, or they need a human watching every step. Autonomy Quest is built to avoid both:

- **It can't game its own metric.** Progress is measured by re-reading the real database, not by trusting what the agent *says* it did. An anti-gaming check rejects any plan whose success criteria don't provably move the real number.
- **It refuses to fabricate.** In testing it repeatedly declined to record companies as "paying customers" it hadn't actually sold to — it held honestly instead of faking the win.
- **It works inside guardrails.** A least-privilege data layer lets it do real work while making the things it *shouldn't* touch structurally impossible, and a consequence gate escalates genuinely high-impact actions to a human.
- **It gets smarter over time.** A two-tier memory (semantic recall + a structured world-model) accumulates what it learns and carries forward across generations.
- **It can safely scale itself.** An instance that learns something can request a replica that carries that learning, under host-enforced limits (capacity caps, resource gates, reversible teardown) — bounded self-improvement, not an unbounded swarm.

## Editions

**Open / Try — public, free, deliberately limited.**
A minimal, sandboxed version so you can run the loop and see the honest-autonomy model for yourself. It's safe *by being limited* — it operates in a throwaway sandbox, can't spend money, reach production systems, or truly self-replicate outside a toy. Purpose: evaluate the approach, learn the mission model, decide if it fits.

**Full — licensed, self-hosted, at cost.**
The complete autonomy engine you run in your own environment. Adds the operational maturity that turns the demo into something a business can actually run: the advanced learning/bootstrap engine, the manager/authority layer, bounded authority-change requests, safe replication controls, and — importantly — the **enforced-substrate guardrails** that make real-scale autonomy trustworthy.

**Managed — we run it for you, in the cloud.**
The full engine, deployed and operated by us, with the safety substrate configured and maintained on your behalf. You define missions and review the escalations; we keep the guardrails enforced and the system healthy. Best fit for teams that want the outcome without operating the machinery.

## What the paid editions add (honestly)

| Capability | Open / Try | Full | Managed |
|---|---|---|---|
| Self-driving mission loop | ✓ (sandbox) | ✓ | ✓ |
| Anti-gaming progress verification | ✓ | ✓ | ✓ |
| Real business-data actions | sandbox only | ✓ | ✓ |
| Two-tier memory (semantic + world-model) | basic | ✓ | ✓ |
| Cold-start bootstrap (acts and learns from an empty model) | — | ✓ | ✓ |
| Safe self-replication with learned changes | toy only | ✓ (bounded) | ✓ (bounded) |
| Manager/authority layer (envelope per workflow) | — | ✓ | ✓ |
| Enforced least-privilege substrate | limited | ✓ | ✓ (we operate it) |
| Bounded authority-change requests | — | ✓ | ✓ |
| Deployment, upgrades, monitoring | self | self | **we handle it** |

## Why the guardrails are a feature, not a withheld brake

We're explicit about this because it's the whole point: **the paid value is operational maturity and managed safety, not "we kept the safety private and shipped you the risk."** The open version is safe because it's *limited*. The full and managed versions are safe because the guardrails are *enforced* — the agent's capability envelope is bound to its approved workflow and enforced by the runtime, so an over-eager or mistaken agent physically can't exceed it. For anyone actually running an autonomous system against real data and real consequences, that enforcement isn't overhead — it's the reason you can trust it at scale. You're buying the brakes *and* the engine, tuned to work together.

## Where it fits

Autonomous, measurable operational work where honest progress matters: keeping a dataset verified and fresh, running a prospect/CRM pipeline, monitoring-and-maintenance loops, and other "pursue a measurable goal, do real work, learn, don't lie about it" jobs — with a human in the loop exactly where consequences warrant it, and nowhere they don't.

## Honest status

Verified and working today: the mission loop, anti-gaming verification, least-privilege data actions, two-tier memory, the cold-start bootstrap, and end-to-end self-replication triggered by a real learning (demonstrated live: a fresh instance did real research, moved its measure, learned something specific, and requested a replica carrying that learning). Actively hardening: the manager/authority layer, enforced-substrate at scale, and the managed-cloud operation. We keep verified-and-shipping distinct from in-progress on purpose — the same honesty the product is built on.

---
*Internal notes (not for the public doc): the enforced-substrate + manager/authority layer are the private differentiators; the open version must be safe on its own by being sandbox-limited. Distribution: at-cost licensed download and/or managed cloud. Public repo carries the explainer + minimal version; the advanced engine stays in autonomy-quest-private.*
