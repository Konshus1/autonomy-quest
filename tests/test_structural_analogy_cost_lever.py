"""Frozen cost-lever verdict and matcher stress controls for task #4834."""
from experiments.graph_analogy_cost_lever.run_experiment import run


def test_cheap_model_cost_lever_existence_triple():
    receipt = run()
    assert receipt["verdict"] == "cost-lever-existence-proof"
    scores = receipt["content_scores"]
    assert scores["baseline"]["core_quantitative_plan_element"] is False
    assert scores["surface"]["core_quantitative_plan_element"] is False
    assert scores["structural"]["core_quantitative_plan_element"] is True
    assert scores["frontier"]["core_quantitative_plan_element"] is True
    assert receipt["baseline_fails_surface_fails_structure_succeeds"] is True
    assert receipt["frontier_alone_reaches_core_element"] is True
    assert receipt["blind_judge"]["scores_by_arm"] == {
        "frontier": 5, "structural": 4, "baseline": 2, "surface": 1
    }


def test_matcher_stress_separates_structure_from_surface_and_near_miss():
    receipt = run()
    retrieval = receipt["retrieval"]
    controls = receipt["matcher_controls"]
    assert retrieval["surface_selected"] == "protocol_coverage_gate"
    assert retrieval["structural_selected"] == "ecology_chao1"
    assert retrieval["true_match"]["systematicity_score"] > retrieval["near_miss"]["systematicity_score"]
    assert controls["scrambled_topology"]["accepted"] is False
    assert controls["opaque_rename"]["accepted"] is True
    assert controls["opaque_rename"]["systematicity_score"] == retrieval["true_match"]["systematicity_score"]
    assert controls["partial_target"]["accepted"] is True
    assert controls["partial_target_near_miss"]["accepted"] is True
    assert controls["partial_target_near_miss"]["systematicity_score"] > controls["partial_target"]["systematicity_score"]
    assert receipt["partial_target_break_frozen"] is True
    assert controls["extra_target_relation_true"]["systematicity_score"] > controls["extra_target_relation_near_miss"]["systematicity_score"]
    assert controls["heavy_source_noise_boundary"]["accepted"] is False
    assert receipt["matcher_solid"] is True



def test_multi_case_extension_verifies_all_frozen_artifacts_and_denominator():
    from experiments.graph_analogy_cost_lever.run_multi_case import run_multi_case

    receipt = run_multi_case()
    assert receipt["verdict"] == "two-new-existence-triples; requested-three-to-five-not-earned"
    assert receipt["aggregate"] == {
        "new_triples": 2,
        "new_tasks_tried": 32,
        "including_prior_chao1_triples": 3,
        "including_prior_chao1_tasks": 33,
    }
    found = {row["id"]: row for row in receipt["rows"] if row["status"] == "FOUND_TRIPLE"}
    assert set(found) == {"defect_chain_ladder", "robot_engset"}
    for row in found.values():
        assert row["content_core"] == {
            "baseline": False,
            "surface": False,
            "structural": True,
            "frontier": True,
        }
        assert row["prompt_sha256"]["baseline"] == row["prompt_sha256"]["frontier"]
        assert row["retrieval"]["structural_score"] > row["retrieval"]["near_miss_score"]


def test_multi_case_blind_scores_and_honest_cost_scope_are_frozen():
    from experiments.graph_analogy_cost_lever.run_multi_case import run_multi_case

    receipt = run_multi_case()
    by_id = {row["id"]: row for row in receipt["rows"]}
    chain = by_id["defect_chain_ladder"]["blind_judges"]
    assert chain["fable"]["scores_by_arm"] == {
        "baseline": 2, "surface": 1, "structural": 4, "frontier": 5
    }
    assert chain["codex"]["scores_by_arm"] == {
        "baseline": 1, "surface": 1, "structural": 3, "frontier": 5
    }
    engset = by_id["robot_engset"]["blind_judges"]
    for judge in ("fable", "codex"):
        assert engset[judge]["scores_by_arm"] == {
            "baseline": 3, "surface": 3, "structural": 5, "frontier": 5
        }
        assert engset[judge]["fatal_by_arm"] == {
            "baseline": True, "surface": True, "structural": False, "frontier": False
        }
    assert receipt["access"]["new_found_case_realized_envelope_ratios"] == {
        "defect_chain_ladder": 12.97,
        "robot_engset": 12.2,
    }
    assert "not earned" in receipt["access"]["cost_verdict"]



def test_deepseek_extension_verifies_frozen_resamples_and_negative_cases():
    from experiments.graph_analogy_cost_lever.run_deepseek_extension import run_deepseek_extension

    receipt = run_deepseek_extension()
    assert receipt["verdict"] == "one-of-three-haiku-triples-transfers-to-deepseek-v4-flash"
    assert receipt["aggregate"] == {
        "deepseek_triples_holding": 1,
        "existing_haiku_triples_tested": 3,
    }
    rows = {row["id"]: row for row in receipt["rows"]}
    assert {case_id: row["deepseek_triple_holds"] for case_id, row in rows.items()} == {
        "chao1": False,
        "defect_chain_ladder": True,
        "robot_engset": False,
    }
    assert rows["chao1"]["deepseek_baseline_successes"] == 8
    assert rows["defect_chain_ladder"]["deepseek_baseline_successes"] == 0
    assert rows["robot_engset"]["deepseek_baseline_successes"] == 0
    assert rows["robot_engset"]["deepseek_surface_load_bearing_success"] is True
    assert rows["robot_engset"]["deepseek_structural_load_bearing_success"] is False
    assert rows["defect_chain_ladder"]["deepseek_frontier_ratio_if_hold"] == 2423.25


def test_deepseek_extension_prompt_provenance_stability_and_spend_are_frozen():
    from experiments.graph_analogy_cost_lever.run_deepseek_extension import run_deepseek_extension

    receipt = run_deepseek_extension()
    all_runs = []
    for row in receipt["rows"]:
        assert row["response_shas_unique_across_baselines"] == 10
        all_runs.extend(row["runs"]["baseline"])
        all_runs.extend((row["runs"]["surface"], row["runs"]["structural"]))
        assert abs(row["baseline_success_one_sided_95_upper"] - (
            0.9632285621125349 if row["id"] == "chao1" else 0.2588655508930522
        )) < 1e-12
    assert len(all_runs) == 36
    assert len({run["session_id"] for run in all_runs}) == 36
    assert receipt["metered_spend"]["valid_reported_cost_usd"] == 0.008865225600000002
    assert receipt["metered_spend"]["total_cli_reported_metered_cost_usd"] == 0.017790315200000004
    assert receipt["metered_spend"]["total_cli_reported_metered_cost_usd"] < 3.0
    assert "system prompt" in receipt["prompt_equivalence_limit"]
