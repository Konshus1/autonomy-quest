import pytest

from runner.db import bind_acquisition_instruction, is_goal_relaxation_decision
from runner.meta_mode import MetaMode, choose_meta_mode as _choose_meta_mode


def complete(options):
    present = {option["mode"] for option in options}
    for mode in MetaMode:
        if mode.value not in present:
            options.append({"mode": mode.value, "state": "blocked",
                            "direct_value": None, "information_value": None, "cost": None,
                            "evidence_refs": [f"unavailable:{mode.value}"],
                            "rationale": "channel unavailable in this fixture", "instruction": "",
                            "block_reason": "fixture marks channel unavailable",
                            "wake_condition": None})
    return options


def choose(options):
    return _choose_meta_mode(complete(options))


def bounded(mode, direct, info, cost, *, instruction="do it", state="bounded"):
    return {"mode": mode, "state": state,
            "direct_value": {"low": direct[0], "high": direct[1]},
            "information_value": {"low": info[0], "high": info[1]},
            "cost": {"low": cost[0], "high": cost[1]},
            "evidence_refs": [f"forecast:{mode}"], "rationale": f"forecast {mode}",
            "instruction": instruction, "expected_expense_usd": 0,
            "blast_radius_level": 0, "reversible": True, "spends_money": False,
            "touches_human": mode in {"human_question", "human_demonstration"}, "commits": False,
            "wake_condition": "new mission evidence" if mode == "abstain" else None}


def abstain():
    return bounded("abstain", (0, 0), (0, 0), (0, 0), instruction="")


def test_cheapest_option_is_not_best_scoring_option():
    # Compute costs 1 and nets 1; experiment costs 3 but nets 5.
    d = choose([
        bounded("internal_computation", (2, 2), (0, 0), (1, 1), instruction="think"),
        bounded("environment_experiment", (4, 4), (4, 4), (3, 3), instruction="probe"),
        abstain(),
    ])
    assert d.chosen_mode == MetaMode.ENVIRONMENT_EXPERIMENT
    assert str(d.chosen_score) == "5"
    assert d.decision == "acquire"


def test_no_option_worth_cost_explicitly_chooses_abstention():
    d = choose([
        bounded("internal_computation", (0, 1), (0, 0), (2, 2)),
        bounded("environment_experiment", (0, 2), (0, 1), (4, 4)),
        bounded("human_question", (0, 1), (0, 1), (3, 3)),
        abstain(),
    ])
    assert d.chosen_mode == MetaMode.ABSTAIN
    assert d.stop_reason == "no_option_worth_cost"
    assert d.stopped is True


def test_unknown_is_not_silently_scored_as_known_zero():
    unknown = {"mode": "autonomous_practice", "state": "unknown",
               "direct_value": None, "information_value": None, "cost": None,
               "evidence_refs": [], "rationale": "transfer has not been measured",
               "instruction": "", "wake_condition": "a sandbox transfer probe completes"}
    d = choose([unknown,
        bounded("environment_experiment", (0, 0), (0, 0), (1, 1)), abstain()])
    assert d.chosen_mode is None
    assert d.stop_reason == "unresolved_unknown"
    card = next(c for c in d.scorecards if c["mode"] == "autonomous_practice")
    assert card["net_value"] is None and card["state"] == "unknown"


def test_known_worthless_requires_certificate_bounds():
    with pytest.raises(ValueError, match="non-positive upper"):
        choose([bounded("internal_computation", (2, 2), (0, 0), (1, 1),
                                       state="known_worthless"), abstain()])


def test_conservative_interval_score_does_not_choose_optimistic_upper_bound():
    d = choose([
        bounded("internal_computation", (2, 2), (0, 0), (1, 1)),  # [1,1]
        bounded("environment_experiment", (0, 4), (0, 4), (0, 4)),  # [-4,8]
        abstain(),
    ])
    assert d.chosen_mode == MetaMode.INTERNAL_COMPUTATION


def test_hard_block_is_not_scalarized_into_a_price():
    blocked = {"mode": "environment_experiment", "state": "blocked",
               "direct_value": None, "information_value": None, "cost": None,
               "evidence_refs": ["safety:irreversible"], "rationale": "would write production",
               "instruction": "", "block_reason": "irreversible action exceeds authority"}
    d = choose([blocked,
        bounded("internal_computation", (2, 2), (0, 0), (1, 1)), abstain()])
    assert d.chosen_mode == MetaMode.INTERNAL_COMPUTATION
    assert next(c for c in d.scorecards if c["mode"] == "environment_experiment")["net_value"] is None


def test_ties_are_deterministic_under_input_permutation():
    a = bounded("internal_computation", (2, 2), (0, 0), (1, 1))
    b = bounded("human_question", (2, 2), (0, 0), (1, 1))
    assert choose([a, b, abstain()]).chosen_mode == MetaMode.HUMAN_QUESTION
    assert choose([abstain(), b, a]).chosen_mode == MetaMode.HUMAN_QUESTION


def test_abstain_must_be_explicitly_scored():
    with pytest.raises(ValueError, match="abstain"):
        _choose_meta_mode([bounded("internal_computation", (1, 1), (0, 0), (1, 1))])


def test_abstain_is_fixed_zero_incremental_baseline():
    bad = bounded("abstain", (1, 1), (0, 0), (0, 0), instruction="")
    with pytest.raises(ValueError, match="zero incremental"):
        choose([bad])


def test_autonomous_practice_is_a_real_scored_channel():
    d = choose([
        bounded("internal_computation", (1, 1), (0, 0), (1, 1), instruction="think"),
        bounded("autonomous_practice", (3, 3), (2, 2), (1, 1), instruction="sandbox transfer test"),
        abstain(),
    ])
    assert d.chosen_mode == MetaMode.AUTONOMOUS_PRACTICE
    assert d.chosen_instruction == "sandbox transfer test"


def test_channel_cannot_be_silently_omitted():
    with pytest.raises(ValueError, match="every meta-mode"):
        _choose_meta_mode([abstain()])


def test_goal_relaxation_is_terminal_not_an_acquisition_crash():
    relax = bounded("goal_relaxation", (4, 4), (2, 2), (1, 1), instruction="propose smaller goal")
    d = choose([relax, abstain()])
    assert d.chosen_mode == MetaMode.GOAL_RELAXATION
    assert d.decision == "stop"
    assert d.stop_reason == "goal_relaxation_highest_net_value"
    assert d.chosen_instruction == "propose smaller goal"


def test_mode_contract_cannot_be_replaced_by_forecast_instruction():
    bound = bind_acquisition_instruction(
        "Practice only in sandbox; do not touch live target.", "try three fixtures")
    assert "sandbox" in bound and "do not touch live target" in bound
    assert "try three fixtures" in bound and "FORECAST-SPECIFIC DETAIL" in bound


def test_expense_that_cannot_fit_decision_column_is_semantically_rejected():
    option = bounded("environment_experiment", (1, 1), (1, 1), (1, 1))
    option["expected_expense_usd"] = "1000000"
    with pytest.raises(ValueError, match="consequence metadata"):
        choose([option, abstain()])


def test_unknown_and_all_blocked_terminal_decisions_are_not_goal_proposals():
    unknown = choose([{"mode": "internal_computation", "state": "unknown",
                       "direct_value": None, "information_value": None, "cost": None,
                       "evidence_refs": ["missing"], "rationale": "unknown", "instruction": "",
                       "block_reason": None, "wake_condition": "new evidence"}, abstain()])
    assert unknown.chosen_mode is None and not is_goal_relaxation_decision(unknown)
