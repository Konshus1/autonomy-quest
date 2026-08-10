"""Frozen extracted-graph usefulness verdict and file-backed path."""
from experiments.graph_analogy_extracted.run_experiment import run
from ralph_portable.structural_analogy import (
    load_situation_file,
    load_situation_library_file,
    retrieve_analogy,
)


def test_file_backed_matcher_loads_extractor_evidence_without_age():
    target = load_situation_file("experiments/graph_analogy_extracted/extracted_target.raw.json")
    sources = load_situation_library_file("experiments/graph_analogy/corpus.json")

    assert len(target.observed_relations) == 2
    result = retrieve_analogy(target, sources, shortlist_size=2)
    assert result.selected.source.id == "wildfire_firebreak"


def test_extracted_graph_earns_validation_but_not_new_approach_claim():
    receipt = run()

    assert receipt["verdict"] == "validation"
    assert receipt["downstream_planner"]["new_approach_passed"] is False
    assert receipt["candidate_validation"]["structural_order"] == ["M2", "Q7"]
    assert receipt["candidate_validation"]["surface_order"] == ["Q7", "M2"]
    assert receipt["candidate_validation"]["blind_judge"]["viable"]["M2"] is True
    assert receipt["candidate_validation"]["blind_judge"]["viable"]["Q7"] is False

    controls = receipt["controls"]
    assert controls["same_words_families_rewired"]["accepted"] is False
    assert controls["opaque_zero_shared_words"]["accepted"] is True
    assert controls["no_accepted_structure_abstained"] is True
