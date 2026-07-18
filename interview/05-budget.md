# Interview 5 — Autonomy budget

**Default: start small on both budgets. Ratchet up on proof, never on vibes.**

There are **two** budgets and people conflate them. Ask about both separately.

## Budget 1 — Money

What may it spend on models, per day and per month, to keep the loop turning?

| | |
|---|---|
| **Default** | $5/day soft, $100/month hard |
| **Soft cap** | It slows the cadence and tells you |
| **Hard cap** | It stops and asks. It does not "just this once" past a hard cap. |

Anchor this against the monthly estimate from `04-models.md`. If the default budget can't afford the
cadence they want, say so *now* rather than letting them discover it when the loop halts.

## Budget 2 — Scope (blast radius)

How far may it act without asking? This is the budget that actually scares people, and it's the one
worth being careful with.

It maps directly to `mission.boundaries` from `01-mission.md`. Restate that boundary as an autonomy
level so the system can enforce it:

| Level | It may… | Good for |
|---|---|---|
| **`propose`** | Do nothing. Draft everything, act on nothing. | Someone who doesn't trust it yet. Costs almost nothing. Also teaches them nothing about whether it works. |
| **`act-reversible`** *(default)* | Act alone on anything cheaply undoable. Ask before anything else. | Almost everyone. |
| **`act-external`** | Also touch the outside world — send, publish, spend within budget. | Once it has earned it. |
| **`act-broad`** | Act freely inside the mission's boundaries. | Only after months of evidence. |

**Default to `act-reversible`.** It's the level where the system does real work and real mistakes
stay cheap. `propose` feels safe and is mostly a way to build a system you never learn to trust.

## The ratchet — this is the important idea

Autonomy is **earned, and earned against evidence.** Set the ratchet now so it isn't relitigated
every week on the basis of how everyone happens to feel:

> "Every two weeks, look at what it did on its own. If nothing it did autonomously had to be undone,
> it moves up one level. If something did, it moves down one and we look at why."

Write that rule into the instance. A system that asks for more autonomy gets it by *showing its
record*, not by asking nicely — and one that abused it loses it automatically, without anyone having
to have an awkward conversation about it.

## Record

```yaml
budget:
  money:
    daily_soft_usd: 5
    monthly_hard_usd: 100
  autonomy:
    level: act-reversible        # propose | act-reversible | act-external | act-broad
    ratchet:
      review_every: 14d
      promote_if: "no autonomous action required rollback in the window"
      demote_if: "any autonomous action required rollback"
```

## Optional curiosity budget

Curiosity is off by default. Turn it on only when the instance has an external frontier worth
checking between mission cycles: a catalog, allowlist, registry, or fetched authoritative set the
loop cannot edit.

This is a bounded appetite, not a second mission. It gets its own small standing budget, it is cut
before mission work under cost pressure, and its output is staged inertly. A curiosity result does
not affect behavior until it goes through review and promotion, and promotion requires local
behavior-change evidence.

Record only if opted in:

```yaml
curiosity:
  enabled: true
  budget:
    cycles_per_day: 1
    max_cost_usd_per_day: 1
  frontier:
    source: "select id, topic from external_catalog where in_scope = true order by priority desc"
    authority: "external_catalog"
    target: 20
    goal: reach_and_maintain
    max_items_per_cycle: 1
  ratchet:
    window_cycles: 10
    shrink_factor: 0.5
    floor_cycles_per_day: 0
```

Do not seed `frontier.source` from the loop's own work, runs, learnings, measurements, heartbeat, or
hibernation tables. That lets the loop define its own frontier, and it will define it as infinite.
