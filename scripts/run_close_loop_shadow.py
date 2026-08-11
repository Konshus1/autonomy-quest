#!/usr/bin/env python3
"""Create one admitted demo task and print its end-to-end shadow receipt."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

from runner.close_loop.bridge import SourceSnapshot
from runner.close_loop.hashing import source_hashes
from runner.close_loop.runtime import AuthenticatedIdentity, TestCommand
from runner.close_loop.shadow import SubprocessHermeticEvaluator, run_shadow_once


GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "shadow-demo",
    "GIT_AUTHOR_EMAIL": "shadow-demo@aq.invalid",
    "GIT_COMMITTER_NAME": "shadow-demo",
    "GIT_COMMITTER_EMAIL": "shadow-demo@aq.invalid",
}


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, env=GIT_ENV,
        text=True, capture_output=True, check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="aq-hermetic-verifier:close-loop-shadow")
    parser.add_argument("--receipt-log", type=Path,
                        default=Path("/tmp/aq-close-loop-shadow/receipts.jsonl"))
    args = parser.parse_args()

    root = Path(tempfile.mkdtemp(prefix="aq-shadow-run-"))
    repo = root / "candidate-repo"
    remote = root / "public-remote.git"
    repo.mkdir(); remote.mkdir()
    git(repo, "init", "-b", "main")
    git(remote, "init", "--bare")
    (repo / "feature.py").write_text("VALUE = 0\n", encoding="utf-8")
    (repo / "test_feature.py").write_text(
        "from feature import VALUE\n\ndef test_shadow_candidate():\n    assert VALUE == 42\n",
        encoding="utf-8",
    )
    git(repo, "add", ".")
    git(repo, "commit", "-m", "shadow base")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-u", "origin", "main")

    private = root / "gate-private.pem"
    public = root / "gate-public.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", private], check=True)
    private.chmod(0o600)
    subprocess.run(["openssl", "pkey", "-in", private, "-pubout", "-out", public], check=True)

    task = {
        "id": 4863,
        "title": "Evaluable close-loop shadow candidate",
        "description": "Queue to worker to hermetic gate to shadow receipt",
        "status": "pending", "parent_task_id": None, "completed_on": None,
        "details": {"aq_phase1": {
            "admit": True,
            "intent": {
                "repo_id": str(repo), "target_ref": "refs/heads/main",
                "scope": ["feature.py"], "definition_of_done": ["pytest passes"],
                "stop_condition": "one append-only shadow receipt",
                "verifier_manifest": {"id": "hermetic-v2", "sha256": "a" * 64},
                "authority": {"class": "shadow_local_only"}, "dependencies": [],
                "expected_changed_paths": ["feature.py"],
                "protected_paths": ["runner/close_loop/", "hermetic_verifier/"],
            },
            "shadow_changes": {"feature.py": "VALUE = 42\n"},
        }},
    }
    readiness = {
        "task_id": 4863, "status": "launch_ready_bounded_dev_safe_slice",
        "ready_for_worker_launch": True, "missing_contract_fields": [], "contract_state": {},
    }
    hashes = source_hashes(task, readiness)
    snapshot = SourceSnapshot(
        "talkingback", "4863", readiness["status"], True, False, hashes,
        hashes.intent_hash, payload=task,
    )
    evaluator = SubprocessHermeticEvaluator(
        signing_key=private, public_key=public, image=args.image,
        key_id="close-loop-shadow-key-v1", nonce_store=root / "used-nonces",
        timeout_seconds=120,
    )
    receipt = run_shadow_once(
        snapshot=snapshot, repo=repo,
        worker=AuthenticatedIdentity("close-loop-shadow-worker", "local-demo-auth"),
        reviewer_principal="hermetic:close-loop-shadow-key-v1",
        command=TestCommand.from_argv(("python", "-m", "pytest", "-q"), kind="pytest"),
        evaluator=evaluator, receipt_log=args.receipt_log,
    )
    print(json.dumps(receipt, sort_keys=True, indent=2))
    return 0 if receipt["gate"]["approved"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
