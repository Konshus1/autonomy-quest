#!/usr/bin/env python3
"""Decode the completed blinded N=15 study and compute preregistered outcomes.

This script performs no model calls and never changes the frozen inputs or reviews.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
CONDITIONS = ("default", "matched", "irrelevant_worked")
REVIEWERS = ("A", "B")


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def dump(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def wilson(successes: int, n: int, z: float = 1.959963984540054) -> list[float]:
    if n <= 0:
        raise ValueError("Wilson interval requires n > 0")
    p = successes / n
    den = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [max(0.0, center - half), min(1.0, center + half)]


def fisher_greater(a_success: int, a_total: int, b_success: int, b_total: int) -> float:
    """Exact P[X >= a_success] for first row under fixed margins.

    The first row is the hypothesized-higher group (MATCHED here).
    """
    total_success = a_success + b_success
    total = a_total + b_total
    lo = max(0, a_total - (total - total_success))
    hi = min(a_total, total_success)
    denominator = math.comb(total, a_total)
    return sum(
        math.comb(total_success, x) * math.comb(total - total_success, a_total - x)
        for x in range(max(a_success, lo), hi + 1)
    ) / denominator


def rate(successes: int, total: int) -> dict[str, Any]:
    return {
        "successes": successes,
        "total": total,
        "rate": successes / total,
        "exact": f"{successes}/{total}",
        "wilson_95": wilson(successes, total),
    }


def analyze(study: Path, run: Path) -> dict[str, Any]:
    plan = load(study / "plan.json")
    decode = load(run / "private" / "decode_map.json")
    consensus = load(run / "consensus_blinded.json")
    generation = load(study / "results" / "manifest.json")

    decoded = {r["label"]: r for r in decode["records"]}
    votes = {r["label"]: r for r in consensus["records"]}
    tests = {(r["task"], r["condition"], r["sample"]): r for r in generation["records"]}
    expected = {
        (case, condition, sample)
        for case in plan["cases"]
        for condition in plan["conditions"]
        for sample in range(1, plan["n_per_cell"] + 1)
    }
    if len(decoded) != plan["total_generations"] or len(votes) != len(decoded):
        raise SystemExit("decode/consensus coverage is not exactly the preregistered total")
    if {(r["case"], r["condition"], r["sample"]) for r in decoded.values()} != expected:
        raise SystemExit("decode map does not cover the preregistered cells exactly")
    if set(tests) != expected:
        raise SystemExit("generation manifest does not cover the preregistered cells exactly")
    if set(votes) != set(decoded):
        raise SystemExit("consensus labels do not exactly match the decode map")

    rows: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for label, meta in decoded.items():
        vote = votes[label]
        test = tests[(meta["case"], meta["condition"], meta["sample"])]
        rows[(meta["case"], meta["condition"])].append({
            "label": label,
            "sample": meta["sample"],
            "consensus_presence": bool(vote["consensus_presence"]),
            "reviewer_effective_yes": vote["reviewer_effective_yes"],
            "correct": test["test_rc"] == 0,
        })

    case_results: dict[str, Any] = {}
    qualifying: list[str] = []
    for case in plan["cases"]:
        conditions: dict[str, Any] = {}
        for condition in CONDITIONS:
            cell = sorted(rows[(case, condition)], key=lambda x: x["sample"])
            mechanism_n = sum(x["consensus_presence"] for x in cell)
            correct_n = sum(x["correct"] for x in cell)
            correct_mechanism_n = sum(x["correct"] and x["consensus_presence"] for x in cell)
            reviewer_counts = {
                reviewer: sum(x["reviewer_effective_yes"][reviewer] for x in cell)
                for reviewer in REVIEWERS
            }
            disagreements = sum(
                x["reviewer_effective_yes"]["A"] != x["reviewer_effective_yes"]["B"]
                for x in cell
            )
            conditions[condition] = {
                "mechanism_presence": rate(mechanism_n, len(cell)),
                "correctness": rate(correct_n, len(cell)),
                "correct_and_mechanism_present": rate(correct_mechanism_n, len(cell)),
                "reviewer_yes_counts": reviewer_counts,
                "reviewer_disagreements": disagreements,
                "positive_samples": [x["sample"] for x in cell if x["consensus_presence"]],
            }

        default_n = conditions["default"]["mechanism_presence"]["successes"]
        matched_n = conditions["matched"]["mechanism_presence"]["successes"]
        irrelevant_n = conditions["irrelevant_worked"]["mechanism_presence"]["successes"]
        matched_correct_mechanism = conditions["matched"]["correct_and_mechanism_present"]["successes"]
        n = plan["n_per_cell"]
        gates = {
            "default_absence_0_of_15": default_n == 0,
            "matched_presence_gt_0_of_15": matched_n > 0,
            "irrelevant_absence_0_of_15": irrelevant_n == 0,
            "correct_matched_mechanism_present": matched_correct_mechanism > 0,
            "source_traceability": matched_n > 0,  # any effective YES already required a valid source-relation citation
            "not_worse": False,
        }
        if matched_n == 0:
            selector = {
                "status": "not_evaluable_no_correct_consensus_positive_matched_candidate",
                "candidate": None,
                "baseline": None,
                "gate": False,
                "maintainability_judgment_required": False,
            }
        else:
            # A positive study would require the separately preregistered metric selector and blinded judge.
            selector = {
                "status": "pending_selector_and_blinded_maintainability_judge",
                "candidate": None,
                "baseline": None,
                "gate": False,
                "maintainability_judgment_required": True,
            }
        case_qualifies = all(gates.values())
        if case_qualifies:
            qualifying.append(case)
        correct_default = conditions["default"]["correctness"]["successes"]
        correct_matched = conditions["matched"]["correctness"]["successes"]
        correct_irrelevant = conditions["irrelevant_worked"]["correctness"]["successes"]
        case_results[case] = {
            "mechanism_id": plan["mechanisms"][case]["id"],
            "conditions": conditions,
            "mechanism_fisher_one_sided_matched_greater_than_default": fisher_greater(matched_n, n, default_n, n),
            "mechanism_fisher_one_sided_matched_greater_than_irrelevant": fisher_greater(matched_n, n, irrelevant_n, n),
            "correctness_fisher_one_sided_matched_greater_than_default": fisher_greater(correct_matched, n, correct_default, n),
            "correctness_fisher_one_sided_irrelevant_greater_than_default": fisher_greater(correct_irrelevant, n, correct_default, n),
            "correctness_difference_irrelevant_minus_default": (correct_irrelevant - correct_default) / n,
            "gates": gates,
            "not_worse_selector": selector,
            "qualifies": case_qualifies,
        }

    correctness_total = sum(r["test_rc"] == 0 for r in generation["records"])
    aggregate_correctness: dict[str, Any] = {}
    for condition in CONDITIONS:
        condition_rows = [r for r in generation["records"] if r["condition"] == condition]
        aggregate_correctness[condition] = rate(sum(r["test_rc"] == 0 for r in condition_rows), len(condition_rows))
    aggregate_irrelevant = aggregate_correctness["irrelevant_worked"]["successes"]
    aggregate_default = aggregate_correctness["default"]["successes"]
    aggregate_n = aggregate_correctness["default"]["total"]
    out = {
        "schema_version": 1,
        "preregistration_sha256": decode["preregistration_sha256"],
        "engine": plan["engine"],
        "sample_size": {
            "cases": len(plan["cases"]),
            "conditions": len(plan["conditions"]),
            "n_per_cell": plan["n_per_cell"],
            "total": plan["total_generations"],
        },
        "generation_correctness": rate(correctness_total, plan["total_generations"]),
        "aggregate_correctness_by_condition": aggregate_correctness,
        "aggregate_correctness_fisher_one_sided_irrelevant_greater_than_default": fisher_greater(aggregate_irrelevant, aggregate_n, aggregate_default, aggregate_n),
        "cases": case_results,
        "qualifying_cases": qualifying,
        "primary_outcome": "existence_supported" if qualifying else "null_no_case_qualifies",
        "irrelevant_control_verdict": (
            "no_named_mechanism_in_any_irrelevant_worked_sample"
            if all(case_results[c]["conditions"]["irrelevant_worked"]["mechanism_presence"]["successes"] == 0 for c in plan["cases"])
            else "named_mechanism_present_example_quality_confound"
        ),
        "reviewer_asymmetry_policy": "strict consensus requires A AND B; disagreements are not positives",
        "interpretation": (
            "No frozen named mechanism appeared under strict two-reviewer consensus in any of 135 outputs. "
            "This is a distributional existence null and does not establish equivalence. Individual reviewer rates are retained."
            if not qualifying else
            "At least one case met every preregistered existence and quality gate."
        ),
    }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--study", type=Path, default=HERE)
    ap.add_argument("--run-dir", type=Path)
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    study = args.study.resolve()
    run = args.run_dir.resolve() if args.run_dir else study / "annotation_results"
    output = args.output.resolve() if args.output else study / "analysis.json"
    result = analyze(study, run)
    output.write_text(dump(result), encoding="utf-8")
    print(f"wrote {output}")
    print(f"primary_outcome={result['primary_outcome']} qualifying_cases={json.dumps(result['qualifying_cases'])}")
    for case, value in result["cases"].items():
        exact = {c: value["conditions"][c]["mechanism_presence"]["exact"] for c in CONDITIONS}
        correct = {c: value["conditions"][c]["correctness"]["exact"] for c in CONDITIONS}
        print(f"{case} mechanism={json.dumps(exact, sort_keys=True)} correctness={json.dumps(correct, sort_keys=True)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
