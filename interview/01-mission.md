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
    where: "..."          # the table / dashboard / file the number actually lives in
  horizon: "..."
  boundaries:
    may_act_alone: ["...", "..."]
    must_ask_first: ["...", "..."]
```

**Check before moving on:** read the mission back to them out loud. If they wince, it's wrong. Fix
it now — this is the cheapest moment it will ever be to fix.


## Measures need a CEILING

**A measure with no ceiling gets run to infinity.** A real instance told to reach "60 models" with a bare `count(*)` ran to **50,082** — the loop was told to move the number and did, forever, because nothing marked 60 as the target rather than the floor. Every mission MUST set `measure.target` (the number that means done) and `measure.goal` = `reach_and_maintain` (hit it then hold it, the common case) or `maximize` (rare; more is the point). Also prefer `count(DISTINCT ...)` over `count(*)` unless duplicates are genuinely the unit.
