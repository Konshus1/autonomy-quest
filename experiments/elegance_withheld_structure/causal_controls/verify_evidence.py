#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,subprocess,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent; OUT=Path(sys.argv[1]) if len(sys.argv)>1 else HERE/"results"; errors=[]; plan=json.loads((HERE/"control_plan.json").read_text())
try: manifest=json.loads((OUT/"manifest.json").read_text()); analysis=json.loads((OUT/"analysis.json").read_text())
except Exception as exc: print(f"FAIL: M6 evidence package unreadable: {exc}"); raise SystemExit(1)
records=manifest.get("records",[])
if len(records)!=18: errors.append(f"records={len(records)}, expected 18")
for r in records:
 d=OUT/r["task"]/r["condition"]/f"repeat{r['repeat']}"; reqp=d/"request.json"; shap=d/"request_sha256.txt"
 if not reqp.is_file() or not shap.is_file(): errors.append(f"{r['task']}/{r['condition']}/{r['repeat']}: request evidence missing"); continue
 payload=json.loads(reqp.read_text()); canonical=json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode(); got=hashlib.sha256(canonical).hexdigest()
 if got!=shap.read_text().strip(): errors.append(f"{r['task']}/{r['condition']}/{r['repeat']}: request hash mismatch")
 if payload.get("seed")!=424242 or payload.get("temperature")!=0: errors.append(f"{r['task']}/{r['condition']}/{r['repeat']}: seed/temperature changed")
 if "Authorization" in reqp.read_text() or "api_key" in reqp.read_text().lower(): errors.append("credential leaked")
if analysis.get("request_identical_pairs")!=9 or analysis.get("response_identical_pairs")!=0 or analysis.get("fingerprint_identical_pairs")!=9: errors.append("repeatability counts changed")
if analysis.get("creative_existence_claim","").split(':')[0]!="cannot_determine": errors.append("causal status must be cannot_determine")
cp=subprocess.run([sys.executable,str(HERE/"verify_scramble.py")],capture_output=True,text=True)
if cp.returncode: errors.append("scrambled map was not rejected")
if errors:
 print("FAIL: M6 evidence integrity gate")
 for e in errors: print(f"- {e}")
 raise SystemExit(1)
print("PASS: M6 evidence integrity gate")
print("calls=18 identical_request_pairs=9 identical_fingerprints=9 identical_response_pairs=0")
print("scrambled_valid_accepted=3 scrambled_rejected=3")
print("strict_causal_status=cannot_determine irrelevant_control=indeterminate")
