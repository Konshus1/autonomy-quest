from types import SimpleNamespace

from runner.config import Boundaries, Measure, Mission
from runner.evaluate import check_intent_coverage
from runner.intent_lineage import (
    measure_plan,
    mission_intent_contract,
    verify_intent_lineage,
)


def authoritative():
    return [
        {"concern_id": "useful_progress", "kind": "serve",
         "predicate": {"metric": "useful_records", "operator": ">=", "value": 10}},
        {"concern_id": "no_volume_gaming", "kind": "must_not_harm",
         "predicate": {"metric": "total_records", "operator": "<=", "value": 12}},
    ]


def valid_plan():
    return {
        "goal_predicate": {"metric": "useful_records", "operator": ">=", "value": 10},
        "mission_concerns": authoritative(),
        "subgoals": [{
            "subgoal_id": "grounded-coverage",
            "success_predicate": {"metric": "total_records", "operator": "<=", "value": 12},
            "serves_concern_ids": ["useful_progress", "no_volume_gaming"],
        }],
        "steps": [{"step_id": "s1", "subgoal_id": "grounded-coverage"}],
    }


def test_prework_verifier_accepts_machine_followable_lineage():
    result = verify_intent_lineage(valid_plan(), authoritative())
    assert result.valid is True
    assert result.verifier_id == "deterministic-interval-v1"


def test_volume_optimized_goal_is_rejected_before_work():
    # Mandatory negative control: the local goal rewards volume, but mission intent caps it.
    plan = valid_plan()
    plan["goal_predicate"] = {"metric": "total_records", "operator": ">=", "value": 100}
    plan["subgoals"][0]["success_predicate"] = {
        "metric": "useful_records", "operator": ">=", "value": 10
    }
    result = verify_intent_lineage(plan, authoritative())
    assert result.valid is False
    assert any("no_volume_gaming" in reason for reason in result.reasons)


def test_three_plan_measures_are_independent():
    plan = valid_plan()
    measures = measure_plan(
        plan, {"useful_records": 10, "total_records": 100},
        [{"step_id": "s1", "executed": True, "confirmed": True}],
    )
    assert measures.goal_satisfied is True
    assert (measures.steps_confirmed, measures.steps_executed) == (1, 1)
    assert measures.intent_satisfied is False  # success at the wrong thing


def test_check_intent_coverage_is_enforceable_not_unconditional_stub():
    work = SimpleNamespace(plan=valid_plan())
    assert check_intent_coverage(work, {"useful_records": 10, "total_records": 11}) is True
    assert check_intent_coverage(work, {"useful_records": 10, "total_records": 100}) is False
    assert check_intent_coverage(work, {}) is False


def test_live_prework_rejects_volume_plan_before_act_and_persists_reason():
    from runner import prompts
    from runner.executor import Usage
    from runner.loop import Loop
    from tests.test_loop_approval_execution import FakeDb, instance
    from tests.test_pre_act_predictions import PlannedExecutor

    class VolumeExecutor(PlannedExecutor):
        def run(self, prompt, schema, tier="working"):
            if schema is prompts.DECIDE_SCHEMA:
                decision, usage = super().run(prompt, schema, tier)
                decision["plan"]["goal_predicate"] = {
                    "metric": "mission_value", "operator": ">=", "value": 100
                }
                decision["plan"]["subgoals"][0]["success_predicate"] = {
                    "metric": "mission_delta", "operator": ">=", "value": 0
                }
                return decision, usage
            return super().run(prompt, schema, tier)

    db = FakeDb()
    ex = VolumeExecutor(db)
    assert Loop(instance(), db, ex).cycle() is None
    assert "intent_rejected" in db.events
    assert "act" not in db.events
    assert db.intent_checks[-1][-1].valid is False
    assert any("must_not_overshoot" in reason for reason in db.intent_checks[-1][-1].reasons)


def _reach_mission(target=3.0):
    return Mission(
        objective="reach and hold the count",
        measure=Measure(what="subscriptions", where="q", target=target,
                        goal="reach_and_maintain"),
        horizon="30d", boundaries=Boundaries(),
    )


def _reach_plan(progress_predicate):
    """A structurally valid reach-target plan whose ONLY variable is how the serve
    concern's progress is expressed (mission_delta vs. mission_value)."""
    concerns = mission_intent_contract(_reach_mission(), 3.0)
    return {
        "goal_predicate": {"metric": "mission_value", "operator": ">=", "value": 3},
        "mission_concerns": concerns,
        "subgoals": [
            {"subgoal_id": "sg-progress", "success_predicate": progress_predicate,
             "serves_concern_ids": ["serve_mission_progress"]},
            {"subgoal_id": "sg-ceiling",
             "success_predicate": {"metric": "mission_value", "operator": "<=", "value": 3},
             "serves_concern_ids": ["must_not_overshoot"]},
        ],
        "steps": [{"step_id": "s1", "subgoal_id": "sg-progress"},
                  {"step_id": "s2", "subgoal_id": "sg-ceiling"}],
    }


def test_reach_target_plan_passes_only_with_a_mission_delta_predicate():
    # Regression for the reach-target stall (766d814): a serve concern is a CHANGE
    # (mission_delta >= 0), so a plan expressing progress as the absolute value cannot
    # entail it — you could reach the value by falling. The fixed planner commits a
    # mission_delta predicate instead; the verifier must accept that and only that.
    concerns = mission_intent_contract(_reach_mission(), 3.0)

    # OLD, buggy shape: progress as `mission_value == 3` (structurally valid) is REJECTED,
    # and specifically because it does not guarantee the serve concern — not for any other reason.
    value_only = verify_intent_lineage(
        _reach_plan({"metric": "mission_value", "operator": "==", "value": 3}), concerns)
    assert value_only.valid is False
    assert value_only.reasons == (
        "satisfying plan criteria does not guarantee concern serve_mission_progress",)

    # FIXED shape: a genuine `mission_delta >= 3` (entails >= 0) PASSES cleanly.
    delta_plan = verify_intent_lineage(
        _reach_plan({"metric": "mission_delta", "operator": ">=", "value": 3}), concerns)
    assert delta_plan.valid is True
    assert delta_plan.reasons == ()


def test_reach_target_serve_concern_is_graded_on_db_delta_not_the_reached_value():
    # Anti-Goodhart: the delta claim is not a free pass. measure_plan grades the AUTHORITATIVE
    # serve concern (mission_delta >= 0) against independently re-read metrics, so reaching the
    # target value by FALLING (delta < 0) fails intent even though the goal value is hit.
    plan = _reach_plan({"metric": "mission_delta", "operator": ">=", "value": 3})
    step = [{"step_id": "s1", "executed": True, "confirmed": True}]

    reached_by_rising = measure_plan(plan, {"mission_value": 3.0, "mission_delta": 1.0}, step)
    assert reached_by_rising.goal_satisfied is True
    assert reached_by_rising.intent_satisfied is True   # real progress, mission genuinely served

    reached_by_falling = measure_plan(plan, {"mission_value": 3.0, "mission_delta": -2.0}, step)
    assert reached_by_falling.goal_satisfied is True    # value hit...
    assert reached_by_falling.intent_satisfied is False  # ...but the serve concern is NOT met


def test_evaluator_rejects_goal_and_steps_success_when_intent_harmed():
    from decimal import Decimal
    from types import SimpleNamespace
    from runner.evaluate import Evaluator
    from tests.test_evaluate import _Esc, _GOOD_INSIGHT

    plan = valid_plan()
    result = Evaluator(_Esc()).evaluate(
        work=SimpleNamespace(plan=plan), outcome="created records", succeeded=True,
        evidence="artifact", measure_before=Decimal(0), measure_after=Decimal(100),
        insight=_GOOD_INSIGHT, predicted_certainty=None,
        observed_metrics={"useful_records": 10, "total_records": 100},
        step_results=[{"step_id": "s1", "executed": True, "confirmed": True}],
    )
    assert result.productive is False         # movement that harms authoritative intent is not progress
    assert result.plan_goal_satisfied is True
    assert (result.steps_confirmed, result.steps_executed) == (1, 1)
    assert result.intent_covered is False
    assert result.verdict == "rework"
