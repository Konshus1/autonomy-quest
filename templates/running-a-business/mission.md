# Template — Running a business

*The flagship. Hand this to someone stuck on `interview/01-mission.md`: people edit far better than
they compose, and a worked example they can argue with beats a blank page.*

---

## The worked example

```yaml
mission:
  objective: "Get to 20 paying customers by the end of Q3."
  measure:
    what:  "count of active paying customers"
    # count(DISTINCT ...) — never count(*). count(*) counts ROWS, so a loop can move the number
    # by re-inserting; count(DISTINCT customer_id) counts the thing you actually care about.
    where: "postgres: select count(distinct customer_id) from subscriptions where status='active'"
    target: 20                 # THE NUMBER THAT MEANS DONE. Required — a measure with no ceiling
                               # gets run to infinity. See interview/01-mission.md "Measures need a CEILING".
    goal: reach_and_maintain   # hit 20, then HOLD it — not "grow forever". At target the loop shifts
                               # to maintenance and honestly does nothing rather than manufacturing work.
  horizon: "2026-09-30, reviewed weekly"
  boundaries:
    may_act_alone:
      - "research prospects and enrich the CRM"
      - "draft outreach, proposals, and follow-ups"
      - "move deals through pipeline stages"
      - "prepare and schedule delivery work"
      - "flag at-risk accounts before they churn"
    must_ask_first:
      - "send anything to a customer or prospect"
      - "spend money"
      - "publish anything publicly"
      - "commit to a price, a date, or a scope"
```

Note what that boundary is doing: the system does **all the work** — the research, the drafting, the
pipeline hygiene, the churn watch — and asks before anything that touches a human or a dollar. The
human's job collapses to *approving and sending*, which is the part they're actually good at.

## What the loop does with it

Every cycle:

1. **Observe** — where's the number? What moved since last time? What's gone stale, gone quiet, or
   gone wrong?
2. **Decide** — given the mission and everything it has learned so far, what's the highest-value
   thing to do right now? Not "what's next in the queue" — *what actually moves the number.*
3. **Act** — do it, within the boundaries. Anything on the ask-first list gets queued for the human.
4. **Record** — what it did, what it cost, what happened.
5. **Learn** — *did that work?* Outreach that gets replies, follow-up timing that closes, the kind
   of prospect that actually converts, the drafts that get sent unedited versus rewritten.

Cycle 100 is not doing what cycle 1 did. That's the point. Cycle 1 is generic; cycle 100 has learned
what works **on your customers, in your market, in your voice** — a thing no template could have
shipped with, because nobody knew it yet, including you.

## What it learns that a human wouldn't bother to

Small, boring, cumulative things — the kind nobody has the discipline to track by hand:

- which prospect *shapes* convert, not which prospects (the pattern, not the anecdote)
- the follow-up interval that actually gets replies, per segment
- which drafts you send unedited and which you always rewrite — and *what you change*
- which deals were doomed early, and the signal that showed it while it was still cheap to walk away
- what it did that you had to undo — the most valuable signal it gets, and the one it should never
  need to be told twice

## Adapting it

Change `objective` and `measure` to your business. Keep the boundary shape — *do the work, ask
before touching humans and money* — until it has earned more room. Then let the ratchet in
`interview/05-budget.md` widen it on evidence, not on how anyone happens to feel that week.
