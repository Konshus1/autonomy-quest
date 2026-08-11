from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import subprocess

from hermetic_verifier import run as hermetic_run
from hermetic_verifier.verify_verdict import AuthorizationRequest
from runner.close_loop.bridge import SourceSnapshot
from runner.close_loop.hashing import source_hashes
from runner.close_loop.runtime import AuthenticatedIdentity, TestCommand
from runner.close_loop.shadow import HermeticEvaluation, run_shadow_once


GIT_ENV = {
    **os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
}


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, env=GIT_ENV, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def _fixture(tmp_path: Path):
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    repo.mkdir(); remote.mkdir()
    _git(repo, "init", "-b", "main")
    _git(remote, "init", "--bare")
    (repo / "README.md").write_text("base\n")
    (repo / "test_feature.py").write_text("def test_base(): assert True\n")
    _git(repo, "add", "."); _git(repo, "commit", "-m", "base")
    _git(repo, "remote", "add", "origin", str(remote)); _git(repo, "push", "-u", "origin", "main")
    task = {
        "id": 4863, "title": "Shadow candidate", "description": "exercise whole loop",
        "status": "pending", "parent_task_id": None, "completed_on": None,
        "details": {"aq_phase1": {
            "admit": True,
            "intent": {
                "repo_id": str(repo), "target_ref": "refs/heads/main", "scope": ["feature.py"],
                "definition_of_done": ["pytest passes"], "stop_condition": "one shadow receipt",
                "verifier_manifest": {"id": "hermetic-v2", "sha256": "a" * 64},
                "authority": {"class": "shadow_local"}, "dependencies": [],
                "expected_changed_paths": ["feature.py"], "protected_paths": ["runner/close_loop/"],
            },
            "shadow_changes": {"feature.py": "VALUE = 42\n"},
        }},
    }
    readiness = {"task_id": 4863, "status": "launch_ready_bounded_dev_safe_slice",
                 "ready_for_worker_launch": True, "missing_contract_fields": [], "contract_state": {}}
    hashes = source_hashes(task, readiness)
    snapshot = SourceSnapshot(
        "talkingback", "4863", readiness["status"], True, False, hashes,
        hashes.intent_hash, payload=task,
    )
    private = tmp_path / "private.pem"; public = tmp_path / "public.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", private], check=True)
    private.chmod(0o600)
    subprocess.run(["openssl", "pkey", "-in", private, "-pubout", "-out", public], check=True)
    return repo, remote, snapshot, private, public


def _evaluator(tmp_path: Path, private: Path, public: Path, *, swapped=False):
    def evaluate(candidate, command: TestCommand) -> HermeticEvaluation:
        now = dt.datetime.now(dt.timezone.utc)
        plan = {"command": list(command.argv)}
        entrypoint = ["python", "/opt/hermetic-verifier/container_entrypoint.py"]
        policy = {"effective_policy_inspected": True, "network": "none"}
        payload = {
            "schema": hermetic_run.SCHEMA, "verdict": "PASS",
            "candidate": {"repository": hermetic_run.repository_id(candidate.repo),
                          "commit": candidate.candidate_sha},
            "test_plan": {**plan, "digest": hermetic_run.digest_json(plan)},
            "runtime": {"image": "approved", "image_id": "sha256:" + "b" * 64,
                        "entrypoint": entrypoint,
                        "entrypoint_digest": hermetic_run.digest_json(entrypoint)},
            "result": {"exit_code": 0, "timed_out": False, "infrastructure_error": None,
                       "passed_count": 1, "skipped_count": 0},
            "policy": policy, "policy_digest": hermetic_run.digest_json(policy),
            "authorization": {"key_id": "shadow-key-v1", "issued_at": now.isoformat(),
                              "expires_at": (now + dt.timedelta(minutes=5)).isoformat(),
                              "nonce": "nonce-shadow-001"},
        }
        request = AuthorizationRequest(
            public, payload["candidate"]["repository"], candidate.candidate_sha,
            payload["test_plan"]["digest"], payload["runtime"]["image_id"],
            payload["runtime"]["entrypoint_digest"], payload["policy_digest"],
            "shadow-key-v1", tmp_path / "nonces",
        )
        if swapped:
            payload["runtime"]["entrypoint"] = ["/usr/bin/true"]
            payload["runtime"]["entrypoint_digest"] = hermetic_run.digest_json(payload["runtime"]["entrypoint"])
        envelope = {"payload": payload, "signature": {"algorithm": "ed25519",
                    "value": hermetic_run.sign(payload, private)}}
        return HermeticEvaluation(envelope, request)
    return evaluate


def test_queue_to_worker_to_gate_to_append_only_shadow_receipt_public_inert(tmp_path):
    repo, remote, snapshot, private, public = _fixture(tmp_path)
    remote_before = _git(remote, "for-each-ref", "--format=%(refname) %(objectname)")
    receipt_log = tmp_path / "shadow-receipts.jsonl"
    result = run_shadow_once(
        snapshot=snapshot, repo=repo,
        worker=AuthenticatedIdentity("shadow-worker", "authenticated"),
        reviewer_principal="hermetic:shadow-key-v1",
        command=TestCommand.from_argv(("python", "-m", "pytest", "-q"), kind="pytest"),
        evaluator=_evaluator(tmp_path, private, public), receipt_log=receipt_log,
    )
    assert result["queue"]["work_id"] == 1 and result["queue"]["dispatched"] is True
    assert result["candidate"]["sha"] == _git(repo, "rev-parse", "refs/heads/aq-shadow/task-4863")
    assert result["hermetic_authorization"] == {"accepted": True, "reason": "accepted"}
    assert result["gate"]["approved"] is True
    assert result["shadow_actuator"]["status"] == "would_approve"
    assert result["public_actuator"] == {"status": "refused", "reason": "public_interlock_pending"}
    assert _git(remote, "for-each-ref", "--format=%(refname) %(objectname)") == remote_before
    rows = receipt_log.read_text().splitlines()
    assert len(rows) == 1 and json.loads(rows[0]) == result
    assert len(result["receipt_hash"]) == 64 and result["previous_receipt_hash"] is None


def test_resigned_swapped_entrypoint_is_held_and_public_remains_inert(tmp_path):
    repo, remote, snapshot, private, public = _fixture(tmp_path)
    remote_before = _git(remote, "for-each-ref", "--format=%(refname) %(objectname)")
    result = run_shadow_once(
        snapshot=snapshot, repo=repo,
        worker=AuthenticatedIdentity("shadow-worker", "authenticated"),
        reviewer_principal="hermetic:shadow-key-v1",
        command=TestCommand.from_argv(("python", "-m", "pytest", "-q"), kind="pytest"),
        evaluator=_evaluator(tmp_path, private, public, swapped=True),
        receipt_log=tmp_path / "held.jsonl",
    )
    assert result["hermetic_authorization"]["reason"] == "entrypoint_identity_mismatch"
    assert result["gate"] == {"approved": False, "reason": "hermetic_entrypoint_identity_mismatch"}
    assert result["public_actuator"] == {"status": "refused", "reason": "gate_not_approved"}
    assert _git(remote, "for-each-ref", "--format=%(refname) %(objectname)") == remote_before
