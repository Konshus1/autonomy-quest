#!/usr/bin/env python3
from __future__ import annotations
import importlib.util,json,statistics,subprocess,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
RESULTS=Path(sys.argv[1]) if len(sys.argv)>1 else HERE/"results"
METRICS=HERE.parent/"metrics_validation"/"metrics.py"
sp=importlib.util.spec_from_file_location("elegance_metrics",METRICS); mm=importlib.util.module_from_spec(sp); sp.loader.exec_module(mm)
spec=json.loads((HERE/"control_spec.json").read_text()); thresholds=spec["pre_registration"]["thresholds"]; errors=[]; pairs=[]
try: manifest=json.loads((RESULTS/"manifest.json").read_text())
except Exception as exc: manifest={}; errors.append(f"manifest unreadable: {exc}")
records={(x.get("task"),x.get("arm")):x for x in manifest.get("records",[])}
for task in spec["tasks"]:
    tid=task["id"]; measured={}
    for arm in ("direct","structural"):
        d=RESULTS/tid/arm; sol=d/"solution.py"; rec=records.get((tid,arm))
        if not rec: errors.append(f"{tid}/{arm}: missing manifest record"); continue
        if rec.get("generation_rc")!=0: errors.append(f"{tid}/{arm}: generation failed")
        cp=subprocess.run([sys.executable,str(HERE/f"test_{task['test']}.py"),str(sol)],capture_output=True,text=True)
        if cp.returncode: errors.append(f"{tid}/{arm}: correctness failed")
        else: measured[arm]=mm.measure(sol)
        prompt=(d/"prompt.txt").read_text() if (d/"prompt.txt").is_file() else ""
        if "Organize the implementation explicitly as Decorator" not in prompt: errors.append(f"{tid}/{arm}: structure not stated")
        if arm=="direct" and "MACHINE-RETRIEVED" in prompt: errors.append(f"{tid}/direct: analogy contamination")
        if arm=="structural" and "MACHINE-RETRIEVED CROSS-DOMAIN" not in prompt: errors.append(f"{tid}/structural: analogy absent")
    if set(measured)=={"direct","structural"}:
        a=measured["direct"]; b=measured["structural"]
        pairs.append({"task":tid,"direct":a,"structural":b,"abs_loc_ratio":abs(b["lines"]-a["lines"])/a["lines"],"abs_cyclomatic":abs(b["cyclomatic"]-a["cyclomatic"]),"abs_new_concepts":abs(b["new_concepts"]-a["new_concepts"]),"dependencies_match":a["dependencies"]==b["dependencies"]})
if len(pairs)==3:
    aggregate={"median_abs_loc_ratio":statistics.median(x["abs_loc_ratio"] for x in pairs),"median_abs_cyclomatic":statistics.median(x["abs_cyclomatic"] for x in pairs),"median_abs_new_concepts":statistics.median(x["abs_new_concepts"] for x in pairs),"dependencies_all_match":all(x["dependencies_match"] for x in pairs)}
    try: stored=json.loads((RESULTS/"score.json").read_text())
    except Exception as exc: stored={}; errors.append(f"score unreadable: {exc}")
    if stored!={"pairs":pairs,"aggregate":aggregate}: errors.append("stored scores do not match recomputation")
    for key in ("median_abs_loc_ratio","median_abs_cyclomatic","median_abs_new_concepts"):
        if aggregate[key]>thresholds[key]: errors.append(f"collapse threshold exceeded: {key}={aggregate[key]:.6g} > {thresholds[key]}")
    if not aggregate["dependencies_all_match"]: errors.append("dependency lists differ within a pair")
else: aggregate={}; errors.append(f"only {len(pairs)} complete pairs")
if errors:
    print("FAIL: M4 structure-stated collapse gate")
    for e in errors: print(f"- {e}")
    raise SystemExit(1)
print("PASS: M4 structure-stated collapse gate")
print("correctness=6/6 pairs=3")
print("aggregate="+json.dumps(aggregate,sort_keys=True))
