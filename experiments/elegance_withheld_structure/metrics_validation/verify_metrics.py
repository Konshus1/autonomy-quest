#!/usr/bin/env python3
from __future__ import annotations
import importlib.util,json,subprocess,sys
from pathlib import Path
BASE=Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).resolve().parent
PILOT=Path(__file__).resolve().parents[1]/"pilot_c05"
spec=importlib.util.spec_from_file_location("metrics_impl",BASE/"metrics.py"); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
errors=[]; measured={}
for label in ("simple","complex"):
 p=BASE/f"known_{label}.py"; measured[label]=mod.measure(p)
 cp=subprocess.run([sys.executable,str(PILOT/"test_solution.py"),str(p)],capture_output=True,text=True)
 if cp.returncode: errors.append(f"known_{label} fails shared behavior tests")
try: stored=json.loads((BASE/"result.json").read_text())
except Exception as exc: stored={}; errors.append(f"stored result unreadable: {exc}")
if stored!=measured: errors.append("stored metrics do not match recomputation")
for key in ("lines","cyclomatic","new_concepts"):
 if measured["simple"][key] >= measured["complex"][key]: errors.append(f"{key} does not rank known-simple below known-complex")
if measured["simple"]["dependencies"] or measured["complex"]["dependencies"]: errors.append("stdlib-only fixtures reported external dependencies")
if errors:
 print("FAIL: M3 metric sanity check")
 for e in errors: print(f"- {e}")
 raise SystemExit(1)
print("PASS: M3 metric sanity check")
print("simple="+json.dumps(measured["simple"],sort_keys=True))
print("complex="+json.dumps(measured["complex"],sort_keys=True))
