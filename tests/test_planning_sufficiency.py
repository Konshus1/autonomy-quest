from runner.planning_sufficiency import (
    ConflictSeverity,
    Direction,
    EdgeState,
    KnownRelation,
    PlanStep,
    RelationConflict,
    assess_plan_sufficiency,
)


def step(step_id="s1"):
    return PlanStep(step_id, "research", "evidence", Direction.TOWARD, {"domain": "pricing"})


def relation(**overrides):
    values = dict(
        relation_id="r1",
        action="research",
        effect="evidence",
        direction=Direction.TOWARD,
        mechanism="query documented source",
        scope={"domain": "pricing"},
        certainty=0.8,
        principle_id="p1",
    )
    values.update(overrides)
    return KnownRelation(**values)


def test_solid_edge_is_sufficient_when_direction_and_mechanism_are_known():
    result = assess_plan_sufficiency([step()], [relation()])
    assert result.sufficient is True
    assert result.blocked is False
    assert result.steps[0].edge_state == EdgeState.SOLID


def test_fuzzy_edge_is_sufficient_when_direction_known_mechanism_unknown():
    # MANDATORY BB #2430 discriminator: demanding a mechanism here recreates P(fire) ~= 1.
    result = assess_plan_sufficiency([step()], [relation(mechanism=None)])
    assert result.sufficient is True
    assert result.blocked is False
    assert result.frame_gap_step_ids == ()
    assert result.steps[0].edge_state == EdgeState.FUZZY
    assert result.steps[0].relation_id == "r1"


def test_absent_edge_localizes_frame_gap_to_exact_step():
    result = assess_plan_sufficiency([step("known"), PlanStep(
        "gap", "ship", "customer-value", Direction.TOWARD, {"domain": "pricing"}
    )], [relation(mechanism=None)])
    assert result.sufficient is False
    assert result.blocked is False  # insufficiency is a trigger, not the hard-contradiction gate
    assert result.frame_gap_step_ids == ("gap",)
    assert result.replan_from_step_id == "gap"
    assert result.steps[1].edge_state == EdgeState.ABSENT


def test_scope_mismatch_is_absent_not_covered():
    result = assess_plan_sufficiency([step()], [relation(scope={"domain": "releases"})])
    assert result.sufficient is False
    assert result.frame_gap_step_ids == ("s1",)


def test_opposite_direction_is_localized_hard_contradiction():
    result = assess_plan_sufficiency([step()], [relation(direction=Direction.AWAY)])
    assert result.blocked is True
    assert result.sufficient is False
    assert result.hard_conflicts[0].step_ids == ("s1",)
    assert "predicts away" in result.hard_conflicts[0].reason


def test_soft_tension_annotates_and_degrades_but_does_not_block():
    tension = RelationConflict("r1", "r2", ConflictSeverity.SOFT, "may raise cost", ("s1",))
    result = assess_plan_sufficiency([step()], [relation(mechanism=None)], [tension])
    assert result.sufficient is True
    assert result.blocked is False
    assert result.steps[0].edge_state == EdgeState.FUZZY
    assert result.steps[0].certainty == 0.6
    assert result.steps[0].annotation == "may raise cost"


def test_declared_hard_conflict_reports_pair_and_blocks():
    conflict = RelationConflict("r1", "r2", ConflictSeverity.HARD, "mutually exclusive", ("s1",))
    result = assess_plan_sufficiency([step()], [relation()], [conflict])
    assert result.blocked is True
    assert result.hard_conflicts[0].left_relation_id == "r1"
    assert result.hard_conflicts[0].right_relation_id == "r2"
    assert result.replan_from_step_id == "s1"
