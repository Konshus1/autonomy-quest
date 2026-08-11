"""Offline verifier for the DeepSeek V4 Flash cost-lever extension.

No model calls are made. The verifier checks exact emitted prompt bytes, model
receipts, costs, baseline resamples, blind judges, and the frozen hold rule.
"""
from __future__ import annotations

import argparse
import gzip
from functools import lru_cache
import hashlib
import json
import re
import sys
from pathlib import Path

import math

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.graph_analogy_cost_lever.run_experiment import _content as chao_content
from experiments.graph_analogy_cost_lever.run_multi_case import _chain_core, _engset_core
from ralph_portable.structural_analogy import (
    compare_retrieval_regimes,
    load_situation_file,
    load_situation_library_file,
)

HERE = Path(__file__).resolve().parent
EXT = HERE / "deepseek_extension"
CASE_IDS = ("chao1", "defect_chain_ladder", "robot_engset")
LB_INDICES = {"chao1": (0, 1, 2), "defect_chain_ladder": (1, 2, 3), "robot_engset": (0, 1, 2, 3)}



def _binom_cdf(k: int, n: int, p: float) -> float:
    return sum(math.comb(n, i) * p**i * (1-p)**(n-i) for i in range(k+1))


def _solve_increasing(fn, target: float) -> float:
    lo, hi = 0.0, 1.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if fn(mid) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _solve_decreasing(fn, target: float) -> float:
    lo, hi = 0.0, 1.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if fn(mid) > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _clopper_pearson(successes: int, n: int) -> tuple[list[float], float]:
    lower = 0.0 if successes == 0 else _solve_increasing(
        lambda p: 1 - _binom_cdf(successes-1, n, p), .025
    )
    upper = 1.0 if successes == n else _solve_decreasing(
        lambda p: _binom_cdf(successes, n, p), .025
    )
    one_sided_upper = 1.0 if successes == n else _solve_decreasing(
        lambda p: _binom_cdf(successes, n, p), .05
    )
    return [lower, upper], one_sided_upper


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_json_text(path: Path) -> dict:
    text = path.read_text().strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def _events(path: Path) -> list[dict]:
    with gzip.open(path, "rb") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _text(message: dict) -> str:
    return "".join(part.get("text", "") for part in message["content"] if part.get("type") == "text")


def _validator(case_id: str, text: str) -> bool:
    if case_id == "chao1":
        return chao_content(text)["core_quantitative_plan_element"]
    if case_id == "defect_chain_ladder":
        return _chain_core(text)
    return _engset_core(text)


def _verify_run(case_id: str, arm: str, sample: int, prompt: bytes) -> dict:
    directory = EXT / case_id / "deepseek" / arm / f"sample_{sample:02d}"
    events = _events(directory / "cli_stdout.jsonl.gz")
    user = next(e["message"] for e in events if e["type"] == "message_end" and e.get("message", {}).get("role") == "user")
    assistant = [e["message"] for e in events if e["type"] == "message_end" and e.get("message", {}).get("role") == "assistant"][-1]
    assert _text(user).encode() == prompt
    assert assistant["provider"] == "deepseek"
    assert assistant["model"] == "deepseek-v4-flash"
    assert assistant["stopReason"] == "stop"
    frozen_receipt = json.loads((directory / "cli_receipt.json").read_text())["message"]
    assert assistant == frozen_receipt
    response = (directory / "response.txt").read_text()
    assert response.rstrip("\n") == _text(assistant).rstrip("\n")
    assert _sha(directory / "response.txt") == (directory / "response_sha256.txt").read_text().strip()
    assert hashlib.sha256(prompt).hexdigest() == (directory / "prompt_sha256.txt").read_text().strip()
    session = next(e["id"] for e in events if e["type"] == "session")
    return {
        "session_id": session,
        "cost_usd": assistant["usage"]["cost"]["total"],
        "tokens": assistant["usage"]["totalTokens"],
        "response_sha256": _sha(directory / "response.txt"),
        "raw_jsonl_gz_sha256": _sha(directory / "cli_stdout.jsonl.gz"),
        "mechanical_core_success": _validator(case_id, response),
    }


def _verify_judge(case_id: str, judge_id: str) -> tuple[dict, dict]:
    directory = EXT / case_id / "judges" / judge_id
    assert _sha(directory / "prompt.txt") == (directory / "prompt_sha256.txt").read_text().strip()
    mapping = json.loads((directory / "anonymous_map.json").read_text())
    expected = {f"baseline_{i:02d}" for i in range(1, 11)} | {"surface", "structural", "frontier"}
    assert set(mapping.values()) == expected and len(mapping) == 13
    result = _parse_json_text(directory / "response.txt")
    if judge_id == "fable":
        raw_path = directory / "cli_stdout.json"
        envelope = json.loads(raw_path.read_text())
        assert envelope["is_error"] is False
        assert envelope["stop_reason"] == "end_turn"
        assert set(envelope["modelUsage"]) == {"claude-fable-5"}
        assert envelope["result"].strip() == (directory / "response.txt").read_text().strip()
        provenance = {
            "cli": "claude",
            "model": "claude-fable-5",
            "session_id": envelope["session_id"],
            "raw_stdout_sha256": _sha(raw_path),
            "raw_stderr_sha256": _sha(directory / "cli_stderr.txt"),
        }
    else:
        raw_path = directory / "cli_stdout.jsonl"
        stderr = (directory / "cli_stderr.txt").read_text()
        assert raw_path.read_text().strip() == (directory / "response.txt").read_text().strip()
        assert "OpenAI Codex v0.144.0" in stderr
        assert "gpt-5.4" in stderr
        assert "session id:" in stderr.lower()
        session_match = re.search(r"session id:\x1b\[0m ([0-9a-f-]+)", stderr)
        assert session_match
        provenance = {
            "cli": "codex exec",
            "model": "gpt-5.4",
            "session_id": session_match.group(1),
            "raw_stdout_sha256": _sha(raw_path),
            "raw_stderr_sha256": _sha(directory / "cli_stderr.txt"),
        }
    assert set(result["evaluations"]) == set(mapping)
    assert set(result["ranking"]) == set(mapping)
    decoded = {}
    for label, row in result["evaluations"].items():
        assert len(row["criteria"]) == 5
        assert row["score"] == sum(row["criteria"])
        expected_success = all(row["criteria"][i] for i in LB_INDICES[case_id]) and not row["fatal_error"]
        assert row["load_bearing_success"] == expected_success
        decoded[mapping[label]] = row
    provenance.update({
        "prompt_sha256": _sha(directory / "prompt.txt"),
        "response_sha256": _sha(directory / "response.txt"),
        "anonymous_map_sha256": _sha(directory / "anonymous_map.json"),
    })
    return decoded, provenance


@lru_cache(maxsize=1)
def run_deepseek_extension() -> dict:
    manifest = json.loads((EXT / "manifest.json").read_text())
    frozen_results = json.loads((EXT / "results.json").read_text())
    spend = json.loads((EXT / "spend_ledger.json").read_text())
    assert manifest["protocol_version"] == "deepseek-v4-flash-extension-v2"
    assert tuple(row["id"] for row in manifest["cases"]) == CASE_IDS
    assert manifest["deepseek"]["baseline_samples"] == 10
    assert manifest["stability_gate"].startswith("baseline_core_success_count must equal 0 of 10")
    assert manifest["judges"] == [
        {"id": "fable", "cli": "claude", "model": "claude-fable-5"},
        {"id": "codex", "cli": "codex exec", "model": "gpt-5.4", "note": "Actual canonical model printed by Codex v0.144.0; the original requested label gpt-5.4-codex resolves here to gpt-5.4."},
    ]
    assert manifest["evaluator_reconciliation_disclosure"]["verdict_sensitivity"].startswith("No case verdict changes")

    sessions = set()
    rows = []
    for declared in manifest["cases"]:
        case_id = declared["id"]
        prompts = {}
        for arm in ("baseline", "surface", "structural", "frontier"):
            copied = EXT / case_id / "prompts" / f"{arm}.txt"
            source = ROOT / declared["source_prompt_paths"][arm]
            assert copied.read_bytes() == source.read_bytes()
            assert _sha(copied) == declared["prompt_sha256"][arm]
            assert _sha(copied) == (copied.with_suffix(".sha256")).read_text().strip()
            prompts[arm] = copied.read_bytes()
        assert prompts["baseline"] == prompts["frontier"]

        baseline_runs = [_verify_run(case_id, "baseline", i, prompts["baseline"]) for i in range(1, 11)]
        surface = _verify_run(case_id, "surface", 1, prompts["surface"])
        structural = _verify_run(case_id, "structural", 1, prompts["structural"])
        for run in (*baseline_runs, surface, structural):
            assert run["session_id"] not in sessions
            sessions.add(run["session_id"])

        fable, fable_hashes = _verify_judge(case_id, "fable")
        codex, codex_hashes = _verify_judge(case_id, "codex")
        vectors = {
            "mechanical": [run["mechanical_core_success"] for run in baseline_runs],
            "fable": [fable[f"baseline_{i:02d}"]["load_bearing_success"] for i in range(1, 11)],
            "codex": [codex[f"baseline_{i:02d}"]["load_bearing_success"] for i in range(1, 11)],
        }
        conservative = [any(vectors[name][i] for name in vectors) for i in range(10)]
        successes = sum(conservative)
        stable = successes == 0
        surface_success = surface["mechanical_core_success"] or fable["surface"]["load_bearing_success"] or codex["surface"]["load_bearing_success"]
        structural_success = structural["mechanical_core_success"] and fable["structural"]["load_bearing_success"] and codex["structural"]["load_bearing_success"]
        frontier_success = fable["frontier"]["load_bearing_success"] and codex["frontier"]["load_bearing_success"]
        hold = stable and not surface_success and structural_success and frontier_success
        ci, one_sided_upper = _clopper_pearson(successes, 10)
        row = {
            "id": case_id,
            "deepseek_baseline_success_vectors": vectors,
            "deepseek_conservative_any_evaluator_success_vector": conservative,
            "deepseek_baseline_successes": successes,
            "deepseek_baseline_failures": 10-successes,
            "baseline_success_two_sided_95_exact_ci": ci,
            "baseline_success_one_sided_95_upper": one_sided_upper,
            "stable_baseline_failure_gate": stable,
            "deepseek_surface_load_bearing_success": surface_success,
            "deepseek_structural_load_bearing_success": structural_success,
            "frontier_load_bearing_success": frontier_success,
            "deepseek_triple_holds": hold,
            "deepseek_structural_cost_usd": structural["cost_usd"],
            "response_shas_unique_across_baselines": len({run["response_sha256"] for run in baseline_runs}),
            "blind_primary_scores": {judge: {arm: decoded[arm]["score"] for arm in ("baseline_01", "surface", "structural", "frontier")} for judge, decoded in (("fable", fable), ("codex", codex))},
            "judge_hashes": {"fable": fable_hashes, "codex": codex_hashes},
            "runs": {"baseline": baseline_runs, "surface": surface, "structural": structural},
        }
        frozen = next(item for item in frozen_results["cases"] if item["id"] == case_id)
        for key in ("deepseek_baseline_success_vectors", "deepseek_conservative_any_evaluator_success_vector", "deepseek_baseline_successes", "deepseek_baseline_failures", "stable_baseline_failure_gate", "deepseek_surface_load_bearing_success", "deepseek_structural_load_bearing_success", "frontier_load_bearing_success", "deepseek_triple_holds", "blind_primary_scores"):
            assert row[key] == frozen[key]
        row["haiku_triple_holds"] = frozen["haiku_triple_holds"]
        row["haiku_frontier_ratio"] = frozen["haiku_frontier_ratio"]
        row["deepseek_frontier_ratio_if_hold"] = frozen["deepseek_frontier_ratio_if_hold"]
        rows.append(row)

    reported_valid = sum(run["cost_usd"] for row in rows for arm in ("baseline", "surface", "structural") for run in (row["runs"][arm] if arm == "baseline" else [row["runs"][arm]]))
    assert abs(reported_valid - spend["valid_reported_cost_usd"]) < 1e-12
    total_reported = reported_valid + spend["excluded_invalid_stdin_reported_cost_usd"]
    assert abs(total_reported - spend["total_cli_reported_metered_cost_usd"]) < 1e-12
    assert total_reported < spend["cap"] == 3.0
    assert sum(row["deepseek_triple_holds"] for row in rows) == 1

    # Existing matcher choices remain frozen. No new graph or matcher was introduced.
    for case_id in ("defect_chain_ladder", "robot_engset"):
        case_dir = HERE / "multi_case" / case_id
        target = load_situation_file(case_dir / "target_graph.json")
        sources = load_situation_library_file(case_dir / "source_graphs.json")
        regimes = compare_retrieval_regimes(target, sources, shortlist_size=3)
        assert regimes.surface_source_id == f"{case_id}_surface_source"
        assert regimes.structural_source_id == f"{case_id}_structural_source"

    return {
        "verdict": "one-of-three-haiku-triples-transfers-to-deepseek-v4-flash",
        "claim": "Only defect-chain-ladder passed the strict DeepSeek screen: 0/10 baseline core hits, surface miss, structural hit, and frontier hit.",
        "scope_limit": frozen_results["scope_limit"],
        "aggregate": {"deepseek_triples_holding": 1, "existing_haiku_triples_tested": 3},
        "metered_spend": {
            "valid_reported_cost_usd": reported_valid,
            "excluded_invalid_stdin_reported_cost_usd": spend["excluded_invalid_stdin_reported_cost_usd"],
            "total_cli_reported_metered_cost_usd": total_reported,
            "cap_usd": spend["cap"],
            "incomplete_invalid_calls_without_final_usage": spend["incomplete_invalid_calls_without_final_usage"],
            "caveat": spend["unattributed_cost_caveat"],
        },
        "prompt_equivalence_limit": "Frozen user-message bytes match exactly. Prime Agent's system prompt and provider stack differ from Claude CLI, so full-request equivalence is not claimed.",
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=EXT / "receipt.json")
    args = parser.parse_args()
    receipt = run_deepseek_extension()
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print("verdict:", receipt["verdict"])
    print("reported metered USD:", receipt["metered_spend"]["total_cli_reported_metered_cost_usd"])
    for row in receipt["rows"]:
        print(row["id"], "baseline successes", row["deepseek_baseline_successes"], "/10", "surface", row["deepseek_surface_load_bearing_success"], "structural", row["deepseek_structural_load_bearing_success"], "holds", row["deepseek_triple_holds"], "ratio", row["deepseek_frontier_ratio_if_hold"])


if __name__ == "__main__":
    main()
