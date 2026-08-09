from runner.plan_resolution import resolve_plan_steps


def steps(): return [{"step_id": "s1"}, {"step_id": "s2"}, {"step_id": "s3"}]


def test_direct_refutation_localizes_and_preserves_confirmed_prefix():
    r = resolve_plan_steps(steps(), [
        {"step_id": "s1", "executed": True, "confirmed": True, "harmed_concern_ids": [], "evidence": "e1"},
        {"step_id": "s2", "executed": True, "confirmed": False, "evidence": "e2"},
        {"step_id": "s3", "executed": False, "confirmed": False, "evidence": ""},
    ], goal_satisfied=False, intent_satisfied=True)
    assert r.failed_step_id == "s2"
    assert r.preserved_prefix_step_ids == ("s1",)
    assert [x.outcome_kind for x in r.steps] == ["supported", "direct_effect_refuted", "not_executed"]


def test_correct_but_irrelevant_plan_refutes_composed_direction():
    r = resolve_plan_steps(steps()[:2], [
        {"step_id": "s1", "executed": True, "confirmed": True, "harmed_concern_ids": [], "evidence": "e1"},
        {"step_id": "s2", "executed": True, "confirmed": True, "harmed_concern_ids": [], "evidence": "e2"},
    ], goal_satisfied=False, intent_satisfied=True)
    assert r.failed_step_id == "s2"
    assert r.preserved_prefix_step_ids == ("s1",)
    assert r.steps[1].outcome_kind == "goal_not_satisfied"
    assert r.steps[1].direct_effect_confirmed is True
    assert r.steps[1].direction_confirmed is False


def test_cross_concern_harm_localizes_even_when_direct_effect_confirmed():
    r = resolve_plan_steps(steps(), [
        {"step_id": "s1", "executed": True, "confirmed": True, "harmed_concern_ids": [], "evidence": "e1"},
        {"step_id": "s2", "executed": True, "confirmed": True,
         "harmed_concern_ids": ["no_spam"], "evidence": "complaint"},
        {"step_id": "s3", "executed": True, "confirmed": True, "harmed_concern_ids": [], "evidence": "e3"},
    ], goal_satisfied=True, intent_satisfied=False)
    assert r.failed_step_id == "s2"
    assert r.preserved_prefix_step_ids == ("s1",)
    assert r.steps[1].outcome_kind == "intent_harm"
