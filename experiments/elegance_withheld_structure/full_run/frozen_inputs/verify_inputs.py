#!/usr/bin/env python3
from __future__ import annotations
import ast,hashlib,json,subprocess,sys,tempfile
from pathlib import Path
HERE=Path(__file__).resolve().parent; RUN=HERE.parent; EXP=HERE.parents[1]
errors=[]
try: tasks=json.loads((HERE/"task_contracts.json").read_text()); audit=json.loads((HERE/"contract_audit_final.json").read_text()); sem=json.loads((HERE/"semantic_contexts.json").read_text()); st=json.loads((HERE/"structural_contexts.json").read_text()); hm=json.loads((HERE/"human_maps.json").read_text())
except Exception as exc: print(f"FAIL: M5 frozen inputs unreadable: {exc}"); raise SystemExit(1)
ids=[x["id"] for x in tasks]; verdicts={x["id"]:x["verdict"] for x in audit}
if len(ids)!=15 or len(set(ids))!=15: errors.append("task count/identity is not 15 unique")
if set(verdicts)!=set(ids) or any(verdicts.get(x)!="include" for x in ids): errors.append("not every appended contract has final independent include verdict")
corpus=json.loads((EXP/"corpus_candidates.json").read_text()); original={x["id"]:x for x in corpus["candidates"] if x.get("status")=="included"}
for task in tasks:
 cid=task["id"]; contract=task["contract"]
 if not contract.startswith(original[cid]["requirement"]): errors.append(f"{cid}: exact frozen requirement prefix changed")
 try: ast.parse((RUN/"tests"/f"test_{cid.lower()}.py").read_text())
 except Exception as exc: errors.append(f"{cid}: test syntax: {exc}")
 if cid not in sem or cid not in st or cid not in hm: errors.append(f"{cid}: arm context missing")
 elif len(st[cid].get("role_correspondences",[]))<4: errors.append(f"{cid}: structural map too short")
 if len(task.get("public_symbols",[]))<1: errors.append(f"{cid}: public symbols absent")
with tempfile.TemporaryDirectory() as td:
 broken=Path(td)/"solution.py"; broken.write_text('raise RuntimeError("deliberately broken")\n')
 rejected=[]
 for cid in ids:
  cp=subprocess.run([sys.executable,str(RUN/"tests"/f"test_{cid.lower()}.py"),str(broken)],capture_output=True,text=True); rejected.append(cp.returncode!=0)
 if not all(rejected): errors.append("at least one test accepted deliberately broken implementation")
if (RUN/"results").exists(): errors.append("results already exist; inputs were not checked pre-generation")
if errors:
 print("FAIL: M5 frozen-input gate")
 for e in errors: print(f"- {e}")
 raise SystemExit(1)
print("PASS: M5 frozen-input gate")
print("problems=15 tests=15 broken_rejected=15 semantic_contexts=15 structural_contexts=15 human_maps=15 independent_contract_includes=15")
for name in ("task_contracts.json","contract_audit_final.json","semantic_contexts.json","structural_contexts.json","human_maps.json"):
 print(name+"_sha256="+hashlib.sha256((HERE/name).read_bytes()).hexdigest())
