"""Completion verifier for the held-out failure-structure experiment."""
from __future__ import annotations
import argparse, json, math
from pathlib import Path
import failure_experiment as failure

HERE=Path(__file__).resolve().parent
ARMS={"direct","semantic","structural"}

def verify(data:dict)->list[str]:
    errors=[]
    if data.get("conclusion")!="measured": errors.append("experiment conclusion is not measured")
    corpora={"receipt_failure":failure.normalize_receipt_corpus(HERE/"receipt_failure_cases.json"),
             "watchdog_control":failure.normalize_watchdog_corpus(HERE/"watchdog_cases.json")}
    for key,corpus in corpora.items():
        result=data.get(key,{})
        if result.get("corpus_sha256")!=corpus["sha256"]: errors.append(f"{key}: corpus hash mismatch")
        if result.get("model")!=result.get("judge_model"): errors.append(f"{key}: generator/judge model mismatch")
        records=result.get("records",[])
        if len(records)!=len(corpus["targets"]): errors.append(f"{key}: record count mismatch")
        targets={x["id"]:x for x in corpus["targets"]}; sources={x["id"]:x for x in corpus["sources"]}
        for rec in records:
            where=f"{key}/{rec.get('problem_id')}"; target=targets.get(rec.get("problem_id"))
            if target is None: errors.append(f"{where}: unknown problem"); continue
            if set(rec.get("candidates",{}))!=ARMS: errors.append(f"{where}: missing arm")
            for arm in ARMS:
                text=rec.get("candidates",{}).get(arm,{}).get("candidate_text")
                if not isinstance(text,str) or not text.strip(): errors.append(f"{where}: empty {arm} candidate")
            structural=rec.get("candidates",{}).get("structural",{}); source=sources.get(structural.get("source_id"))
            if source is None: errors.append(f"{where}: unknown structural source")
            else:
                for error in failure.validate_structural_mapping(target,source,structural):
                    errors.append(f"{where}: {error}")
            if key == "receipt_failure" and not rec.get("mapping_audit",{}).get("result",{}).get("valid"):
                errors.append(f"{where}: mapping audit invalid")
            judgments=rec.get("judgments",[])
            if len(judgments)!=3: errors.append(f"{where}: expected three judgments")
            for j in judgments:
                if set(j.get("scores",{}))!=ARMS: errors.append(f"{where}: judgment missing arm")
                for arm in ARMS:
                    score=j.get("scores",{}).get(arm,{}).get("usefulness")
                    if not isinstance(score,int) or not 1<=score<=5: errors.append(f"{where}: invalid usefulness")
        summary=result.get("summary",{})
        if set(summary.get("arms",{}))!=ARMS: errors.append(f"{key}: all-arm usefulness absent")
        comps=summary.get("structural_minus_baseline",{})
        if set(comps)!={"direct","semantic"}: errors.append(f"{key}: both baseline deltas required")
        for baseline,comp in comps.items():
            if not isinstance(comp.get("mean_delta"),(int,float)) or not math.isfinite(comp["mean_delta"]):
                errors.append(f"{key}: nonnumeric delta vs {baseline}")
    metrics=data.get("receipt_failure",{}).get("retrieval_metrics",{})
    if metrics.get("structural_valid_defect_retrievals")!=metrics.get("n") or metrics.get("n")!=5:
        errors.append("held-out structural recognition is not 5/5")
    sham=data.get("scrambled_mapping_control",{})
    if not sham.get("passed_negative_control") or not sham.get("machine_errors") or sham.get("audit",{}).get("result",{}).get("valid") is not False:
        errors.append("scrambled mapping negative control did not fail closed")
    for baseline,comp in data.get("watchdog_control",{}).get("summary",{}).get("structural_minus_baseline",{}).items():
        if comp.get("significant_positive_gain"): errors.append(f"watchdog has significant gain vs {baseline}")
    return errors

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("result"); args=ap.parse_args()
    errors=verify(json.loads(Path(args.result).read_text()))
    if errors:
        print("FAIL: held-out Track-C evidence invalid")
        for e in errors: print("- "+e)
        raise SystemExit(1)
    d=json.loads(Path(args.result).read_text()); rc=d["receipt_failure"]["summary"]; wc=d["watchdog_control"]["summary"]
    print("PASS: held-out failure-structure measurement is complete")
    print(f"receipt structural-direct delta={rc['structural_minus_baseline']['direct']['mean_delta']}")
    print(f"receipt structural-semantic delta={rc['structural_minus_baseline']['semantic']['mean_delta']}")
    print(f"watchdog structural-direct delta={wc['structural_minus_baseline']['direct']['mean_delta']}")
    print(f"watchdog structural-semantic delta={wc['structural_minus_baseline']['semantic']['mean_delta']}")

if __name__=="__main__": main()
