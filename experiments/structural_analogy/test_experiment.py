from __future__ import annotations
import copy
import importlib.util
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import experiment
import verify_result


def test_cross_domain_retrieval_has_real_mapping_and_transfer():
    corpus = experiment.load_corpus(HERE / "cross_domain_cases.json")
    target = next(x for x in corpus["problems"] if x["id"] == "malware_lateral")
    result = experiment.retrieve_structural(corpus["analogy_kb"], target, require_cross_domain=True)
    assert result["source_domain"] != result["target_domain"]
    assert len(result["role_correspondences"]) >= 2
    assert len(result["relation_correspondences"]) >= 2
    assert len(result["transferred_candidate_inferences"]) >= 1
    # The target did not contain the intervention; it enters only as a transferred new role.
    assert any(endpoint["kind"] == "new_analogue"
               for inference in result["transferred_candidate_inferences"]
               for endpoint in (inference["target_subject"], inference["target_object"]))


def test_transfer_relations_cannot_leak_into_retrieval_score():
    corpus = experiment.load_corpus(HERE / "cross_domain_cases.json")
    target = corpus["problems"][0]
    source = copy.deepcopy(corpus["analogy_kb"][0])
    before = experiment.structural_match(source, target)
    source["transfer_relations"] = [["intervention", "magically_solves", "asset", "answer_leak"]]
    after = experiment.structural_match(source, target)
    assert before == after


def test_direction_and_topology_matter_not_relation_bag_overlap():
    source = {"roles": {"a":"a", "b":"b", "c":"c"},
              "observed_relations": [["a","p","b","flow"],["b","q","c","exposure"]]}
    target = {"roles": {"x":"x", "y":"y", "z":"z"},
              "observed_relations": [["x","p","y","flow"],["z","q","y","exposure"]]}
    match = experiment.structural_match(source, target)
    assert match is not None
    assert match["matched_relation_count"] == 1  # families overlap, graph topology does not


def test_paired_stats_reports_number_in_either_direction():
    positive = experiment.paired_stats([4, 5, 4], [3, 3, 4])
    negative = experiment.paired_stats([2, 2, 3], [3, 4, 3])
    assert positive["mean_delta"] > 0
    assert negative["mean_delta"] < 0
    assert len(positive["bootstrap_95_ci"]) == 2


def _valid_result():
    retrieval = {"source_domain":"source", "target_domain":"target",
                 "role_correspondences":[{"a":1},{"b":2}],
                 "relation_correspondences":[{"a":1},{"b":2}],
                 "transferred_candidate_inferences":[{"candidate":1}]}
    record = {"problem_id":"p1", "candidates":{}, "judgments":[], "retrieval":retrieval}
    for arm in experiment.ARMS:
        record["candidates"][arm] = {"candidate_text": f"{arm} idea"}
    record["judgments"] = [{"scores": {arm: {"usefulness":3} for arm in experiment.ARMS}}]
    comp = {"mean_delta":0.0, "bootstrap_95_ci":[-1,1], "significant_positive_gain":False}
    corpus = {"model":"deepseek-v4-flash", "judge_model":"deepseek-v4-flash",
              "records":[record], "summary":{"arms":{arm:{} for arm in experiment.ARMS},
              "structural_minus_baseline":{"direct":copy.deepcopy(comp),"semantic":copy.deepcopy(comp)}}}
    control = copy.deepcopy(corpus)
    control["records"][0]["retrieval"]["target_domain"] = "source"
    return {"conclusion":"measured_null_or_negative", "cross_domain":corpus, "watchdog_control":control}


def test_completion_verifier_accepts_numeric_null():
    assert verify_result.verify_result(_valid_result()) == []


def test_completion_verifier_rejects_missing_transferred_inference():
    broken = _valid_result()
    broken["cross_domain"]["records"][0]["retrieval"]["transferred_candidate_inferences"] = []
    errors = verify_result.verify_result(broken)
    assert any("no transferred candidate inference" in x for x in errors)


def test_completion_verifier_rejects_watchdog_gain():
    broken = _valid_result()
    broken["watchdog_control"]["summary"]["structural_minus_baseline"]["direct"]["significant_positive_gain"] = True
    errors = verify_result.verify_result(broken)
    assert any("measurement is void" in x for x in errors)


def test_experiment_is_not_imported_by_main_path():
    root = HERE.parent.parent
    forbidden = []
    for folder in (root / "runner", root / "management", root / "ralph_portable"):
        for path in folder.rglob("*.py"):
            if "structural_analogy" in path.read_text(errors="ignore"):
                forbidden.append(str(path.relative_to(root)))
    assert forbidden == []


def test_receipt_corpus_is_cross_project_and_role_ids_are_not_generic_slots():
    import failure_experiment as failure
    data = json.loads((HERE / "receipt_failure_cases.json").read_text())
    assert len(data["sources"]) == 9
    assert len({x["domain"] for x in data["sources"]}) == 9
    assert len(data["targets"]) == 5
    assert all("c2f-1557-manager" in x["provenance"] for x in data["targets"])
    generic = {"assertion", "receipt", "effect", "observer"}
    assert all(not (set(x["roles"]) & generic) for x in data["sources"] + data["targets"])


def test_mapping_validator_rejects_non_bijective_or_invented_correspondence():
    import failure_experiment as failure
    data = json.loads((HERE / "receipt_failure_cases.json").read_text())
    source = next(x for x in data["sources"] if x["id"] == "single_grep_universal")
    target = next(x for x in data["targets"] if x["id"] == "ralph_empty_input_ambiguity")
    good = {
        "role_correspondences": [
            {"source_role":"empty_narrow_query","target_role":"zero_parsed_clauses"},
            {"source_role":"universal_absence_claim","target_role":"ambiguity_claim"},
            {"source_role":"population_wide_absence","target_role":"actual_contract_content"},
        ],
        "relation_correspondences": [
            {"source_relation":source["relations"][0],"target_relation":target["relations"][0]},
            {"source_relation":source["relations"][1],"target_relation":target["relations"][1]},
        ],
        "transferred_candidate_inference":"Enumerate all authorized locations before concluding absence.",
    }
    assert failure.validate_structural_mapping(target, source, good) == []
    broken = copy.deepcopy(good)
    broken["role_correspondences"][1]["target_role"] = "zero_parsed_clauses"
    assert any("repeated target role" in x for x in failure.validate_structural_mapping(target, source, broken))


def test_final_failure_result_and_deliberate_break_are_distinguished():
    import verify_failure_result
    result_path = HERE / "failure_results.json"
    if not result_path.exists():
        import pytest
        pytest.skip("generated result not present")
    data = json.loads(result_path.read_text())
    assert verify_failure_result.verify(data) == []
    broken = copy.deepcopy(data)
    broken["receipt_failure"]["records"][0]["candidates"]["structural"]["transferred_candidate_inference"] = ""
    assert any("missing transferred candidate inference" in x for x in verify_failure_result.verify(broken))


def test_invariance_corpus_gold_counts_and_classes_are_self_consistent():
    import invariance_experiment as inv
    data = inv.load_corpus(HERE / "invariance_cases.json")
    assert len(data["cases"]) == 14
    assert len({c["domain"] for c in data["cases"]}) == 14
    assert {c["system"] for c in data["cases"]} == {"AQ", "Ralph"}
    assert sum(r["gold_class"] == "invariant" for r in data["rules"]) == 2
    assert all(r["gold_support_count"] == len(r["applies_to"]) for r in data["rules"])


def test_mcnemar_exact_detects_symmetric_and_one_sided_disagreement():
    import invariance_experiment as inv
    same = inv.mcnemar_exact([True, False], [True, False])
    assert same["two_sided_p"] == 1.0
    one_sided = inv.mcnemar_exact([False] * 6, [True] * 6)
    assert one_sided["structural_only_correct"] == 6
    assert one_sided["two_sided_p"] == 0.03125


def test_final_invariance_result_and_broken_per_case_count_are_distinguished():
    import verify_invariance_result
    result_path = HERE / "invariance_results.json"
    if not result_path.exists():
        import pytest
        pytest.skip("generated invariance result not present")
    data = json.loads(result_path.read_text())
    assert verify_invariance_result.verify(data) == []
    broken = copy.deepcopy(data)
    row = broken["records"][0]["predictions"]["structural"]["per_case"][0]
    row["applies"] = not bool(row["applies"])
    assert any("structural per_case/count mismatch" in x for x in verify_invariance_result.verify(broken))
