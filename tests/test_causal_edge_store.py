"""Task #4407 slice 1b — causal-edge store + gated promote/demote + plan->certainty->surprise flow."""

from __future__ import annotations

from ralph_portable.causal_edge_store import InMemoryCausalEdgeStore
from ralph_portable.causal_edges import (
    edge_identity,
    propose_update,
    surprise,
)


def _edge(**over):
    e = {
        "cause": "send_followup", "effect": "email_sent",
        "formality": "evidential", "strictness": "advisory", "directness": "predicate",
        "executor": {"kind": "predicate", "ref": "checks/email_sent.sql"},
        "predicted_certainty": 0.7,
    }
    e.update(over)
    return e


# ---- store basics ----

def test_put_get_all_and_identity_key():
    s = InMemoryCausalEdgeStore()
    ident = s.put(_edge())
    assert ident == edge_identity(_edge())
    assert s.get(ident)["cause"] == "send_followup"
    assert len(s.all()) == 1


def test_upsert_by_identity_preserves_support_and_evidence():
    s = InMemoryCausalEdgeStore()
    ident = s.put(_edge())
    # accumulate some support/evidence
    s.record_evidence(ident, surprise(0.7, True))          # confirm -> support 1
    assert s.get(ident)["support_count"] == 1
    # a promotion updates the dials at the SAME identity; support/evidence carry over
    s.put(_edge(formality="formal", directness="script",
                executor={"kind": "script", "ref": "scripts/send.py"}))
    e = s.get(ident)
    assert e["formality"] == "formal"          # state changed
    assert e["support_count"] == 1             # support preserved
    assert len(s.all()) == 1                    # still ONE edge (same identity)


def test_remine_does_not_clobber_earned_support():
    # Adversarial finding 1: re-running the miner must NOT reset support earned by the surprise
    # loop. A mined edge omits support_count; the surprise loop earns it; re-mining (another put
    # that again omits support_count) must PRESERVE the earned value.
    from ralph_portable.principle_mining import mine_causal_edges

    obs = {
        "work_kind": "send_followup", "succeeded": True,
        "measure_before": 10, "measure_after": 12,
        "learning_insight": "x", "learning_confidence": 0.3,
        "run_id": 1, "work_id": 1, "learning_id": 1,
    }
    s = InMemoryCausalEdgeStore()
    mined = mine_causal_edges([obs])[0]
    assert "support_count" not in mined            # miner never sets it
    ident = s.put(mined)
    for _ in range(6):                              # earn support via the surprise loop
        s.record_evidence(ident, surprise(0.3, True))
    assert s.get(ident)["support_count"] == 6
    # re-mine the same observation and upsert again -> earned support must survive
    s.put(mine_causal_edges([obs])[0])
    assert s.get(ident)["support_count"] == 6
    assert s.get(ident)["observed_runs"] == 1       # breadth refreshed, support untouched


def test_governing_matches_plan_steps():
    s = InMemoryCausalEdgeStore()
    s.put(_edge(cause="a", effect="e1"))
    s.put(_edge(cause="b", effect="e2"))
    gov = s.governing([{"action": "a", "effect": "e1"}, {"action": "z", "effect": "e9"}])
    assert {(e["cause"], e["effect"]) for e in gov} == {("a", "e1")}


def test_assess_plan_returns_certainty_profile():
    s = InMemoryCausalEdgeStore()
    s.put(_edge(cause="a", effect="e1", formality="formal", directness="script",
                executor={"kind": "script", "ref": "s.py"}, predicted_certainty=0.95))
    prof = s.assess_plan([{"action": "a", "effect": "e1"}, {"action": "b", "effect": "e2"}])
    assert prof["covered"] == 1 and prof["uncovered"] == 1
    assert prof["per_step"][0]["guaranteed"] is True


# ---- gated promotion / demotion ----

def test_propose_update_demote_on_confident_wrong():
    prop = propose_update(_edge(formality="formal"), surprise(0.95, 0.0))
    assert prop["action"] == "demote" and prop["to"] == "evidential" and prop["gated"] is True


def test_propose_update_promote_needs_support():
    # not enough support -> hold
    assert propose_update(_edge(support_count=1), surprise(0.7, True))["action"] == "hold"
    # BB #775: support at the evidential rung is NOT enough for formal — reaching formal now
    # requires a VERIFIED formal executor (a passing oracle proof), so a confirm here HOLDS.
    prop = propose_update(_edge(support_count=5), surprise(0.7, True))
    assert prop["action"] == "hold" and "verified formal executor" in prop["reason"]
    # with a verified script/constraint executor, the same confirm promotes evidential -> formal
    armed = _edge(support_count=5, directness="script",
                  executor={"kind": "constraint", "ref": "k"}, executor_verified=True)
    prop2 = propose_update(armed, surprise(0.7, True))
    assert prop2["action"] == "promote" and prop2["to"] == "formal"


def test_propose_update_refuses_formal_promotion_on_judgment():
    e = _edge(formality="evidential", directness="judgment",
              executor={"kind": "judgment"}, support_count=9)
    prop = propose_update(e, surprise(0.7, True))
    assert prop["action"] == "hold" and "judgment" in prop["reason"]


# ---- end-to-end: plan -> certainty -> surprise -> gated update ----

def test_full_front_half_flow():
    s = InMemoryCausalEdgeStore()
    ident = s.put(_edge(cause="a", effect="e1", formality="evidential",
                        directness="predicate", predicted_certainty=0.7))
    plan = [{"action": "a", "effect": "e1"}]

    # 1) planning check produces a certainty profile
    prof = s.assess_plan(plan)
    predicted = prof["per_step"][0]["certainty"]
    assert prof["covered"] == 1

    # 2) act (stubbed) succeeds 5x -> confirming surprises accumulate support
    last = None
    for _ in range(5):
        last = s.record_evidence(ident, surprise(predicted, True))
    assert s.get(ident)["support_count"] == 5
    assert len(s.get(ident)["evidence"]) == 5

    # 3) BB #775: at the evidential rung a confirm alone can no longer propose FORMAL — that rung
    #    requires a verified formal executor (an oracle proof). So the proposal HOLDS (gated), and
    #    nothing is auto-applied. (The fuzzy->evidential rung is still earned by confirms.)
    assert last["action"] == "hold" and "verified formal executor" in last["reason"]
    assert s.get(ident)["formality"] == "evidential"   # NOT auto-applied / not advanced

    # 4) a confident-but-wrong outcome PROPOSES fast demotion
    dem = s.record_evidence(ident, surprise(0.95, False))
    assert dem["action"] == "demote" and dem["gated"] is True
