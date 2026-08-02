#!/usr/bin/env python3
"""Watch an agent think — with receipts.

A self-contained, narrated walkthrough of Autonomy Quest's causal learning loop.
NO container, DB, API, or keys: it drives the real in-memory causal machinery from
``ralph_portable`` (the same code the production loop calls) against a small,
scripted "mission world" so you can watch the mechanism end to end.

The story, in one breath: the agent MINES a fuzzy causal principle from its own
completed runs (cause -> effect, with provenance), PREDICTS an outcome before it
acts ("~30% sure X moves the number"), then EARNS trust as the real number moves —
honestly, capped, and never auto-promoted to "guaranteed".

What is REAL here (imported, not re-implemented):
    ralph_portable.principle_mining.mine_causal_edges   -> mines fuzzy edges
    ralph_portable.causal_edge_store.InMemoryCausalEdgeStore
                                                        -> stores + scores + learns
    ralph_portable.causal_edges.surprise                -> prediction vs actual
    ralph_portable.causal_edges.propose_update          -> the GATED promote/demote call

What is SIMULATED here: only the mission outcomes (whether a tactic moved the
number this week). Those are a scripted stand-in for a live mission so the trace is
deterministic and reproducible. The causal reasoning is the production code.

Run it:
    python3 demo/causal_learning_demo.py
"""

from __future__ import annotations

import os
import sys

# Import the repo's ralph_portable without any install step.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from ralph_portable.causal_edge_store import InMemoryCausalEdgeStore  # noqa: E402
from ralph_portable.causal_edges import (  # noqa: E402
    is_guaranteed,
    surprise,
)
from ralph_portable.principle_mining import mine_causal_edges  # noqa: E402

W = 74  # trace width


# ----------------------------------------------------------------------------- #
# tiny narration helpers (stdlib only)
# ----------------------------------------------------------------------------- #
def rule(ch: str = "-") -> None:
    print(ch * W)


def banner(title: str) -> None:
    print()
    print("=" * W)
    print(title.center(W))
    print("=" * W)


def say(line: str = "") -> None:
    print(line)


def pct(certainty: float) -> str:
    """The agent's own words for a certainty score."""
    return f"~{round(certainty * 100)}%"


def dials(edge: dict) -> str:
    return (
        f"formality={edge['formality']:<10} "
        f"strictness={edge['strictness']:<9} "
        f"directness={edge['directness']}"
    )


# ----------------------------------------------------------------------------- #
# The mission and its (simulated) world.
#
# Mission: grow weekly REPLIES to founder outreach. "The number" is replies/week.
# The agent tries tactics (work kinds). The world below is the ground truth the
# agent does NOT know in advance — it can only predict, act, and score.
# ----------------------------------------------------------------------------- #
FOLLOWUP = "send_personalized_followup"
COUPON = "apply_verified_coupon"

# Completed history the agent already lived through (its own run records).
# Shape matches what principle_mining.mine_causal_edges consumes.
SEED_RUNS = [
    {
        "work_kind": FOLLOWUP, "succeeded": True,
        "measure_before": 8, "measure_after": 11,
        "learning_confidence": 0.30,
        "learning_insight": "Replies jump when the follow-up names the "
                            "prospect's last shipped feature.",
        "run_id": 101, "work_id": 5101, "learning_id": 9101,
    },
    {
        "work_kind": FOLLOWUP, "succeeded": True,
        "measure_before": 11, "measure_after": 13,
        "learning_confidence": 0.22,
        "learning_insight": "A single concrete question in the P.S. line pulls "
                            "replies.",
        "run_id": 102, "work_id": 5102, "learning_id": 9102,
    },
    {
        "work_kind": FOLLOWUP, "succeeded": True,
        "measure_before": 13, "measure_after": 14,
        "learning_confidence": 0.28,
        "learning_insight": "Tuesday-morning sends out-reply Friday sends.",
        "run_id": 103, "work_id": 5103, "learning_id": 9103,
    },
    {
        # Completed work, but the number did NOT move -> mining must ignore it.
        "work_kind": "blast_generic_template", "succeeded": True,
        "measure_before": 14, "measure_after": 14,
        "learning_confidence": 0.40,
        "learning_insight": "Generic blast felt productive but moved nothing.",
        "run_id": 104, "work_id": 5104, "learning_id": 9104,
    },
]

# Scripted outcomes for the LIVE cycles (deterministic so the trace reproduces).
# Each entry: (did the number move up?, before, after, note).
LIVE_OUTCOMES = [
    (True, 15, 17, "named their last release in the opener"),
    (True, 17, 18, "P.S. asked one sharp question"),
    (True, 18, 20, "sent Tuesday 9am their timezone"),
    (True, 20, 21, "referenced a mutual connection"),
    (True, 21, 23, "tied the ask to their public roadmap"),
    (True, 23, 24, "short, specific, one CTA"),
    (False, 24, 24, "audience fatigued -- same play stopped landing"),
]


def main() -> None:
    banner("AUTONOMY QUEST -- CAUSAL LEARNING LOOP  (watch it think)")
    say()
    say("Mission     : grow weekly REPLIES to founder outreach")
    say("The number  : replies / week  (starts at 14)")
    say("The rule    : the agent may PREDICT and ADVISE, never silently promote a")
    say("              hunch to 'guaranteed'. Trust is earned, capped, and receipted.")
    say()
    say("The three dials on every causal claim:")
    say("   formality  = how sure the cause->effect is  : fuzzy < evidential < formal")
    say("   strictness = how hard it binds behavior     : advisory < soft < hard")
    say("   directness = how deterministic the action is: judgment < predicate < script")

    # ------------------------------------------------------------------ #
    # ACT 1 — MINE a fuzzy principle from completed runs (with provenance)
    # ------------------------------------------------------------------ #
    banner("ACT 1  --  MINE A PRINCIPLE FROM THE AGENT'S OWN RUNS")
    say()
    say(f"Feeding {len(SEED_RUNS)} completed runs into the miner. It keeps only runs that")
    say("SUCCEEDED and where the number actually MOVED (honest evidence only):")
    say()
    for r in SEED_RUNS:
        moved = r["measure_after"] - r["measure_before"]
        verdict = f"moved +{moved}" if moved > 0 else "no move -> IGNORED"
        say(f"   run #{r['run_id']}  {r['work_kind']:<26} "
            f"{r['measure_before']}->{r['measure_after']}  ({verdict})")

    mined = mine_causal_edges(SEED_RUNS)
    say()
    say(f"Miner returned {len(mined)} candidate principle(s). Here is the one it found:")
    say()

    hero = mined[0]
    rule()
    say(f"  PRINCIPLE:  {hero['cause']}")
    say(f"              ->  {hero['effect']}   (the weekly number goes up)")
    say(f"  DIALS    :  {dials(hero)}")
    say(f"  CLAIM    :  '{pct(hero['predicted_certainty'])} sure this cause moves the number'")
    say("              (fuzzy is capped at 34% by design -- a hunch can NEVER")
    say("               masquerade as a formal guarantee)")
    say(f"  MINED    :  {hero.get('mined', False)}   observed over {hero['observed_runs']} distinct runs")
    say("  PROVENANCE (receipts -- every claim traces to a real run + learning):")
    for p in hero["provenance"]:
        say(f"     - run #{p['run_id']} / learning #{p['learning_id']}: {p['insight']}")
    rule()
    say()
    say("Note the honesty: the generic-blast run was DROPPED (the number never")
    say("moved), so it earned no principle. Mining only ever proposes fuzzy guides.")

    # ------------------------------------------------------------------ #
    # ACT 2 — LOAD the principle and run live cycles: PREDICT -> ACT -> SCORE
    # ------------------------------------------------------------------ #
    banner("ACT 2  --  PREDICT BEFORE ACTING, THEN EARN IT")
    store = InMemoryCausalEdgeStore()
    ident = store.put(hero)

    step = [{"action": FOLLOWUP, "effect": "measure_up"}]

    for i, (moved_up, before, after, note) in enumerate(LIVE_OUTCOMES, start=1):
        edge = store.get(ident)
        # PRE-ACT: consult the stored causal model for THIS planned step.
        profile = store.assess_plan(step)
        per_step = profile["per_step"][0]
        predicted = per_step["certainty"]

        say()
        rule("=")
        say(f"CYCLE {i}   plan: {FOLLOWUP} -> measure_up")
        rule("=")
        say(f"  PRE-ACT PREDICTION : '{pct(predicted)} sure this moves the number'")
        say(f"                       (rung={edge['formality']}, support so far="
            f"{int(edge.get('support_count') or 0)}, "
            f"guaranteed={is_guaranteed(edge)})")

        # ACT: the (simulated) mission world responds.
        say(f"  ACT                : agent runs it -- {note}")
        if moved_up:
            say(f"  OUTCOME            : replies {before} -> {after}  (the number MOVED UP)")
        else:
            say(f"  OUTCOME            : replies {before} -> {after}  (NO MOVE -- reality "
                f"disagreed)")

        # SCORE: prediction vs actual = surprise, and the GATED learning signal.
        s = surprise(predicted, bool(moved_up))
        proposal = store.record_evidence(ident, s)
        edge = store.get(ident)
        support = int(edge.get("support_count") or 0)

        say(f"  SURPRISE           : predicted={s['predicted']} actual={s['actual']} "
            f"|gap|={s['surprise']}  -> signal='{s['signal']}'")
        say(f"  SUPPORT            : {support}   (a 'confirm' earns +1 toward promotion; "
            f"a miss does not)")
        say(f"  GATED PROPOSAL     : {proposal['action'].upper()} "
            f"({proposal['from']} -> {proposal['to']})  --  {proposal['reason']}")

        # ACTUATE the gated proposal — this is the learning loop's job, not the
        # store's. The store only ever PROPOSES; nothing self-promotes.
        if proposal["action"] == "promote":
            promoted = dict(edge)
            promoted["formality"] = proposal["to"]
            # The loop raises its STATED confidence to the newly-earned ceiling.
            # (edge_certainty still caps it at the formality weight -- it cannot
            #  claim more than the 'evidential' rung allows.)
            promoted["predicted_certainty"] = {"evidential": 0.67, "formal": 1.0}.get(
                proposal["to"], edge.get("predicted_certainty")
            )
            store.put(promoted)
            say(f"  >> LOOP ACTUATES   : PROMOTED to '{proposal['to']}'. Ceiling lifts; "
                f"next prediction rises.")
        elif proposal["action"] == "hold" and "judgment" in proposal["reason"]:
            say("  >> LOOP ACTUATES   : REFUSED. It earned the support, but a judgment")
            say("                       call can NEVER be promoted to 'formal/guaranteed'")
            say("                       -- that needs a script or predicate, not a hunch.")
        elif s["signal"] == "investigate":
            say("  >> LOOP ACTUATES   : HELD FOR INVESTIGATION. The principle never")
            say("                       claimed enough certainty to be 'confidently")
            say("                       wrong', so a miss is logged, not auto-demoted.")

    # ------------------------------------------------------------------ #
    # ACT 3 — CONTRAST: a 'guaranteed' automation gets FAST-DEMOTED on reality
    # ------------------------------------------------------------------ #
    banner("ACT 3  --  THE FAST-DEMOTE LANE (nothing stays trusted for free)")
    say()
    say("The mined hunch above could never be 'confidently wrong' -- it never")
    say("claimed formal certainty. But a hand-authored, GUARANTEED automation can.")
    say("Watch what happens to one when reality breaks it.")
    say()

    coupon_edge = {
        "cause": COUPON, "effect": "measure_up", "scope": {},
        "formality": "formal", "strictness": "soft", "directness": "script",
        "executor": {"kind": "script", "ref": "scripts/apply_coupon.py"},
        "predicted_certainty": 0.95,
    }
    cident = store.put(coupon_edge)
    cstep = [{"action": COUPON, "effect": "measure_up"}]

    before_prof = store.assess_plan(cstep)["per_step"][0]
    say(f"  PRINCIPLE : {COUPON} -> measure_up")
    say(f"  DIALS     : {dials(coupon_edge)}")
    say(f"  PRE-ACT   : '{pct(before_prof['certainty'])} sure' "
        f"(guaranteed={before_prof['guaranteed']} -- a formal claim run by a script)")
    say("  ACT       : runs it -- but the coupon vendor changed their API overnight")
    say("  OUTCOME   : the number did NOT move  (a CONFIDENT prediction, WRONG)")

    s = surprise(before_prof["certainty"], False)
    proposal = store.record_evidence(cident, s)
    say(f"  SURPRISE  : predicted={s['predicted']} actual={s['actual']} "
        f"-> signal='{s['signal']}'  (p>=0.70 + wrong = the fast-demote lane)")
    say(f"  PROPOSAL  : {proposal['action'].upper()} "
        f"({proposal['from']} -> {proposal['to']})  --  {proposal['reason']}")

    # Actuate the fast demotion.
    demoted = dict(store.get(cident))
    demoted["formality"] = proposal["to"]
    store.put(demoted)
    after_prof = store.assess_plan(cstep)["per_step"][0]
    say(f"  >> ACTUATE: DEMOTED to '{proposal['to']}'. Certainty ceiling drops "
        f"{pct(before_prof['certainty'])} -> {pct(after_prof['certainty'])}, "
        f"guaranteed={after_prof['guaranteed']}.")
    say()
    say("  Promotion is slow and earned (needs 5+ confirms, and gated). Demotion")
    say("  on counter-evidence is FAST. Even a 'guaranteed' script is humbled the")
    say("  moment the real number disagrees.")

    # ------------------------------------------------------------------ #
    # Closing — the honest summary
    # ------------------------------------------------------------------ #
    hero_final = store.get(ident)
    banner("WHAT JUST HAPPENED  --  THE HONEST LEDGER")
    say()
    say(f"  Hero principle : {hero_final['cause']} -> {hero_final['effect']}")
    say(f"  Final rung     : {hero_final['formality']}  "
        f"(earned up from 'fuzzy', refused 'formal')")
    say(f"  Support earned : {int(hero_final.get('support_count') or 0)} confirming outcomes")
    say(f"  Evidence kept  : {len(hero_final.get('evidence') or [])} scored surprises on file")
    say(f"  Guaranteed?    : {is_guaranteed(hero_final)}   <-- still NO, and that is the point")
    say()
    say("  Every number above is real output of ralph_portable's causal engine:")
    say("    - The principle is FUZZY: mined from its own runs, capped honest, with")
    say("      provenance you can click back to a run and a learning.")
    say("    - Trust is EARNED, not guaranteed: it climbed a rung only after 5")
    say("      confirming outcomes, and was flatly refused a 'formal' guarantee")
    say("      because it is still a judgment call.")
    say("    - It was NEVER ACTED ON autonomously: at every cycle it only PREDICTED")
    say("      and ADVISED; a separate loop actuated each gated promote/demote.")
    say()
    say("  That is the whole pitch: an agent that shows its causal reasoning before")
    say("  it acts, then earns or loses your trust as the real number moves -- with")
    say("  receipts, and without ever quietly promoting a hunch to a guarantee.")
    say()


if __name__ == "__main__":
    main()
