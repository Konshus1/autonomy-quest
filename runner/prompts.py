"""The three prompts the loop runs, and the schemas that pin their replies.

Kept in one file because these ARE the loop's thinking, and they should be readable as a unit
by anyone asking "what does this system actually do every cycle?"

Every schema is `additionalProperties: false` and lists its required fields. That is not
pedantry: the executor validates against these, and a reply that doesn't match is a FAILURE
rather than something we half-parse. A loop that acts on a half-understood reply will file the
misunderstanding as a learning and carry it forever.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 1. DECIDE — what is the highest-value thing to do right now?
# ---------------------------------------------------------------------------

DECIDE_SCHEMA = {
    "type": "object",
    "properties": {
        "do_nothing": {"type": "boolean",
                       "description": "true if nothing is genuinely worth doing this cycle"},
        "kind": {"type": "string"},
        "summary": {"type": "string"},
        "rationale": {"type": "string",
                      "description": "why THIS moves the mission's number"},
        "reversible": {"type": "boolean",
                       "description": "could we quietly undo this in ten minutes if it was wrong?"},
        "spends_money": {"type": "boolean"},
        "touches_human": {"type": "boolean",
                          "description": "does it contact a customer, prospect, or any person?"},
        "commits": {"type": "boolean",
                    "description": "does it promise a price, a date, or a scope?"},
    },
    "required": ["do_nothing", "kind", "summary", "rationale",
                 "reversible", "spends_money", "touches_human", "commits"],
    "additionalProperties": False,
}


def decide(world: dict, template: str, guidance: str = "") -> str:
    m = world["mission"]
    learned = "\n".join(f"- {l['insight']}" for l in world["learnings"]) or "(nothing yet — first cycles)"
    recent = "\n".join(f"- {r['summary']}: {r['outcome']}" for r in world["recent_runs"]) or "(no runs yet)"
    parked = len(world.get("parked") or [])

    nudge = f"\n!!! {guidance}\n" if guidance else ""

    return f"""You are the DECIDE phase of an autonomous operations loop. You run continuously,
aimed at one mission. Pick the single highest-value thing to do RIGHT NOW.
{nudge}

Not "what's next in a queue" — what most moves the mission's number, given everything learned so
far. Using what you have learned is the entire point of having learned it.

MISSION:  {m.objective}
MEASURE:  {m.measure.what} — currently {world['now']}
HORIZON:  {m.horizon}
TEMPLATE: {template}

YOU MAY DO ALONE:  {m.boundaries.may_act_alone}
YOU MUST ASK FIRST: {m.boundaries.must_ask_first}

WHAT YOU HAVE LEARNED SO FAR:
{learned}

RECENT RUNS:
{recent}

{parked} item(s) are already parked waiting on the human — don't queue more of the same.

BATCH THE WORK WHERE IT BATCHES. Every cycle costs one rate-limit slot on a subscription (and a
decide+act+reflect round trip on API mode). If the work divides into naturally similar items —
twenty models to research, thirty prospects to enrich — do a BATCH IN ONE CYCLE rather than one
item per cycle. Twenty items in one cycle beats twenty cycles of one item by a factor of twenty,
and on a rate-limited plan the difference is between finishing today and finishing next week.

Observed on a real box: one instance chose "research 20 models" per cycle and reached its target of
60 in four cycles. Another chose one model per cycle, hit the plan's rate limit, and was still at
1/60. Same mission, same kit. The batching decision WAS the difference.

If nothing is genuinely worth doing, set do_nothing and say so honestly. Inventing busywork to
look productive is worse than idling: it costs money and teaches the loop nothing.

Be honest about the flags. `reversible` means we could quietly undo it in ten minutes.
`touches_human` means a real person receives something. Getting these wrong is how an autonomous
system does something it was never allowed to do."""


# ---------------------------------------------------------------------------
# 2. ACT — do it, and report what ACTUALLY happened
# ---------------------------------------------------------------------------

ACT_SCHEMA = {
    "type": "object",
    "properties": {
        "outcome": {"type": "string",
                    "description": "what actually happened, in plain language, including failures"},
        "succeeded": {"type": "boolean"},
        "evidence": {"type": "string",
                     "description": "what you can point at that shows this — a file, a row, a URL"},
    },
    "required": ["outcome", "succeeded", "evidence"],
    "additionalProperties": False,
}


def act(work, boundaries) -> str:
    return f"""You are the ACT phase of an autonomous operations loop. Do the work.

WORK: {work.summary}
WHY:  {work.rationale}

YOU MAY:                  {boundaries.may_act_alone}
YOU MAY NOT (ask first):  {boundaries.must_ask_first}

You have web search and a shell. Use them. Actually do the thing — don't describe how one might
do it.

Then report what ACTUALLY happened, including what failed. Do NOT report success you did not
achieve. A false success is worse than a failure, because the next phase will LEARN from it and
the lie compounds through every future cycle. If you couldn't do it, say so and say why — that
is a useful cycle, not a wasted one.

`evidence` must be something a human could go and look at. If you cannot point at anything, you
probably did not do anything."""


# ---------------------------------------------------------------------------
# 3. LEARN — what do we now believe that we didn't before?
# ---------------------------------------------------------------------------

REFLECT_SCHEMA = {
    "type": "object",
    "properties": {
        "insight": {"type": "string",
                    "description": "what you now believe that you did not believe before. Specific and falsifiable."},
        "evidence": {"type": "string",
                     "description": "what in the outcome supports it"},
        "scope": {"type": "string", "enum": ["local", "generalisable"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["insight", "evidence", "scope", "confidence"],
    "additionalProperties": False,
}


def reflect(work, outcome: str, succeeded: bool, prior) -> str:
    known = "\n".join(f"- {l['insight']}" for l in prior) or "(nothing yet)"

    return f"""You are the LEARN phase of an autonomous operations loop. This phase is what makes
this system evolve rather than merely repeat. Take it seriously.

WORK:      {work.summary}
OUTCOME:   {outcome}
SUCCEEDED: {succeeded}

ALREADY KNOWN — do not restate any of this:
{known}

What do you now believe that you did NOT believe before? Be specific and falsifiable. "Outreach
is important" is not a learning; "prospects who replied within 48h converted 3x more often than
those who replied later" is.

A FAILURE teaches more than a success — mine it. Something you had to undo is the single most
valuable signal you get, and the loop must never need to be told the same thing twice.

Mark `scope` as "generalisable" ONLY if this would hold for a DIFFERENT instance with a DIFFERENT
mission — a fact about how autonomous operation works, not a fact about this particular business.
Most learnings are local, and that is fine and expected. Do not inflate the scope to seem more
useful; a false generalisation propagates to other instances and does real damage.

You are NOT being asked whether the run was good. You are being asked what CHANGED in what you
believe."""
