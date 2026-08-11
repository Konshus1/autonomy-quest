"""One-shot, background-safe queue-to-hermetic-gate shadow loop.

This module has no public merge implementation.  Its only actuator call is the
existing inert ``shadow`` mode plus an explicit public-refusal proof.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
import stat
import subprocess
import sys
from typing import Any, Callable, Mapping

from hermetic_verifier.run import canonical
from hermetic_verifier.verify_verdict import (
    AuthorizationDecision,
    AuthorizationRequest,
    verify_envelope,
)

from .actuator import ActuationRefused, actuate
from .bridge import Bridge, InMemoryBridgeLedger, SourceSnapshot
from .gate import GATE_VERSION, GateDecision, GateRequest, evaluate_gate
from .lease import AQSelector, SharedLeaseStore
from .runtime import (
    AuthenticatedIdentity,
    ReviewReceipt,
    TestCommand,
    TestObservation,
    WorkerCandidate,
    produce_worker_commit,
)


@dataclass(frozen=True)
class HermeticEvaluation:
    envelope: Mapping[str, Any]
    authorization_request: AuthorizationRequest


HermeticEvaluator = Callable[[WorkerCandidate, TestCommand], HermeticEvaluation]
APPROVED_ENTRYPOINT = ("python", "/opt/hermetic-verifier/container_entrypoint.py")


def _approved_policy(policy: object) -> bool:
    if not isinstance(policy, Mapping):
        return False
    expected = {
        "network": "none", "root_filesystem": "read-only", "capabilities": [],
        "no_new_privileges": True, "uid": 65532,
        "source_mount": "read-only-git-archive",
        "writable_filesystems": ["tmpfs:/work", "tmpfs:/tmp"],
        "credentials_forwarded": [], "effective_policy_inspected": True,
        "mount_count": 1, "network_mode": "none", "readonly_rootfs": True,
        "user": "65532:65534", "entrypoint": list(APPROVED_ENTRYPOINT),
    }
    if any(policy.get(key) != value for key, value in expected.items()):
        return False
    names = policy.get("environment_names")
    return isinstance(names, list) and set(names) <= {
        "PATH", "LANG", "LC_ALL", "HOME", "HOSTNAME", "TERM", "PYTHON_VERSION",
        "PYTHON_PIP_VERSION", "PYTHON_SETUPTOOLS_VERSION", "PYTHON_GET_PIP_URL",
        "PYTHON_GET_PIP_SHA256", "PYTHON_SHA256", "GPG_KEY", "AQ_CANDIDATE_SHA",
    } and "AQ_CANDIDATE_SHA" in names


class SubprocessHermeticEvaluator:
    """Invoke the consumed primitive and construct independent gate expectations."""

    def __init__(self, *, signing_key: str | Path, public_key: str | Path,
                 image: str, key_id: str, nonce_store: str | Path,
                 timeout_seconds: int = 300) -> None:
        self.signing_key = Path(signing_key)
        self.public_key = Path(public_key)
        self.image = image
        self.key_id = key_id
        self.nonce_store = Path(nonce_store)
        self.timeout_seconds = timeout_seconds
        from hermetic_verifier.run import image_identity
        self.image_id = image_identity(image)

    def __call__(self, candidate: WorkerCandidate, command: TestCommand) -> HermeticEvaluation:
        from hermetic_verifier.run import digest_json, repository_id
        process = subprocess.run(
            [sys.executable, "-m", "hermetic_verifier.run",
             "--repo", str(candidate.repo), "--sha", candidate.candidate_sha,
             "--signing-key", str(self.signing_key), "--key-id", self.key_id,
             "--image", self.image, "--timeout", str(self.timeout_seconds),
             "--", *command.argv],
            cwd=Path(__file__).resolve().parents[2], text=True, capture_output=True,
            timeout=self.timeout_seconds + 30, check=False,
        )
        try:
            envelope = json.loads(process.stdout)
            payload = envelope["payload"]
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"hermetic verifier emitted no signed envelope: {process.stderr[-2000:]}") from exc
        plan_digest = digest_json({"command": list(command.argv)})
        policy_digest = payload.get("policy_digest") if _approved_policy(payload.get("policy")) else "0" * 64
        request = AuthorizationRequest(
            self.public_key,
            repository_id(candidate.repo),
            candidate.candidate_sha,
            plan_digest,
            self.image_id,
            digest_json(list(APPROVED_ENTRYPOINT)),
            policy_digest,
            self.key_id,
            self.nonce_store,
        )
        return HermeticEvaluation(envelope, request)


class AppendOnlyReceiptLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, body: Mapping[str, Any]) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise RuntimeError("receipt log cannot be a symlink")
        flags = os.O_RDWR | os.O_CREAT | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.path, flags, 0o600)
        try:
            with os.fdopen(descriptor, "r+", encoding="utf-8") as stream:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise RuntimeError("receipt log must be a regular file")
                stream.seek(0)
                lines = [line for line in stream.read().splitlines() if line]
                previous = None
                if lines:
                    previous = json.loads(lines[-1]).get("receipt_hash")
                    if not isinstance(previous, str) or len(previous) != 64:
                        raise RuntimeError("receipt chain tail is malformed")
                receipt = {**body, "previous_receipt_hash": previous}
                receipt_hash = hashlib.sha256(canonical(receipt)).hexdigest()
                receipt["receipt_hash"] = receipt_hash
                stream.seek(0, os.SEEK_END)
                stream.write(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
                return receipt
        except Exception:
            # fdopen owns the descriptor after successful construction.
            raise


def _held(candidate: WorkerCandidate, reason: str) -> GateDecision:
    return GateDecision(False, reason, GATE_VERSION, candidate.base_sha, candidate.candidate_sha)


def _review_receipt(candidate: WorkerCandidate, reviewer: str, command: TestCommand,
                    evaluation: HermeticEvaluation) -> tuple[AuthorizationDecision, ReviewReceipt | None]:
    authorization = verify_envelope(evaluation.envelope, evaluation.authorization_request)
    if not authorization.accepted:
        return authorization, None
    payload = evaluation.envelope["payload"]
    result = payload["result"]
    observation = TestObservation(
        command=command,
        returncode=int(result["exit_code"]),
        stdout="",
        stderr="",
        collected=int(result["passed_count"]) + int(result["skipped_count"]),
        passed=int(result["passed_count"]),
        skipped=int(result["skipped_count"]),
    )
    return authorization, ReviewReceipt(
        candidate.candidate_sha,
        candidate.worker.principal,
        reviewer,
        True,
        True,
        True,  # exact git-archive materialization is branch-independent
        (command,),
        (observation,),
    )


def run_shadow_once(*, snapshot: SourceSnapshot, repo: str | Path,
                    worker: AuthenticatedIdentity, reviewer_principal: str,
                    command: TestCommand, evaluator: HermeticEvaluator,
                    receipt_log: str | Path) -> dict[str, Any]:
    """Consume exactly one admitted item and persist exactly one shadow receipt."""
    root = Path(repo).resolve()
    lease_store = SharedLeaseStore()
    ledger = InMemoryBridgeLedger()
    materialized = Bridge(
        "materialize", ledger=ledger, lease_store=lease_store,
        selector=AQSelector(lease_store, owner_instance="aq-shadow-loop"),
    ).tick(snapshot=snapshot)

    phase = snapshot.payload["details"]["aq_phase1"]
    changes = phase.get("shadow_changes")
    if not isinstance(changes, Mapping) or not changes:
        raise ValueError("admitted shadow task requires non-empty aq_phase1.shadow_changes")
    intent = phase["intent"]

    def dispatch_work(work_id: int) -> dict[str, Any]:
        candidate = produce_worker_commit(
            root,
            branch=f"aq-shadow/task-{snapshot.source_task_id}",
            worker=worker,
            changes=changes,
            message=f"shadow worker candidate for task {snapshot.source_task_id}",
            base_ref=intent["target_ref"],
        )
        evaluation = evaluator(candidate, command)
        authorization, review = _review_receipt(
            candidate, reviewer_principal, command, evaluation
        )
        if review is None:
            decision = _held(candidate, "hermetic_" + authorization.reason)
        else:
            decision = evaluate_gate(GateRequest(
                repo=root,
                base_sha=candidate.base_sha,
                candidate_sha=candidate.candidate_sha,
                worker_principal=worker.principal,
                reviewer_principal=reviewer_principal,
                receipt=review,
                required_manifest=(command,),
                source_worktree=root,
                protected_prefixes=tuple(intent.get("protected_paths") or ()),
                observed_target_sha=_git_sha(root, intent["target_ref"]),
                source_intent_hash=snapshot.intent_hash,
                admitted_source_intent_hash=snapshot.admitted_intent_hash,
                mission_hash=snapshot.mission_hash,
                admitted_mission_hash=snapshot.admitted_mission_hash,
            ))

        if decision.approved:
            shadow = asdict(actuate(root, decision=decision, mode="shadow", attempt=f"task-{snapshot.source_task_id}"))
            try:
                actuate(root, decision=decision, mode="public", attempt=f"task-{snapshot.source_task_id}")
            except ActuationRefused as exc:
                public = {"status": "refused", "reason": exc.code}
            else:  # pragma: no cover - the inert actuator makes this unreachable
                raise RuntimeError("public actuator unexpectedly armed")
        else:
            shadow = {"mode": "shadow", "status": "held", "ref": None, "sha": None}
            public = {"status": "refused", "reason": "gate_not_approved"}

        return {
            "schema": "aq.close-loop.shadow-receipt.v1",
            "source": {"system": snapshot.source_system, "task_id": snapshot.source_task_id,
                       "intent_hash": snapshot.intent_hash},
            "candidate": {"branch": candidate.branch, "base_sha": candidate.base_sha,
                          "sha": candidate.candidate_sha, "worker": worker.principal},
            "reviewer": reviewer_principal,
            "hermetic_authorization": {"accepted": authorization.accepted,
                                       "reason": authorization.reason},
            "hermetic_binding": {
                "image_id": evaluation.envelope["payload"].get("runtime", {}).get("image_id"),
                "entrypoint": evaluation.envelope["payload"].get("runtime", {}).get("entrypoint"),
                "entrypoint_digest": evaluation.envelope["payload"].get("runtime", {}).get("entrypoint_digest"),
                "test_plan_digest": evaluation.envelope["payload"].get("test_plan", {}).get("digest"),
                "policy_digest": evaluation.envelope["payload"].get("policy_digest"),
                "key_id": evaluation.envelope["payload"].get("authorization", {}).get("key_id"),
            },
            "gate": {"approved": decision.approved, "reason": decision.reason},
            "shadow_actuator": shadow,
            "public_actuator": public,
        }

    dispatched = Bridge(
        "dispatch", ledger=ledger, lease_store=lease_store, dispatcher=dispatch_work,
    ).tick(capability=materialized.capability)
    body = {
        **dispatched.result,
        "queue": {"work_id": dispatched.work_id, "dispatched": True,
                  "lease_generation": dispatched.lease_generation},
    }
    return AppendOnlyReceiptLog(receipt_log).append(body)


def _git_sha(repo: Path, ref: str) -> str:
    import subprocess
    result = subprocess.run(
        ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"], cwd=repo,
        text=True, capture_output=True, check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"cannot resolve target ref {ref}")
    return result.stdout.strip()
