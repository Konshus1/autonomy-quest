"""Reproduce and verify the frozen multi-case analogy-pursuit extension.

No model calls are made. This validates prompts, subscription envelopes, graph
retrieval, deterministic load-bearing content, blind judges, and provenance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ralph_portable.structural_analogy import (
    compare_retrieval_regimes,
    load_situation_file,
    load_situation_library_file,
    structural_match,
)

HERE = Path(__file__).resolve().parent
MULTI = HERE / "multi_case"
ARMS = ("baseline", "surface", "structural", "frontier")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_response(path: Path) -> dict:
    text = path.read_text().strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def _chain_core(text: str) -> bool:
    lower = text.lower()
    return (
        "chain-ladder" in lower
        and "49.5" in lower
        and re.search(r"\b33\b", lower) is not None
        and "17.5" in lower
        and "1.5" in lower
        and "1.1" in lower
    )


def _engset_core(text: str) -> bool:
    lower = text.lower()
    return (
        "engset" in lower
        and re.search(r"(?:c\s*=\s*6|6 technicians)", lower) is not None
        and re.search(r"(?:3\.4|3\.44|0\.0344)", lower) is not None
        and re.search(r"(?:10\.7|0\.107)", lower) is not None
        and ("n-1" in lower or "n − 1" in lower or "n=10" in lower)
    )


VALIDATORS = {
    "defect_chain_ladder": _chain_core,
    "robot_engset": _engset_core,
}


def _verify_arm(case_dir: Path, arm: str, expected_model: str) -> dict | None:
    arm_dir = case_dir / "arms" / arm
    prompt = arm_dir / "prompt.txt"
    assert _sha(prompt) == (arm_dir / "prompt_sha256.txt").read_text().strip()
    response_path = arm_dir / "response.txt"
    envelope_path = arm_dir / "cli_stdout.json"
    if not response_path.exists():
        return None
    envelope = json.loads(envelope_path.read_text())
    assert envelope["result"].rstrip("\n") == response_path.read_text().rstrip("\n")
    assert expected_model in envelope["modelUsage"]
    assert envelope["is_error"] is False
    return {
        "prompt_sha256": _sha(prompt),
        "response_sha256": _sha(response_path),
        "envelope_sha256": _sha(envelope_path),
        "session_id": envelope["session_id"],
        "estimated_cost_usd_not_billed": envelope["total_cost_usd"],
        "model": expected_model,
    }


def _verify_judge(case_dir: Path, judge_id: str) -> tuple[dict, dict]:
    judge_dir = case_dir / "judges" / judge_id
    assert _sha(judge_dir / "prompt.txt") == (judge_dir / "prompt_sha256.txt").read_text().strip()
    mapping = json.loads((judge_dir / "anonymous_map.json").read_text())
    assert set(mapping.values()) == set(ARMS) and len(set(mapping)) == 4
    result = _json_response(judge_dir / "response.txt")
    evaluations = result["evaluations"]
    assert set(evaluations) == set(mapping)
    assert set(result["ranking"]) == set(mapping)
    by_arm = {}
    for label, row in evaluations.items():
        assert len(row["criteria"]) == 5
        assert row["score"] == sum(row["criteria"])
        assert row["core_success"] == (all(row["criteria"][:4]) and not row["fatal_error"])
        by_arm[mapping[label]] = row
    return by_arm, {
        "prompt_sha256": _sha(judge_dir / "prompt.txt"),
        "response_sha256": _sha(judge_dir / "response.txt"),
        "anonymous_map_sha256": _sha(judge_dir / "anonymous_map.json"),
    }


def run_multi_case() -> dict:
    manifest = json.loads((MULTI / "manifest.json").read_text())
    outcomes = json.loads((MULTI / "outcomes.json").read_text())
    outcome_by_id = {row["id"]: row for row in outcomes["rows"]}
    assert len(manifest["cases"]) == len(outcome_by_id) == outcomes["new_tasks_tried"]
    assert manifest["prompt_template_sha256"] == hashlib.sha256(manifest["prompt_template"].encode()).hexdigest()

    rows = []
    sessions = set()
    for declared in manifest["cases"]:
        case_id = declared["id"]
        case_dir = MULTI / case_id
        case = json.loads((case_dir / "case.json").read_text())
        assert _sha(case_dir / "case.json") == declared["case_sha256"]

        source_by_arm = {
            "baseline": "No retrieved case was supplied.",
            "surface": case["surface"],
            "structural": case["structural"],
            "frontier": "No retrieved case was supplied.",
        }
        prompt_sha = {}
        normalized = set()
        for arm in ARMS:
            rendered = manifest["prompt_template"].format(
                target=case["target"], analogue_content=source_by_arm[arm]
            )
            frozen = (case_dir / "arms" / arm / "prompt.txt").read_text()
            assert frozen == rendered
            prompt_sha[arm] = _sha(case_dir / "arms" / arm / "prompt.txt")
            assert prompt_sha[arm] == declared["prompt_sha256"][arm]
            normalized.add(hashlib.sha256(frozen.replace(source_by_arm[arm], "{ANALOGUE_CONTENT}").encode()).hexdigest())
        assert len(normalized) == 1
        assert prompt_sha["baseline"] == prompt_sha["frontier"]

        target = load_situation_file(case_dir / "target_graph.json")
        sources = load_situation_library_file(case_dir / "source_graphs.json")
        retrieval = compare_retrieval_regimes(target, sources, shortlist_size=3)
        structural_id = f"{case_id}_structural_source"
        surface_id = f"{case_id}_surface_source"
        near_id = f"{case_id}_near_miss"
        assert retrieval.structural_source_id == structural_id
        assert retrieval.surface_source_id == surface_id
        source_map = {source.id: source for source in sources}
        true_match = structural_match(source_map[structural_id], target)
        near_match = structural_match(source_map[near_id], target)
        assert true_match.accepted and true_match.systematicity_score > near_match.systematicity_score

        arm_receipts = {}
        for arm in ARMS:
            expected = "claude-fable-5" if arm == "frontier" else "claude-haiku-4-5"
            item = _verify_arm(case_dir, arm, expected)
            arm_receipts[arm] = item
            if item:
                assert item["session_id"] not in sessions
                sessions.add(item["session_id"])

        outcome = outcome_by_id[case_id]
        row = {
            "id": case_id,
            "target_domain": case["target_domain"],
            "structural_source_domain": case["source_domain"],
            "status": outcome["status"],
            "reason": outcome["reason"],
            "prompt_sha256": prompt_sha,
            "graph_sha256": {
                "target": _sha(case_dir / "target_graph.json"),
                "sources": _sha(case_dir / "source_graphs.json"),
            },
            "retrieval": {
                "surface_selected": retrieval.surface_source_id,
                "structural_selected": retrieval.structural_source_id,
                "structural_score": true_match.systematicity_score,
                "near_miss_score": near_match.systematicity_score,
            },
            "arms": arm_receipts,
        }
        if outcome["status"] == "FOUND_TRIPLE":
            validator = VALIDATORS[case_id]
            content = {
                arm: validator((case_dir / "arms" / arm / "response.txt").read_text())
                for arm in ARMS
            }
            assert content == {"baseline": False, "surface": False, "structural": True, "frontier": True}
            fable, fable_hashes = _verify_judge(case_dir, "fable")
            codex, codex_hashes = _verify_judge(case_dir, "codex")
            row["content_core"] = content
            row["blind_judges"] = {
                "fable": {"scores_by_arm": {arm: fable[arm]["score"] for arm in ARMS}, "criteria_by_arm": {arm: fable[arm]["criteria"] for arm in ARMS}, "fatal_by_arm": {arm: fable[arm]["fatal_error"] for arm in ARMS}, "hashes": fable_hashes},
                "codex": {"scores_by_arm": {arm: codex[arm]["score"] for arm in ARMS}, "criteria_by_arm": {arm: codex[arm]["criteria"] for arm in ARMS}, "fatal_by_arm": {arm: codex[arm]["fatal_error"] for arm in ARMS}, "hashes": codex_hashes},
            }
            cheap_cost = arm_receipts["structural"]["estimated_cost_usd_not_billed"]
            frontier_cost = arm_receipts["frontier"]["estimated_cost_usd_not_billed"]
            row["frontier_to_structural_estimated_cost_ratio"] = round(frontier_cost / cheap_cost, 2)
        rows.append(row)

    found = [row for row in rows if row["status"] == "FOUND_TRIPLE"]
    assert len(found) == outcomes["new_found_triples"] == 2
    return {
        "verdict": "two-new-existence-triples; requested-three-to-five-not-earned",
        "claim": "Two new one-shot cases show a structural source supplying a load-bearing calculation that Haiku baseline and same-domain surface context missed, with Fable reaching it unaided.",
        "scope_limit": "32 hand-constructed, preregistered task/source attempts; not a population benchmark, causal retrieval isolation, repeated-run reliability result, whole-answer parity result, or ~40x realized-envelope result.",
        "models": {"cheap": "claude-haiku-4-5", "frontier": "claude-fable-5"},
        "access": {"experiment_calls": "subscription CLIs", "metered_experiment_usd": 0.0, "new_found_case_realized_envelope_ratios": {row["id"]: row["frontier_to_structural_estimated_cost_ratio"] for row in found}, "cost_verdict": "The new cases realized only about 12-13x frontier/structural envelope ratios, so the requested ~40x aggregate claim is not earned."},
        "aggregate": {"new_triples": len(found), "new_tasks_tried": len(rows), "including_prior_chao1_triples": len(found)+1, "including_prior_chao1_tasks": len(rows)+1},
        "preregistration_commits": outcomes["preregistration_commits"],
        "prompt_template_sha256": manifest["prompt_template_sha256"],
        "rows": rows,
        "what_would_have_falsified_each_found_case": "Baseline or surface producing the preregistered numeric core; structural or frontier omitting/miscalculating it; graph retrieval selecting the wrong source; prompt normalization differing outside analogue content; artifact hashes/envelopes failing verification.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=MULTI / "receipt.json")
    args = parser.parse_args()
    receipt = run_multi_case()
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print("verdict:", receipt["verdict"])
    print("new triples/tasks:", receipt["aggregate"]["new_triples"], receipt["aggregate"]["new_tasks_tried"])
    print("including prior Chao1 triples/tasks:", receipt["aggregate"]["including_prior_chao1_triples"], receipt["aggregate"]["including_prior_chao1_tasks"])
    for row in receipt["rows"]:
        print(row["id"], row["status"], row["reason"])


if __name__ == "__main__":
    main()
