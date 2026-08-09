"""Falsifiable completion verifier for invariance_results.json."""
from __future__ import annotations
import argparse,json
from pathlib import Path
import experiment as core
import invariance_experiment as inv
HERE=Path(__file__).resolve().parent
ARMS={"direct","semantic","structural"}

def verify(data:dict)->list[str]:
    errors=[]; corpus=inv.load_corpus(HERE/"invariance_cases.json")
    if data.get("corpus_sha256")!=core.sha256_json(corpus):errors.append("corpus hash mismatch")
    if data.get("model")!=data.get("judge_model"):errors.append("model/judge mismatch")
    records=data.get("records",[]); rules={r["rule_id"]:r for r in corpus["rules"]}; case_ids={c["case_id"] for c in corpus["cases"]}
    if len(records)!=len(rules):errors.append("record count mismatch")
    for rec in records:
        rid=rec.get("rule_id");where=f"rule/{rid}";gold=rules.get(rid)
        if not gold:errors.append(f"{where}: unknown rule");continue
        for key in ("gold_class","gold_support_count"):
            if rec.get(key)!=gold[key]:errors.append(f"{where}: {key} mismatch")
        if rec.get("gold_applicable_case_ids")!=gold["applies_to"]:errors.append(f"{where}: gold applicability mismatch")
        if set(rec.get("predictions",{}))!=ARMS:errors.append(f"{where}: missing arm")
        for arm in ARMS:
            p=rec.get("predictions",{}).get(arm,{})
            if p.get("classification") not in inv.CLASSES:errors.append(f"{where}/{arm}: invalid class")
            if not isinstance(p.get("predicted_support_count"),int) or not 0<=p["predicted_support_count"]<=14:errors.append(f"{where}/{arm}: invalid count")
        s=rec.get("predictions",{}).get("structural",{});rows=s.get("per_case",[])
        if len(rows)!=14 or {str(x.get('case_id')) for x in rows}!=case_ids:errors.append(f"{where}: structural per_case shape invalid")
        elif sum(bool(x.get("applies")) for x in rows)!=s.get("predicted_support_count"):errors.append(f"{where}: structural per_case/count mismatch")
        judges=rec.get("judgments",[])
        if len(judges)!=3:errors.append(f"{where}: expected three judges")
        for j in judges:
            if set(j.get("scores",{}))!=ARMS:errors.append(f"{where}: judge missing arm")
    if len(records)==len(rules):
        recomputed=inv.summarize(records)
        if data.get("summary")!=recomputed:errors.append("summary does not recompute from raw records")
    return errors

def main():
    ap=argparse.ArgumentParser();ap.add_argument("result");args=ap.parse_args();d=json.loads(Path(args.result).read_text());errors=verify(d)
    if errors:
        print("FAIL: invariance evidence invalid")
        for e in errors:print("- "+e)
        raise SystemExit(1)
    s=d["summary"]["arms"]
    print("PASS: invariance comparison is internally complete")
    for a in ("direct","semantic","structural"):
        print(f"{a}: class_accuracy={s[a]['class_accuracy']:.6f} support_mae={s[a]['support_count_mae']:.6f} invariant_balanced_accuracy={s[a]['invariant_balanced_accuracy']:.6f}")
if __name__=="__main__":main()
