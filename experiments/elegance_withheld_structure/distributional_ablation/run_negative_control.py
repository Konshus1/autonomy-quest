#!/usr/bin/env python3
"""Deliberately break one formerly passing solution and require verifier rejection."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = Path("results/B13/default/sample01/solution.py")
RECEIPT = HERE / "negative_control_receipt.txt"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    original = HERE / TARGET
    before = sha(original)
    with tempfile.TemporaryDirectory(prefix="elegance-distribution-negative-") as td:
        root = Path(td) / "elegance_withheld_structure"
        study = root / "distributional_ablation"
        root.mkdir(parents=True)
        shutil.copytree(HERE, study)
        # Frozen siblings are not mutated, so link them read-only into the isolated fixture.
        (root / "causal_controls").symlink_to(HERE.parent / "causal_controls", target_is_directory=True)
        (root / "full_run").symlink_to(HERE.parent / "full_run", target_is_directory=True)
        target = study / TARGET
        target.write_text(target.read_text(encoding="utf-8") + "\nraise RuntimeError('NEGATIVE CONTROL: deliberately broken solution')\n", encoding="utf-8")
        test = root / "full_run" / "tests" / "test_b13.py"
        test_cp = subprocess.run([sys.executable, str(test), str(target)], text=True, capture_output=True)
        verifier = study / "verify_distribution.py"
        cp = subprocess.run([sys.executable, str(verifier), "--study", str(study)], text=True, capture_output=True)
        receipt = (
            "NEGATIVE CONTROL: deliberately broken formerly passing solution\n"
            f"target={TARGET.as_posix()}\n"
            f"original_sha256_before={before}\n"
            f"mutated_sha256={sha(target)}\n"
            f"mutated_test_rc={test_cp.returncode}\n"
            f"verifier_rc={cp.returncode}\n"
            "--- verifier stdout ---\n"
            + cp.stdout
            + "--- verifier stderr ---\n"
            + cp.stderr
        )
    after = sha(original)
    receipt += f"original_sha256_after={after}\n"
    RECEIPT.write_text(receipt, encoding="utf-8")
    print(receipt, end="")
    if before != after:
        print("ERROR: negative control mutated the real artifact", file=sys.stderr)
        return 2
    if test_cp.returncode == 0:
        print("ERROR: deliberate break did not fail frozen test", file=sys.stderr)
        return 3
    if cp.returncode == 0 or "FAIL: distributional ablation completion gate" not in cp.stdout:
        print("ERROR: completion verifier accepted the broken fixture", file=sys.stderr)
        return 4
    print("negative_control_result=PASS (broken fixture rejected)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
