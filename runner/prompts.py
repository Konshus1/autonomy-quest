"""The three prompts the loop runs, and the schemas that pin their replies.

Kept in one file because these ARE the loop's thinking, and they should be readable as a unit
by anyone asking "what does this system actually do every cycle?"

Every schema is `additionalProperties: false` and lists its required fields. That is not
pedantry: the executor validates against these, and a reply that doesn't match is a FAILURE
rather than something we half-parse. A loop that acts on a half-understood reply will file the
misunderstanding as a learning and carry it forever.
"""

from __future__ import annotations

# THE PROMPT MUST NOT GROW WITHOUT BOUND.
#
# decide() inlined EVERY live learning and the FULL outcome text of the last 10 runs. So the prompt
# grew with the instance's own history — forever. On a real box a small request was accepted while
# the mission prompt was refused for headroom: the loop had priced itself out of its own plan by
# remembering things.
#
# A system that learns more and can therefore think less is not a learning system. Cap it, and cap
# it by VALUE (highest-confidence learnings first), not by recency.
MAX_LEARNINGS_IN_PROMPT = 15
MAX_RUNS_IN_PROMPT = 5
MAX_OUTCOME_CHARS = 240


def _clip(text: str, n: int = MAX_OUTCOME_CHARS) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "…"

# ---------------------------------------------------------------------------
# 1. DECIDE — what is the highest-value thing to do right now?
# ---------------------------------------------------------------------------

PREDICATE_SCHEMA = {
    "type": "object",
    "properties": {
        "metric": {"type": "string", "minLength": 1},
        "operator": {"type": "string", "enum": [">", ">=", "==", "<=", "<"]},
        "value": {"type": "number"},
    },
    "required": ["metric", "operator", "value"],
    "additionalProperties": False,
}

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
        "plan": {
            "type": "object",
            "properties": {
                "goal_predicate": PREDICATE_SCHEMA,
                "expected_expense_usd": {"type": "number", "minimum": 0,
                    "description": "total incremental external expense expected for this entire plan"},
                "mission_concerns": {"type": "array", "minItems": 1, "items": {
                    "type": "object",
                    "properties": {
                        "concern_id": {"type": "string", "minLength": 1},
                        "kind": {"type": "string", "enum": ["serve", "must_not_harm"]},
                        "predicate": PREDICATE_SCHEMA,
                    },
                    "required": ["concern_id", "kind", "predicate"],
                    "additionalProperties": False,
                }},
                "subgoals": {"type": "array", "minItems": 1, "items": {
                    "type": "object",
                    "properties": {
                        "subgoal_id": {"type": "string", "minLength": 1},
                        "success_predicate": PREDICATE_SCHEMA,
                        "serves_concern_ids": {"type": "array", "minItems": 1,
                            "items": {"type": "string", "minLength": 1}},
                    },
                    "required": ["subgoal_id", "success_predicate", "serves_concern_ids"],
                    "additionalProperties": False,
                }},
                "steps": {"type": "array", "minItems": 1, "items": {
                    "type": "object",
                    "properties": {
                        "step_id": {"type": "string", "minLength": 1},
                        "subgoal_id": {"type": "string", "minLength": 1},
                        "action": {"type": "string", "minLength": 1},
                        "expected_effect": {"type": "string", "minLength": 1},
                        "expected_direction": {"type": "string", "enum": ["toward", "away", "neutral"]},
                        "scope": {
                            "type": "object",
                            "properties": {
                                "conditions": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["conditions"],
                            "additionalProperties": False,
                        },
                        "blast_radius": {
                            "type": "object",
                            "properties": {
                                "affected_entities_upper_bound": {"type": "integer", "minimum": 0},
                                "public_or_unbounded": {"type": "boolean"},
                                "production_wide": {"type": "boolean"},
                                "irreversible_external_write": {"type": "boolean"},
                            },
                            "required": ["affected_entities_upper_bound", "public_or_unbounded",
                                         "production_wide", "irreversible_external_write"],
                            "additionalProperties": False,
                        },
                    },
                    "required": ["step_id", "subgoal_id", "action", "expected_effect", "expected_direction", "scope", "blast_radius"],
                    "additionalProperties": False,
                }},
            },
            "required": ["goal_predicate", "expected_expense_usd", "mission_concerns", "subgoals", "steps"],
            "additionalProperties": False,
        },
    },
    "required": ["do_nothing", "kind", "summary", "rationale",
                 "reversible", "spends_money", "touches_human", "commits", "plan"],
    "additionalProperties": False,
}


def decide(world: dict, template: str, guidance: str = "") -> str:
    m = world["mission"]

    # Highest-confidence learnings first, then cap. The loop must not become unable to think
    # BECAUSE it has learned a lot — which is precisely what an uncapped prompt does.
    ls = sorted(world["learnings"], key=lambda l: -(l.get("confidence") or 0))[:MAX_LEARNINGS_IN_PROMPT]
    learned = "\n".join(f"- {_clip(l['insight'])}" for l in ls) or "(nothing yet — first cycles)"
    if len(world["learnings"]) > MAX_LEARNINGS_IN_PROMPT:
        learned += f"\n  (+{len(world['learnings']) - MAX_LEARNINGS_IN_PROMPT} more, lower confidence)"

    rs = world["recent_runs"][:MAX_RUNS_IN_PROMPT]
    recent = "\n".join(f"- {_clip(r['summary'], 90)}: {_clip(r['outcome'])}" for r in rs) or "(no runs yet)"
    parked = len(world.get("parked") or [])
    pending_replans = world.get("pending_replans") or []
    replan_text = "\n".join(
        f"- failed_step={r['failed_step_id']} preserved_prefix={r['preserved_prefix_step_ids']} reason={r['reason']}"
        for r in pending_replans
    ) or "(none)"

    nudge = f"\n!!! {guidance}\n" if guidance else ""

    # THE TARGET, AND WHETHER WE HAVE MET IT. Without this the loop reads "move the number" as
    # "grow it forever" and manufactures volume past a met goal (a real box hit 50,082 against a
    # target of 60). The measure knows its own ceiling now; put it in front of the model.
    tgt = world.get("target")
    if world.get("satisfied"):
        goal_line = (f"TARGET:   {tgt} — ALREADY MET (currently {world['now']}). THE MISSION IS "
                     f"SATISFIED.\n"
                     f"          DO NOT grow this number further. Growing a met target is not the "
                     f"job — it is the OPPOSITE of the job, and it burns money to make a graph go up.\n"
                     f"          Your work now is MAINTENANCE: find the STALEST / oldest record and "
                     f"re-verify it so the target stays met as records age out. If everything is "
                     f"already fresh and nothing needs maintaining, set do_nothing HONESTLY.")
    elif tgt is not None:
        goal_line = (f"TARGET:   {tgt} (currently {world['now']}) — REACH-AND-MAINTAIN. Move toward "
                     f"{tgt}, then HOLD it. Do not overshoot: {tgt} is the goal, not the floor.")
    else:
        goal_line = "TARGET:   unbounded (maximize) — more is genuinely the point for this mission."

    return f"""You are the DECIDE phase of an autonomous operations loop. You run continuously,
aimed at one mission. Pick the single highest-value thing to do RIGHT NOW.
{nudge}

Do the thing that best SERVES THE MISSION — which is NOT always "make the number bigger." If the
target is met, serving the mission means KEEPING it met, not inflating it.

MISSION:  {m.objective}
MEASURE:  {m.measure.what} — currently {world['now']}
{goal_line}
HORIZON:  {m.horizon}
TEMPLATE: {template}
AUTHORITATIVE MISSION CONCERNS (copy these IDs/kinds/predicates exactly into plan.mission_concerns):
{world["intent_contract"]}

YOU MAY DO ALONE:  {m.boundaries.may_act_alone}
YOU MUST ASK FIRST: {m.boundaries.must_ask_first}

WHAT YOU HAVE LEARNED SO FAR:
{learned}

RECENT RUNS:
{recent}

PENDING LOCALIZED RE-PLANS:
{replan_text}
When a re-plan is pending, preserve the confirmed prefix and begin the replacement at the failed
step. Do not restart or discard confirmed prefix steps.

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

Return an explicit ordered plan. The numeric goal predicate and every sub-goal success predicate
must jointly guarantee every authoritative mission concern, including `must_not_harm` concerns.
Copy the authoritative concerns exactly, link every sub-goal to concern IDs, and every step to a
sub-goal. A separate deterministic verifier checks the implication before work; you do not grade it.
For every step assert the direct effect and whether it moves toward, away from, or neutrally with
respect to the plan goal. `scope` must contain the conditions under which the assertion holds.
These assertions are recorded before ACT; do not invent a mechanism when only direction is known.
For the whole plan provide a numeric expected external expense. For every action provide measured
blast facts: a conservative affected-entity upper bound and whether it is public/unbounded,
production-wide, or an irreversible external write. Human contact, commitment, and spending are
audit categories, not authorization by themselves; consequence magnitude is what the gate uses.

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
        "observed_metrics": {"type": "array", "items": {
            "type": "object",
            "properties": {"metric": {"type": "string"}, "value": {"type": "number"}},
            "required": ["metric", "value"],
            "additionalProperties": False,
        }, "description": "observed values for every metric named by plan predicates"},
        "causal_proposals": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "source_action": {"type": "string"},
                "direct_effect": {"type": "string"},
                "mission_measure": {"type": "string"},
                "direction": {"type": "string", "enum": ["toward", "away", "neutral"]},
                "mechanism": {"type": "string"},
                "scope": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"key": {"type": "string"}, "value": {"type": "string"}},
                    "required": ["key", "value"],
                    "additionalProperties": False,
                }},
                "certainty": {"type": "number", "minimum": 0, "maximum": 1},
                "evidence": {"type": "string"},
            },
            "required": ["source_action", "direct_effect", "mission_measure", "direction",
                         "mechanism", "scope", "certainty", "evidence"],
            "additionalProperties": False,
        }, "description": "candidate relations from acquisition only; proposals carry no authority"},
        "step_results": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "step_id": {"type": "string"}, "executed": {"type": "boolean"},
                "confirmed": {"type": "boolean"}, "evidence": {"type": "string"},
                "harmed_concern_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["step_id", "executed", "confirmed", "evidence", "harmed_concern_ids"],
            "additionalProperties": False,
        }},
    },
    "required": ["outcome", "succeeded", "evidence", "observed_metrics", "step_results",
                 "causal_proposals"],
    "additionalProperties": False,
}


def act(work, boundaries) -> str:
    return f"""You are the ACT phase of an autonomous operations loop. Do the work.

WORK: {work.summary}
WHY:  {work.rationale}
PLAN: {work.plan}

YOU MAY:                  {boundaries.may_act_alone}
YOU MAY NOT (ask first):  {boundaries.must_ask_first}

You have web search and a shell. Use them. Actually do the thing — don't describe how one might
do it.

Then report what ACTUALLY happened, including what failed. Do NOT report success you did not
achieve. A false success is worse than a failure, because the next phase will LEARN from it and
the lie compounds through every future cycle. If you couldn't do it, say so and say why — that
is a useful cycle, not a wasted one.

`evidence` must be something a human could go and look at. If you cannot point at anything, you
probably did not do anything. Report observed numeric values for every metric in the plan's goal
and intent predicates. Report each plan step separately: executed says the action ran; confirmed
says its predicted direct effect was independently observed. List any mission concern the step
harmed in `harmed_concern_ids` even when its direct effect succeeded. These are not the mission measure.

If and only if this is knowledge-acquisition work and the evidence supports the exact missing plan
step, return it in `causal_proposals`. Do not write `causal_edge` or governance tables with shell or
DB tools. A returned candidate is committed with the run as PROVISIONAL and carries no authority;
only the separate governed lifecycle can promote it. Otherwise return an empty array."""


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
    ps = sorted(prior, key=lambda l: -(l.get("confidence") or 0))[:MAX_LEARNINGS_IN_PROMPT]
    known = "\n".join(f"- {_clip(l['insight'])}" for l in ps) or "(nothing yet)"

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


# ---------------------------------------------------------------------------
# 4. EXPLORE — optional curiosity, staged and inert
# ---------------------------------------------------------------------------

EXPLORE_SCHEMA = {
    "type": "object",
    "properties": {
        "outcome": {"type": "string",
                    "description": "what was inspected and what happened"},
        "succeeded": {"type": "boolean"},
        "evidence": {"type": "string",
                     "description": "pointable source, query, file, or URL used as evidence"},
        "insight": {"type": "string",
                    "description": "specific falsifiable proposal to stage inertly"},
        "applies_when": {"type": "string",
                         "description": "predicate for when this proposal might apply"},
        "falsified_by": {"type": "string",
                         "description": "observation or test that would disprove it"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["outcome", "succeeded", "evidence", "insight", "applies_when",
                 "falsified_by", "confidence"],
    "additionalProperties": False,
}


def explore(mission, frontier_items, authority: str) -> str:
    items = "\n".join(f"- {i}" for i in frontier_items) or "(no frontier items returned)"
    return f"""You are the optional EXPLORE phase of an autonomous operations loop.

This is bounded curiosity, not mission work. Inspect ONLY the frontier items below, which were read
from the configured external authority. Do not expand the frontier. Do not add work. Do not change
behavior. Produce at most one falsifiable proposal, staged inertly for later human/local review.

MISSION: {mission.objective}
MEASURE: {mission.measure.what}
AUTHORITY: {authority}

FRONTIER ITEMS:
{items}

Your output is NOT a live learning. It will be staged and inert. It must pass share -> review ->
promote later, and promotion requires a behavior-change test. If you did not find a useful,
falsifiable proposal, set succeeded=false and make the insight explain what was not learned.

`applies_when` must say when the proposal is relevant. `falsified_by` must be an actual observation
or negative control that could disprove it. No falsifier, no claim."""
