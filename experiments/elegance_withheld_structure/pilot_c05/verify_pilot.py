#!/usr/bin/env python3
from __future__ import annotations
import json, subprocess, sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
RESULTS=Path(sys.argv[1]) if len(sys.argv)>1 else HERE/"results"
errors=[]
try: manifest=json.loads((RESULTS/"manifest.json").read_text())
except Exception as exc: manifest={}; errors.append(f"manifest unreadable: {exc}")
if manifest.get("engine")!="codex subscription": errors.append("engine is not codex subscription")
arms=manifest.get("arms",[]); names=[a.get("arm") for a in arms]
if names != ["direct","semantic","structural","human"]: errors.append(f"mandatory arms/order missing: {names}")
try: contexts=json.loads((RESULTS/"contexts.json").read_text())
except Exception as exc: contexts={}; errors.append(f"contexts unreadable: {exc}")
st=contexts.get("structural",{})
if st.get("source_domain") in {None,"Customer communications"}: errors.append("structural source is not cross-domain")
if len(st.get("role_correspondences",{}))<4: errors.append("structural explicit correspondence map incomplete")
if len(contexts.get("human",{}).get("role_correspondences",{}))<4: errors.append("human explicit correspondence map incomplete")
for arm in names:
    d=RESULTS/arm
    for f in ("prompt.txt","response.json","solution.py","normalization.json","test_stdout.txt","test_stderr.txt"):
        if not (d/f).is_file(): errors.append(f"{arm}: missing {f}")
    if (d/"solution.py").is_file():
        cp=subprocess.run([sys.executable,str(HERE/"test_solution.py"),str(d/"solution.py")],capture_output=True,text=True)
        if cp.returncode: errors.append(f"{arm}: correctness failed: {(cp.stdout+cp.stderr).splitlines()[-1] if cp.stdout+cp.stderr else 'no output'}")
if errors:
    print("FAIL: M2 four-arm pilot incomplete")
    for e in errors: print(f"- {e}")
    raise SystemExit(1)
print("PASS: M2 four-arm pilot complete")
print("problem=C05 arms=direct,semantic,structural,human correctness=4/4")
print(f"structural_source={st['source_id']} domain={st['source_domain']} map_roles={len(st['role_correspondences'])}")
