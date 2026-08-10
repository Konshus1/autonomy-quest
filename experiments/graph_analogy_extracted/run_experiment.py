"""Reproduce the frozen machine-extracted graph analogy experiment.

Model calls are not made here. Their blinded prompts and frozen outputs are
stored beside this script. The executable verdict is deterministic and offline.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ralph_portable.structural_analogy import (
    Relation,
    Situation,
    compare_retrieval_regimes,
    load_situation_file,
    load_situation_library_file,
    retrieve_analogy,
    structural_match,
)

HERE = Path(__file__).resolve().parent
SOURCE_FILE = ROOT / "experiments" / "graph_analogy" / "corpus.json"


def _match_row(match) -> dict:
    return {
        "source_id": match.source.id,
        "accepted": match.accepted,
        "matched_relation_count": match.matched_relation_count,
        "coverage": match.coverage,
        "largest_connected_component": match.largest_connected_component,
        "relational_depth": match.relational_depth,
        "systematicity_score": match.systematicity_score,
        "role_mapping": dict(match.role_mapping),
        "rejection_reason": match.rejection_reason,
    }


def _has_path_interruption(plan: dict) -> bool:
    text = " ".join(plan["steps"]).lower()
    path = "peer-to-peer" in text or "peer connectivity" in text
    action = any(term in text for term in ("block", "deny", "restrict", "segment", "isolation"))
    return path and action


def run() -> dict:
    target = load_situation_file(HERE / "extracted_target.raw.json")
    reference = load_situation_file(HERE / "human_reference.json")
    sources = load_situation_library_file(SOURCE_FILE)
    source_by_id = {source.id: source for source in sources}
    raw_target = json.loads((HERE / "extracted_target.raw.json").read_text())
    target_text = (HERE / "target.txt").read_text()

    evidence_spans = [row["evidence"] for row in raw_target["observed_relations"]]
    evidence_grounded = all(span in target_text for span in evidence_spans)
    forbidden = ("interruption", "protection", "segmentation", "firewall", "isolation", "recommendation")
    frozen_graph_text = " ".join(
        f"{row['predicate']} {row['family']}" for row in raw_target["observed_relations"]
    ).lower()
    no_inferred_intervention = not any(term in frozen_graph_text for term in forbidden)

    result = retrieve_analogy(target, sources, shortlist_size=2)
    regimes = compare_retrieval_regimes(target, sources, shortlist_size=len(sources))
    all_matches = {source.id: structural_match(source, target) for source in sources}

    firebreak = source_by_id["wildfire_firebreak"]
    broken = replace(
        firebreak,
        id="wildfire_firebreak_rewired_control",
        observed_relations=(
            Relation("fire", "spreads_through", "fuel", "propagation"),
            Relation("community", "exposes", "fuel", "exposure"),
        ),
    )
    broken_match = structural_match(broken, target)
    opaque = Situation(
        id="opaque_source_control",
        domain="opaque",
        title="ZXQ",
        summary="No shared words",
        signature=firebreak.signature,
        roles={"u1": "g1", "u2": "g2", "u3": "g3", "u4": "g4"},
        observed_relations=(
            Relation("u1", "p9", "u2", "propagation"),
            Relation("u2", "p8", "u3", "exposure"),
        ),
        transferable_relations=(Relation("u4", "p7", "u2", "interruption"),),
    )
    opaque_match = structural_match(opaque, target)
    reference_match = structural_match(reference, target)

    without_good = tuple(source for source in sources if source.id != "wildfire_firebreak")
    abstained = False
    abstention_error = None
    try:
        retrieve_analogy(target, without_good, shortlist_size=len(without_good))
    except LookupError as exc:
        abstained = True
        abstention_error = str(exc)

    planner_outputs = json.loads((HERE / "planner_outputs.json").read_text())
    plan_path_interruption = {
        arm: _has_path_interruption(plan) for arm, plan in planner_outputs.items()
    }
    new_approach_passed = (
        plan_path_interruption["structural"]
        and not plan_path_interruption["baseline"]
        and not plan_path_interruption["surface"]
    )

    candidates = json.loads((HERE / "candidate_approaches.json").read_text())
    evaluation = json.loads((HERE / "blind_candidate_evaluation.json").read_text())
    structural_candidate_order = sorted(
        ("M2", "Q7"),
        key=lambda candidate_id: (
            -all_matches[candidates[candidate_id]["source_id"]].systematicity_score,
            candidate_id,
        ),
    )
    surface_score_by_source = dict(regimes.surface_ranking)
    surface_candidate_order = sorted(
        ("M2", "Q7"),
        key=lambda candidate_id: (
            -surface_score_by_source[candidates[candidate_id]["source_id"]],
            candidate_id,
        ),
    )
    extraction_survived = (
        evidence_grounded
        and no_inferred_intervention
        and reference_match.accepted
        and result.selected.source.id == "wildfire_firebreak"
        and not broken_match.accepted
        and opaque_match.accepted
        and abstained
    )
    validation_passed = (
        extraction_survived
        and structural_candidate_order[0] == "M2"
        and surface_candidate_order[0] == "Q7"
        and evaluation["ranking"][0] == "M2"
        and evaluation["viable"]["M2"] is True
        and evaluation["viable"]["Q7"] is False
    )
    verdict = "new-approach" if new_approach_passed else "validation" if validation_passed else "neither"

    return {
        "verdict": verdict,
        "claim": (
            "On this one frozen machine-extracted target graph, graph-based structural analogy "
            "validated/ranked the viable candidate above the surface-selected candidate."
            if verdict == "validation" else
            "No downstream usefulness claim passed."
        ),
        "claim_not_made": "new plan direction" if not new_approach_passed else None,
        "scope": "one plain-text target, one model extraction, one blinded candidate judgment; advisory only",
        "cost": {
            "metered_usd": 0.0,
            "access": "subscription RLM only",
            "model": "openai-codex/gpt-5.6-sol",
            "frozen_model_calls": 5,
            "reproduction_network_calls": 0,
        },
        "extractor_blinding": {
            "source_cases_visible": False,
            "transfer_relations_visible": False,
            "winner_identity_visible": False,
            "interventions_allowed": False,
            "prompt_file": "extractor_prompt.txt",
        },
        "extraction": {
            "evidence_spans_all_verbatim": evidence_grounded,
            "observed_relation_count": len(target.observed_relations),
            "no_inferred_interventions": no_inferred_intervention,
            "rejected_inferences": raw_target["rejected_inferences"],
            "human_reference_to_extracted": _match_row(reference_match),
            "known_noise": (
                "The extractor collapsed process/path roles into infected_laptop/peer_machines; "
                "the held-out reference used encryption_process/peer_connections. "
                "The two-edge family topology nevertheless remained endpoint-preserving."
            ),
        },
        "stage_1_shortlist": [
            {"source_id": row.situation.id, "cheap_score": row.cheap_score}
            for row in result.shortlist
        ],
        "stage_2_matches": [_match_row(match) for match in result.structural_candidates],
        "stage_2_selected": result.selected.source.id,
        "projection": asdict(result.plan_proposal),
        "controls": {
            "same_words_families_rewired": _match_row(broken_match),
            "opaque_zero_shared_words": _match_row(opaque_match),
            "no_accepted_structure_abstained": abstained,
            "abstention_error": abstention_error,
        },
        "downstream_planner": {
            "path_interruption_step_by_arm": plan_path_interruption,
            "new_approach_passed": new_approach_passed,
            "why_new_approach_failed": (
                "Baseline and surface arms both independently proposed blocking peer-to-peer paths, "
                "so the structural arm did not surface a direction they missed."
            ),
        },
        "candidate_validation": {
            "candidate_source_ids": {
                candidate_id: row["source_id"] for candidate_id, row in candidates.items()
            },
            "structural_order": structural_candidate_order,
            "surface_order": surface_candidate_order,
            "structural_scores": {
                candidate_id: all_matches[candidates[candidate_id]["source_id"]].systematicity_score
                for candidate_id in ("M2", "Q7")
            },
            "surface_scores": {
                candidate_id: surface_score_by_source[candidates[candidate_id]["source_id"]]
                for candidate_id in ("M2", "Q7")
            },
            "blind_judge": evaluation,
            "validation_passed": validation_passed,
        },
        "would_falsify_claim": (
            "The claim would be false here if extraction broke the connected topology, the matcher failed/forced "
            "a match, the rewired control passed, surface ranked M2 first, or the blind judge did not rank M2 "
            "viable above Q7."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=HERE / "receipt.json")
    args = parser.parse_args()
    receipt = run()
    text = json.dumps(receipt, indent=2) + "\n"
    args.output.write_text(text)
    print(text, end="")


if __name__ == "__main__":
    main()
