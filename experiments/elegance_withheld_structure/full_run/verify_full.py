#!/usr/bin/env python3
from __future__ import annotations
import hashlib,importlib.util,json,subprocess,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent; OUT=Path(sys.argv[1]) if len(sys.argv)>1 else HERE/"results"; ARMS=("direct","semantic","structural","human")
errors=[]; tasks=json.loads((HERE/"frozen_inputs"/"task_contracts.json").read_text()); ids=[x["id"] for x in tasks]; public={x["id"]:set(x["public_symbols"]) for x in tasks}
try: manifest=json.loads((OUT/"manifest.json").read_text())
except Exception as exc: manifest={}; errors.append(f"manifest unreadable: {exc}")
records=manifest.get("records",[]); keys=[(x.get("task"),x.get("arm")) for x in records]
expected=[(cid,arm) for cid in ids for arm in ARMS]
if keys!=expected: errors.append(f"cell identities/order wrong: got {len(keys)}, expected 60")
actual_correct={};
for cid,arm in expected:
 d=OUT/cid/arm; rec=next((x for x in records if (x.get("task"),x.get("arm"))==(cid,arm)),None)
 if rec is None: continue
 for f in ("prompt.txt","response.json","plan.json","solution.py","test_stdout.txt","test_stderr.txt"):
  if not (d/f).is_file(): errors.append(f"{cid}/{arm}: missing {f}")
 if (d/"solution.py").is_file():
  cp=subprocess.run([sys.executable,str(HERE/"tests"/f"test_{cid.lower()}.py"),str(d/"solution.py")],capture_output=True,text=True); actual_correct[(cid,arm)]=cp.returncode==0
  if rec.get("test_rc")!=cp.returncode: errors.append(f"{cid}/{arm}: manifest test rc {rec.get('test_rc')} != recomputed {cp.returncode}")
 prompt=(d/"prompt.txt").read_text() if (d/"prompt.txt").is_file() else ""
 marks={"semantic":"NEAREST-NEIGHBOR CODE RETRIEVAL","structural":"MACHINE-RETRIEVED CROSS-DOMAIN","human":"HUMAN-SUPPLIED CROSS-DOMAIN"}
 if arm=="direct" and any(x in prompt for x in marks.values()): errors.append(f"{cid}/direct: context contamination")
 if arm in marks and marks[arm] not in prompt: errors.append(f"{cid}/{arm}: arm context marker absent")
# Mechanical scores must match independent recomputation.
sp=importlib.util.spec_from_file_location("score_impl",HERE/"score_outputs.py"); sm=importlib.util.module_from_spec(sp); sp.loader.exec_module(sm)
try: score_rows=json.loads((OUT/"mechanical_scores.json").read_text()); score_map={(x["task"],x["arm"]):x for x in score_rows}
except Exception as exc: score_map={}; errors.append(f"mechanical score unreadable: {exc}")
for key,ok in actual_correct.items():
 row=score_map.get(key)
 if not row or row.get("correct")!=ok: errors.append(f"{key}: score correctness mismatch"); continue
 if ok:
  got=sm.measure(OUT/key[0]/key[1]/"solution.py",public[key[0]])
  if row.get("metrics")!=got: errors.append(f"{key}: mechanical score mismatch")
# Blinded judgments for every problem with >=2 correct outputs; deterministic rotated labels.
eligible=[]
for cid in ids:
 correct=[a for a in ARMS if actual_correct.get((cid,a))]
 if len(correct)<2: continue
 eligible.append(cid); d=OUT/"judgments"/cid
 try: lm=json.loads((d/"label_map.json").read_text()); dec=json.loads((d/"decoded.json").read_text()); raw=json.loads((d/"response.json").read_text())
 except Exception as exc: errors.append(f"{cid}: judgment unreadable: {exc}"); continue
 ordered=sorted(ARMS,key=lambda a:hashlib.sha256(f"{cid}:{a}".encode()).hexdigest()); exp={a:chr(65+i) for i,a in enumerate(ordered)}
 if lm!=exp: errors.append(f"{cid}: label rotation mismatch")
 if set(dec.get("ranking",[]))!=set(correct) or len(dec.get("ranking",[]))!=len(correct): errors.append(f"{cid}: decoded ranking not exactly correct arms")
try: analysis=json.loads((OUT/"analysis.json").read_text())
except Exception as exc: analysis={}; errors.append(f"analysis unreadable: {exc}")
if analysis.get("cells")!=60 or analysis.get("primary_outcome") not in {"null","retrieval_mapping_bottleneck","at_least_one_analogy_arm_beats_direct"}: errors.append("analysis result missing/invalid")
if errors:
 print("FAIL: M5 full-run completion gate")
 for e in errors: print(f"- {e}")
 raise SystemExit(1)
print("PASS: M5 full-run completion gate")
print(f"cells=60 generations=60 correct={sum(actual_correct.values())} judged_problems={len(eligible)} unjudgeable={15-len(eligible)}")
print("correctness_by_arm="+json.dumps({a:sum(actual_correct.get((cid,a),False) for cid in ids) for a in ARMS},sort_keys=True))
print("primary_outcome="+analysis["primary_outcome"])
