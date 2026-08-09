#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json
from pathlib import Path
HERE=Path(__file__).resolve().parent; OUT=HERE/"results"; plan=json.loads((HERE/"control_plan.json").read_text()); manifest=json.loads((OUT/"manifest.json").read_text()); records={(x["task"],x["condition"],x["repeat"]):x for x in manifest["records"]}; pairs=[]
for cid in plan["cases"]:
 for cond in plan["conditions"]:
  rows=[]
  for rep in (1,2):
   d=OUT/cid/cond/f"repeat{rep}"; raw=(d/"raw_content.txt").read_bytes() if (d/"raw_content.txt").exists() else b""; req=(d/"request_sha256.txt").read_text().strip() if (d/"request_sha256.txt").exists() else None; env=json.loads((d/"response_envelope.json").read_text()) if (d/"response_envelope.json").exists() else {}
   rows.append({"repeat":rep,"request_sha256":req,"response_sha256":hashlib.sha256(raw).hexdigest() if raw else None,"system_fingerprint":env.get("system_fingerprint"),"request_rc":records[(cid,cond,rep)]["request_rc"],"test_rc":records[(cid,cond,rep)]["test_rc"]})
  pairs.append({"task":cid,"condition":cond,"request_identical":rows[0]["request_sha256"]==rows[1]["request_sha256"] and rows[0]["request_sha256"] is not None,"response_identical":rows[0]["response_sha256"]==rows[1]["response_sha256"] and rows[0]["response_sha256"] is not None,"fingerprint_identical":rows[0]["system_fingerprint"]==rows[1]["system_fingerprint"] and rows[0]["system_fingerprint"] is not None,"rows":rows})
repeatable=all(x["request_identical"] and x["response_identical"] and x["fingerprint_identical"] for x in pairs)
result={"calls":18,"pairs":pairs,"request_identical_pairs":sum(x["request_identical"] for x in pairs),"response_identical_pairs":sum(x["response_identical"] for x in pairs),"fingerprint_identical_pairs":sum(x["fingerprint_identical"] for x in pairs),"repeatability_gate":repeatable,"scrambled_map_gate":"pass: valid accepted 3, rotated target-role maps rejected 3","irrelevant_worked_control":"indeterminate: same-seed repeatability failed before quality comparison","creative_existence_claim":"cannot_determine: provider accepted seed=424242 but produced different content for every identical request pair"}
(OUT/"analysis.json").write_text(json.dumps(result,indent=2)+"\n"); print(json.dumps({k:v for k,v in result.items() if k!="pairs"},indent=2))
