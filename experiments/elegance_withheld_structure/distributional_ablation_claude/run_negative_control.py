#!/usr/bin/env python3
"""Deliberately break one formerly passing Claude solution and require verifier rejection."""
from __future__ import annotations
import hashlib,json,shutil,subprocess,sys,tempfile
from pathlib import Path
HERE=Path(__file__).resolve().parent
RECEIPT=HERE/"negative_control_receipt.txt"

def sha(path:Path)->str: return hashlib.sha256(path.read_bytes()).hexdigest()

def main()->int:
    manifest=json.loads((HERE/"results"/"manifest.json").read_text())
    passing=next((r for r in manifest["records"] if r.get("generation_rc")==0 and r.get("test_rc")==0),None)
    if not passing:
        print("ERROR: no formerly passing solution is available for the detector positive control",file=sys.stderr); return 5
    target_rel=Path("results")/passing["task"]/passing["condition"]/f"sample{passing['sample']:02d}"/"solution.py"
    original=HERE/target_rel; before=sha(original)
    with tempfile.TemporaryDirectory(prefix="elegance-distribution-claude-negative-") as td:
        root=Path(td)/"elegance_withheld_structure"; study=root/"distributional_ablation_claude"; root.mkdir(parents=True)
        shutil.copytree(HERE,study)
        (root/"causal_controls").symlink_to(HERE.parent/"causal_controls",target_is_directory=True)
        (root/"full_run").symlink_to(HERE.parent/"full_run",target_is_directory=True)
        target=study/target_rel
        target.write_text(target.read_text(encoding="utf-8")+"\nraise RuntimeError('NEGATIVE CONTROL: deliberately broken solution')\n",encoding="utf-8")
        test=root/"full_run"/"tests"/f"test_{passing['task'].lower()}.py"
        test_cp=subprocess.run([sys.executable,str(test),str(target)],text=True,capture_output=True)
        verifier=study/"verify_distribution.py"
        cp=subprocess.run([sys.executable,str(verifier),"--study",str(study)],text=True,capture_output=True)
        receipt=("NEGATIVE CONTROL: deliberately broken formerly passing Claude solution\n"
                 f"target={target_rel.as_posix()}\n"
                 f"original_recorded_test_rc={passing['test_rc']}\n"
                 f"original_sha256_before={before}\n"
                 f"mutated_sha256={sha(target)}\n"
                 f"mutated_test_rc={test_cp.returncode}\n"
                 f"verifier_rc={cp.returncode}\n"
                 "--- verifier stdout ---\n"+cp.stdout+"--- verifier stderr ---\n"+cp.stderr)
    after=sha(original); receipt+=f"original_sha256_after={after}\n"; RECEIPT.write_text(receipt,encoding="utf-8"); print(receipt,end="")
    if before!=after: print("ERROR: negative control mutated real artifact",file=sys.stderr); return 2
    if test_cp.returncode==0: print("ERROR: deliberate break did not fail frozen test",file=sys.stderr); return 3
    if cp.returncode==0 or "FAIL: distributional ablation completion gate" not in cp.stdout:
        print("ERROR: completion verifier accepted the broken fixture",file=sys.stderr); return 4
    print("negative_control_result=PASS (broken fixture rejected)"); return 0
if __name__=="__main__": raise SystemExit(main())
