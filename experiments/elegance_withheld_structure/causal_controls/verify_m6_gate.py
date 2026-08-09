#!/usr/bin/env python3
import json
from pathlib import Path
a=json.loads((Path(__file__).resolve().parent/"results"/"analysis.json").read_text())
errors=[]
if not a["repeatability_gate"]: errors.append("same-seed repeatability failed: 0/9 identical response pairs")
if a["irrelevant_worked_control"].startswith("indeterminate"): errors.append("correct worked irrelevant-analogue control is indeterminate")
if errors:
 print("FAIL: M6 causal completion gate")
 for e in errors: print("- "+e)
 raise SystemExit(1)
print("PASS: M6 causal completion gate")
