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
