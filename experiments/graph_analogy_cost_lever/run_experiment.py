"""Reproduce the frozen cheap-model graph-analogy cost-lever experiment.

No model calls are made here. Subscription CLI envelopes and prompts are frozen
under ``arms/`` and ``blind_judge_all/``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ralph_portable.structural_analogy import (
    Relation, Situation, compare_retrieval_regimes, load_situation_file,
    load_situation_library_file, project_plan, retrieve_analogy, structural_match,
)

HERE = Path(__file__).resolve().parent


def _row(match):
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


def _content(text: str) -> dict:
    lower = text.lower()
    # The load-bearing plan element is quantitative, not a self-report: choose
    # Chao1 and instantiate it to 80 unseen / 180 total from the supplied counts.
    chose_chao1 = "chao1" in lower
    computed_80 = bool(re.search(r"(?:=|about|approximately|~)\s*80\b|80\s+(?:unseen|additional|failure)", lower))
    computed_180 = bool(re.search(r"(?:=|about|approximately|~)\s*180\b|180\s+total", lower))
    formula = "f1" in lower and "f2" in lower and ("^2" in lower or "²" in lower)
    core = chose_chao1 and formula and computed_80 and computed_180
    return {
        "chose_chao1": chose_chao1,
        "instantiated_f1_f2_formula": formula,
        "computed_80_unseen": computed_80,
        "computed_180_total": computed_180,
        "core_quantitative_plan_element": core,
    }


def _rename(source: Situation) -> Situation:
    names = {name: f"opaque_{i}" for i, name in enumerate(sorted(source.roles), 1)}
    def rel(r):
        return Relation(names[r.subject], f"p_{r.family}", names[r.object], r.family, r.kind)
    return Situation(
        id="opaque_renamed_control", domain="z", title="ZXQ", summary="opaque",
        signature=source.signature,
        roles={names[k]: f"g{i}" for i, k in enumerate(sorted(source.roles), 1)},
        observed_relations=tuple(rel(r) for r in source.observed_relations),
        transferable_relations=tuple(rel(r) for r in source.transferable_relations),
    )


def run() -> dict:
    target = load_situation_file(HERE / "target_graph.json")
    sources = load_situation_library_file(HERE / "source_graphs.json")
    by_id = {s.id: s for s in sources}
    true = by_id["ecology_chao1"]
    near = by_id["ecology_rare_species_near_miss"]
    result = retrieve_analogy(target, sources, shortlist_size=3)
    regimes = compare_retrieval_regimes(target, sources, shortlist_size=3)

    true_match = structural_match(true, target)
    near_match = structural_match(near, target)
    surface_match = structural_match(by_id["protocol_coverage_gate"], target)

    scrambled = replace(
        true, id="scrambled_topology_control",
        observed_relations=(
            Relation("survey", "records", "observed_species", "sampling"),
            Relation("singleton_species", "reversed", "observed_species", "singleton_frequency"),
            Relation("doubleton_species", "reversed", "observed_species", "doubleton_frequency"),
            *true.observed_relations[3:],
        ),
    )
    opaque = _rename(true)
    partial_target = replace(target, id="partial_target_control", observed_relations=target.observed_relations[:2])
    noisy_target = replace(
        target, id="noisy_target_control",
        roles={**target.roles, "clock": "runtime clock"},
        observed_relations=target.observed_relations + (
            Relation("clock", "times", "campaign", "runtime_measurement"),
        ),
    )
    heavy_source = replace(
        true, id="heavy_source_noise_boundary",
        observed_relations=true.observed_relations + (
            Relation("weather", "colors", "observer", "noise_a"),
            Relation("observer", "greets", "survey", "noise_b"),
            Relation("weather", "follows", "observed_species", "noise_c"),
        ),
    )
    controls = {
        "scrambled_topology": _row(structural_match(scrambled, target)),
        "opaque_rename": _row(structural_match(opaque, target)),
        "partial_target": _row(structural_match(true, partial_target)),
        "extra_target_relation_true": _row(structural_match(true, noisy_target)),
        "extra_target_relation_near_miss": _row(structural_match(near, noisy_target)),
        "plausible_near_miss": _row(near_match),
        "heavy_source_noise_boundary": _row(structural_match(heavy_source, target)),
    }

    arm_text = {arm: (HERE / "arms" / arm / "response.txt").read_text()
                for arm in ("baseline", "surface", "structural", "frontier")}
    envelope = {arm: json.loads((HERE / "arms" / arm / "cli_stdout.json").read_text())
                for arm in arm_text}
    estimated_cost = {arm: envelope[arm]["total_cost_usd"] for arm in arm_text}
    scores = {arm: _content(text) for arm, text in arm_text.items()}
    triple = (not scores["baseline"]["core_quantitative_plan_element"]
              and not scores["surface"]["core_quantitative_plan_element"]
              and scores["structural"]["core_quantitative_plan_element"])
    frontier_reaches = scores["frontier"]["core_quantitative_plan_element"]

    judge = json.loads((HERE / "blind_judge_all" / "response.txt").read_text().strip().removeprefix("```json").removesuffix("```").strip())
    anon = json.loads((HERE / "blind_judge_all" / "anonymous_map.json").read_text())
    blind_by_arm = {anon[label]: judge["scores"][label] for label in judge["scores"]}

    matcher_solid = (
        result.selected.source.id == "ecology_chao1"
        and regimes.surface_source_id == "protocol_coverage_gate"
        and true_match.systematicity_score > near_match.systematicity_score
        and not controls["scrambled_topology"]["accepted"]
        and controls["opaque_rename"]["accepted"]
        and controls["opaque_rename"]["systematicity_score"] == true_match.systematicity_score
        and controls["partial_target"]["accepted"]
        and controls["extra_target_relation_true"]["systematicity_score"] > controls["extra_target_relation_near_miss"]["systematicity_score"]
    )
    verdict = "cost-lever-existence-proof" if triple and frontier_reaches and matcher_solid else "neither"

    return {
        "verdict": verdict,
        "claim": "On this one frozen task, structural graph retrieval supplied the quantitative Chao1 plan element that Haiku alone and Haiku with the lexical source omitted; the frontier model produced it alone.",
        "scope_limit": "One prospective existence case, one run per final arm; not a population-level quality or 100x price benchmark.",
        "models": {"cheap": "claude-haiku-4-5", "frontier": "claude-fable-5"},
        "access": {
            "experiment_calls": "claude.ai Max subscription",
            "auth_receipt": "subscription_auth_status.json",
            "metered_experiment_usd": 0.0,
            "cli_estimated_cost_usd_not_billed": estimated_cost,
            "frontier_to_structural_estimated_cost_ratio": round(estimated_cost["frontier"] / estimated_cost["structural"], 2),
            "cost_claim": "41.28x on these realized CLI envelopes, not 100x",
        },
        "retrieval": {
            "surface_selected": regimes.surface_source_id,
            "structural_selected": regimes.structural_source_id,
            "random_selected_seed_4834": regimes.random_source_id,
            "true_match": _row(true_match),
            "near_miss": _row(near_match),
            "score_margin_over_near_miss": round(true_match.systematicity_score - near_match.systematicity_score, 8),
            "projection": asdict(project_plan(target, true_match)),
        },
        "matcher_controls": controls,
        "matcher_solid": matcher_solid,
        "matcher_breaks_at": "With only the first two target relations, the true source remains accepted but the near miss becomes observationally indistinguishable and scores higher; with three core matches diluted by five source-noise edges (3/8 < 40%), the matcher abstains.",
        "content_scores": scores,
        "baseline_fails_surface_fails_structure_succeeds": triple,
        "frontier_alone_reaches_core_element": frontier_reaches,
        "blind_judge": {"ranking_opaque": judge["ranking"], "scores_by_arm": blind_by_arm, "criteria_opaque": judge["criteria"], "prompt_sha256_file": "blind_judge_all/prompt_sha256.txt"},
        "prompt_sha256": {arm: (HERE / "arms" / arm / "prompt_sha256.txt").read_text().strip() for arm in arm_text},
        "graph_sha256": {
            name: hashlib.sha256((HERE / name).read_bytes()).hexdigest()
            for name in ("target_graph.json", "source_graphs.json")
        },
        "what_would_have_falsified": "Haiku baseline or lexical arm computing 80 unseen and 180 total; structural Haiku omitting or miscomputing it; frontier omitting it; or topology/rename/noise controls failing to discriminate the selected source.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=HERE / "receipt.json")
    args = parser.parse_args()
    receipt = run()
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"verdict: {receipt['verdict']}")
    print("core element baseline/surface/structural/frontier:", *(receipt["content_scores"][a]["core_quantitative_plan_element"] for a in ("baseline", "surface", "structural", "frontier")))
    print("retrieval surface/structural:", receipt["retrieval"]["surface_selected"], receipt["retrieval"]["structural_selected"])
    print("matcher true/near scores:", receipt["retrieval"]["true_match"]["systematicity_score"], receipt["retrieval"]["near_miss"]["systematicity_score"])
    print("controls scrambled/opaque/partial:", receipt["matcher_controls"]["scrambled_topology"]["accepted"], receipt["matcher_controls"]["opaque_rename"]["accepted"], receipt["matcher_controls"]["partial_target"]["accepted"])
    print("blind scores baseline/surface/structural/frontier:", *(receipt["blind_judge"]["scores_by_arm"][a] for a in ("baseline", "surface", "structural", "frontier")))

if __name__ == "__main__":
    main()
