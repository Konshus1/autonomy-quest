# Interview 1 — Mission

**There is no default. This one they answer or you stop.**

Everything downstream is a means to this end. An instance with a vague mission will run happily and
accomplish nothing, and it will take weeks for anyone to notice, because a system with no target
never visibly misses.

## What you need out of them

Four things. Do not leave this file until you have all four in `instance.yaml`.

| Field | The question behind it |
|---|---|
| `objective` | What outcome are you trying to produce? |
| `measure` | How will we know it's working? What number moves, and where does that number live? |
| `horizon` | By when? What's the review interval? |
| `boundaries` | What may it touch on its own — and what must it never touch without asking? |

## How to run it

Ask plainly, in their words, and **push back on moods.**

> "Grow the business" is a mood, not a mission. So is "be more efficient" and "use AI better."

A mission has to be something an agent can *act on* and *check itself against*. Keep asking "how
would the system know it did that?" until the answer is a number, a state, or an artifact it can
look at.

**Good:**
> Get to 20 paying customers by Q3. The measure is `count(subscriptions where status=active)` in the
> app database. It may write outreach drafts, run the pipeline, and update the CRM on its own; it may
> not send email to a customer or spend money without asking me.

**Not yet good:**
> Grow revenue. *(Which revenue? Measured where? By when? Allowed to do what?)*

If they're stuck, hand them `templates/running-a-business/mission.md` as a worked example and let
them edit it rather than starting from a blank page. People edit better than they compose.

## Boundaries — do not rush this

`boundaries` is what makes autonomy tolerable. Get two lists:

- **May act alone** — the reversible, cheap, local things. Default generously here; a system that
  asks permission for everything is a chatbot with extra steps.
- **Must ask first** — anything that spends money, touches a customer, is externally visible, or is
  hard to undo. Default conservatively here.

The dividing line is roughly *"could we quietly undo this in ten minutes if it was wrong?"* If yes,
let it act. If no, make it ask. It ratchets outward later, on evidence — see `05-budget.md`.

## Record

```yaml
mission:
  objective: "..."
  measure:
    what: "..."
    where: "..."             # the QUERY the number really lives at (ground truth)
    target: 60               # THE NUMBER THAT MEANS DONE. Required — see "Measures need a ceiling".
    goal: reach_and_maintain # reach_and_maintain (hit it, hold it) | maximize (rare; more is the point)
    # target_query: "..."    # OPTIONAL: a live query for the target, when "done" tracks a changing
                             # set (e.g. "every model in the catalog"). Re-read each cycle so the
                             # target cannot drift as the set grows/shrinks.
  horizon: "..."
  boundaries:
    may_act_alone: ["...", "..."]
    must_ask_first: ["...", "..."]
```

**Check before moving on:** read the mission back to them out loud. If they wince, it's wrong. Fix
it now — this is the cheapest moment it will ever be to fix.


## Measures need a CEILING — and a SCOPE. (Learned the hard way.)

**A measure with no ceiling gets run to infinity.** A real instance told to reach "60 models",
measured as a bare `count(*)`, ran to **50,082** — it was told to move the number and did, forever,
because nothing marked 60 as the *target* rather than the *floor*. This is Goodhart's law, and it
will happen to any measure you leave open-ended. Three rules, all required, all from that runaway:

**1. Set `target` and `goal`.** `target` is the number that means done. `goal` is `reach_and_maintain`
(hit it, then hold it — the common case) or `maximize` (rare; more is genuinely the point, e.g.
revenue). A `reach_and_maintain` measure that ever exceeds 1.5× target trips the loop into
hibernation on purpose — that is the "you are running away" alarm.

**2. Count a bounded, DISTINCT set — never `count(*)`.** Prefer `count(DISTINCT <natural key>)`.
`count(*)` counts rows, so a loop can "grow the number" by re-inserting the same things.

**3. If the measure counts a SET, the set must be defined by GROUND TRUTH — never by the loop.**
This is the deep one. "60 models" never said *which* 60, so the loop enumerated every model it could
find. The fix was not just a cap — it was to SCOPE the set to an authoritative external list (the
OpenRouter catalog), SEEDED into an `in_scope` flag the loop cannot set for itself. **A loop that
gets to define its own scope will define it as infinite.** Seed membership from a real source
(a catalog, an allowlist, a fetched list); the loop's job is to keep the *in-scope* set fresh, and
it physically cannot grow scope to move the number.

**When the target tracks a changing set** ("maintain the whole catalog"), make `target` a
`target_query` (e.g. `count(distinct model in_scope)`), re-read live each cycle. Then "satisfied"
means "every in-scope thing is current", and it self-updates as the set changes — a frozen number
would report "done" while new members go stale.

**What "satisfied" looks like when you get this right:** the loop climbs to target, then STOPS
growing — it re-verifies the stalest records to hold freshness, and on a cycle where nothing is
stale it honestly does nothing rather than manufacturing busywork. A maintain-mission's steady state
is *quiet*, not *busy*. If your loop is always "productive", suspect a measure with no ceiling.
