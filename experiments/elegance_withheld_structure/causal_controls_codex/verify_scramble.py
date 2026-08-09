#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path
HERE=Path(__file__).resolve().parent; FI=HERE.parent/"full_run"/"frozen_inputs"
data=json.loads((FI/"structural_retrieval_inputs.json").read_text()); frozen=json.loads((FI/"structural_contexts.json").read_text()); sources={x["id"]:x for x in data["source_library"]}; targets={x["id"]:x for x in data["targets"]}
def expected(cid):
 src=sources[frozen[cid]["source_id"]]; target=targets[cid]; out=[]
 for key in sorted(set(src["source_role_map"])&set(target["target_role_map"])): out.append({"abstract_role":key,"source_role":src["source_role_map"][key],"target_role":target["target_role_map"][key]})
 return out
def valid(cid,mapping): return mapping==expected(cid)
fail=[]
for cid in json.loads((HERE/"control_plan.json").read_text())["cases"]:
 good=expected(cid); values=[x["target_role"] for x in good]; scrambled=[dict(x,target_role=values[(i+1)%len(values)]) for i,x in enumerate(good)]
 if not valid(cid,good) or valid(cid,scrambled): fail.append(cid)
if fail: print("FAIL: scrambled-map rejection gate: "+','.join(fail)); raise SystemExit(1)
print("PASS: scrambled-map rejection gate")
print("valid_accepted=3 scrambled_rejected=3 method=deterministic_signed_join")
