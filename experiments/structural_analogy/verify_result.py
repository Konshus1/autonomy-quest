"""Falsifiable completion verifier for the frozen Track-C result artifact."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from typing import Any

ARMS = {"direct", "semantic", "structural"}


def verify_result(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if data.get("conclusion") not in {"positive", "measured_null_or_negative"}:
        errors.append("conclusion is missing, invalid, or voided by watchdog gain")
    for corpus_name in ("cross_domain", "watchdog_control"):
        corpus = data.get(corpus_name)
        if not isinstance(corpus, dict):
            errors.append(f"missing {corpus_name}")
            continue
        if corpus.get("model") != corpus.get("judge_model"):
            errors.append(f"{corpus_name}: generator/judge model mismatch")
        records = corpus.get("records")
        if not isinstance(records, list) or not records:
            errors.append(f"{corpus_name}: no records")
            continue
        ids: set[str] = set()
        for index, rec in enumerate(records):
            where = f"{corpus_name}.records[{index}]"
            pid = rec.get("problem_id")
            if not pid or pid in ids:
                errors.append(f"{where}: absent or duplicate problem_id")
            ids.add(pid)
            if set(rec.get("candidates", {})) != ARMS:
                errors.append(f"{where}: candidates do not contain exactly all three arms")
            for arm in ARMS:
                text = rec.get("candidates", {}).get(arm, {}).get("candidate_text")
                if not isinstance(text, str) or not text.strip():
                    errors.append(f"{where}: {arm} has no candidate_text")
            judgments = rec.get("judgments")
            if not isinstance(judgments, list) or not judgments:
                errors.append(f"{where}: no LLM judgments")
            else:
                for j, judgment in enumerate(judgments):
                    if set(judgment.get("scores", {})) != ARMS:
                        errors.append(f"{where}.judgments[{j}]: scores missing an arm")
                        continue
                    for arm in ARMS:
                        usefulness = judgment["scores"][arm].get("usefulness")
                        if not isinstance(usefulness, int) or not 1 <= usefulness <= 5:
                            errors.append(f"{where}.judgments[{j}]: invalid {arm} usefulness")
            retrieval = rec.get("retrieval", {})
            roles = retrieval.get("role_correspondences")
            relations = retrieval.get("relation_correspondences")
            inferences = retrieval.get("transferred_candidate_inferences")
            if not isinstance(roles, list) or len(roles) < 2:
                errors.append(f"{where}: fewer than two explicit role correspondences")
            if not isinstance(relations, list) or len(relations) < 2:
                errors.append(f"{where}: fewer than two explicit relation correspondences")
            if not isinstance(inferences, list) or len(inferences) < 1:
                errors.append(f"{where}: no transferred candidate inference")
            if corpus_name == "cross_domain" and retrieval.get("source_domain") == retrieval.get("target_domain"):
                errors.append(f"{where}: analogy is not cross-domain")
        summary = corpus.get("summary", {})
        if set(summary.get("arms", {})) != ARMS:
            errors.append(f"{corpus_name}: usefulness not reported for all arms")
        comparisons = summary.get("structural_minus_baseline", {})
        if set(comparisons) != {"direct", "semantic"}:
            errors.append(f"{corpus_name}: missing comparison against both baselines")
        else:
            for baseline, comp in comparisons.items():
                if not isinstance(comp.get("mean_delta"), (int, float)):
                    errors.append(f"{corpus_name}: no numeric delta vs {baseline}")
                ci = comp.get("bootstrap_95_ci")
                if not isinstance(ci, list) or len(ci) != 2:
                    errors.append(f"{corpus_name}: no CI vs {baseline}")
    watchdog_comps = data.get("watchdog_control", {}).get("summary", {}).get("structural_minus_baseline", {})
    for baseline, comp in watchdog_comps.items():
        if comp.get("significant_positive_gain"):
            errors.append(f"watchdog control shows significant analogy gain vs {baseline}; measurement is void")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("result")
    args = parser.parse_args()
    path = Path(args.result)
    errors = verify_result(json.loads(path.read_text()))
    if errors:
        print("FAIL: Track-C completion evidence is invalid")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)
    print("PASS: Track-C completion evidence is structurally complete and watchdog control has no significant gain")


if __name__ == "__main__":
    main()
