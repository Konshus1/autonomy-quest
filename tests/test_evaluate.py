"""Task #4407 stage 7 — Evaluator: verdict logic + the invariant that productive == was_productive."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from runner.escalation import Escalation
from runner.evaluate import (
    Evaluator,
    classify_test_level,
    gate3_learning_completeness,
    gate4_model_coherence,
)


class _Esc:
    @staticmethod
    def was_productive(*a, **k):
        return Escalation.was_productive(*a, **k)


def _ev():
    return Evaluator(_Esc())


_GOOD_INSIGHT = {"insight": "x", "evidence": "a real artifact", "scope": "local", "confidence": 0.6}


_PLAN = {
    "goal_predicate": {"metric": "goal", "operator": ">=", "value": 1},
    "mission_concerns": [{"concern_id": "intent", "kind": "serve",
                           "predicate": {"metric": "intent", "operator": ">=", "value": 1}}],
    "subgoals": [{"subgoal_id": "sg", "success_predicate": {"metric": "intent", "operator": ">=", "value": 1},
                  "serves_concern_ids": ["intent"]}],
    "steps": [{"step_id": "s", "subgoal_id": "sg"}],
}


def _run(ev, *, before, after, succeeded=True, evidence="artifact", insight=_GOOD_INSIGHT, pc=None,
         metrics=None, step_results=None):
    return ev.evaluate(work=SimpleNamespace(plan=_PLAN), outcome="ok", succeeded=succeeded, evidence=evidence,
                       measure_before=Decimal(before), measure_after=Decimal(after),
                       insight=insight, predicted_certainty=pc,
                       observed_metrics=({"goal": 1, "intent": 1} if metrics is None else metrics),
                       step_results=([{"step_id": "s", "executed": True, "confirmed": True}]
                                     if step_results is None else step_results))


def test_productive_matches_was_productive_across_truth_table():
    ev = _ev()
    cases = [
        dict(before="10", after="12", succeeded=True, evidence=""),     # number moved -> productive
        dict(before="10", after="10", succeeded=True, evidence="art"),  # evidence+success -> productive
        dict(before="10", after="10", succeeded=True, evidence=""),     # narrated -> NOT productive
        dict(before="10", after="10", succeeded=False, evidence=""),    # failed -> NOT productive
    ]
    for c in cases:
        r = _run(ev, **c)
        assert r.productive == Escalation.was_productive(
            "ok", c["succeeded"], c["evidence"], Decimal(c["before"]), Decimal(c["after"]))


def test_verdict_pass_on_clean_cycle():
    r = _run(_ev(), before="10", after="12", pc=0.3)   # moved up, coherent, complete
    assert r.verdict == "pass" and r.productive is True


def test_verdict_rework_when_not_productive():
    r = _run(_ev(), before="10", after="10", succeeded=True, evidence="")   # narrated
    assert r.verdict == "rework" and r.productive is False


def test_verdict_rework_when_learning_incomplete():
    bad = {"insight": "x", "evidence": "  ", "scope": "local"}              # empty evidence -> Gate 3 fail
    r = _run(_ev(), before="10", after="12", insight=bad)
    assert r.verdict == "rework"


def test_verdict_escalate_on_incoherent_high_certainty_miss():
    # Actor success/evidence cannot override an unchanged independently read measure.
    r = _run(_ev(), before="10", after="10", succeeded=True, evidence="artifact", pc=0.9)
    assert r.verdict == "rework" and r.productive is False


def test_gate3_and_gate4_units():
    assert gate3_learning_completeness(_GOOD_INSIGHT) is True
    assert gate3_learning_completeness({"evidence": "", "scope": "local"}) is False
    assert gate3_learning_completeness({"evidence": "x", "scope": "bogus"}) is False
    assert gate4_model_coherence(None, False) is True          # no principle -> coherent
    assert gate4_model_coherence(0.9, False) is False          # confident + wrong -> incoherent
    assert gate4_model_coherence(0.9, True) is True
    assert gate4_model_coherence(0.3, False) is True           # low certainty miss is fine


def test_classify_test_level():
    assert classify_test_level(None, "ran pytest, 12 assert passed") == "unit"
    assert classify_test_level(None, "curl the /api/ endpoint") == "api"
    assert classify_test_level(None, "playwright screenshot") == "ui"
    assert classify_test_level(None, "did a thing") == "none"


def test_actor_success_and_url_with_zero_steps_cannot_claim_productivity():
    r = _run(_ev(), before="10", after="10", succeeded=True,
             evidence="https://example.invalid/receipt", metrics={}, step_results=[])
    assert r.productive is False
    assert r.verdict == "rework"
    assert r.plan_goal_satisfied is False
    assert (r.steps_executed, r.steps_confirmed) == (0, 0)
