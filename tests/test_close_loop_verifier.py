from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path

import pytest

from runner.close_loop.policy import CheckSpec, PolicyError, VerifierPolicy, canonical_json
import runner.close_loop.verifier as verifier_module
from runner.close_loop.verifier import ObservedResult, ResultBinding, verify_closure

BASE = "1" * 40
CANDIDATE = "2" * 40
TREE = "3" * 40
EMPTY_DIGEST = hashlib.sha256(b"").hexdigest()


def _policy(tmp_path: Path) -> tuple[VerifierPolicy, Path, Path]:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    trusted = tmp_path / "supervisor"
    checks = trusted / "checks"
    checks.mkdir(parents=True)
    artifact = checks / "required.py"
    artifact.write_text("print('trusted')\n", encoding="utf-8")
    raw = {
        "candidate_sha": CANDIDATE,
        "checks": [{
            "timeout_seconds": 30,
            "argv": ["/usr/local/bin/python", "-I", "/opt/aq-trusted/checks/required.py"],
            "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "artifact": "required.py",
            "id": "unit.required",
        }],
        "base_sha": BASE,
        "version": 1,
    }
    manifest = trusted / "manifest.json"
    manifest.write_text(json.dumps(raw), encoding="utf-8")
    policy = VerifierPolicy.load(
        manifest, trusted_checks_root=checks, candidate_root=candidate
    )
    return policy, manifest, candidate


def _result(policy: VerifierPolicy, **changes) -> ObservedResult:
    values = {
        "check_id": "unit.required",
        "status": "passed",
        "exit_code": 0,
        "stdout_digest": EMPTY_DIGEST,
        "stderr_digest": EMPTY_DIGEST,
        "binding": ResultBinding(BASE, CANDIDATE, TREE, policy.digest),
    }
    values.update(changes)
    return ObservedResult(**values)


def _verify(policy: VerifierPolicy, results, **changes):
    values = dict(
        base_sha=BASE,
        candidate_sha=CANDIDATE,
        tree_sha=TREE,
        manifest_digest=policy.digest,
        changed_paths=("runner/code.py",),
        expected_changed_paths=("runner/*.py",),
        observed_results=results,
    )
    values.update(changes)
    return verify_closure(policy, **values)


def test_manifest_is_strict_immutable_nonempty_and_canonically_digested(tmp_path):
    policy, _, _ = _policy(tmp_path)
    assert policy.digest == hashlib.sha256(canonical_json({
        "version": 1,
        "base_sha": BASE,
        "candidate_sha": CANDIDATE,
        "checks": [policy.checks[0].mapping()],
    })).hexdigest()
    with pytest.raises(FrozenInstanceError):
        policy.base_sha = "4" * 40
    with pytest.raises(FrozenInstanceError):
        policy.checks[0].id = "changed"
    with pytest.raises(PolicyError, match="invalid check id"):
        CheckSpec("", "required.py", EMPTY_DIGEST, (
            "/usr/local/bin/python", "-I", "/opt/aq-trusted/checks/required.py"
        ), 30)
    with pytest.raises(PolicyError, match="non-empty"):
        VerifierPolicy(1, BASE, CANDIDATE, (), EMPTY_DIGEST)


def test_policy_must_be_external_and_candidate_manifest_cannot_replace_it(tmp_path):
    policy, trusted_manifest, candidate = _policy(tmp_path)
    candidate_manifest = candidate / "manifest.json"
    candidate_manifest.write_text(json.dumps({"version": 1, "checks": []}))
    with pytest.raises(PolicyError, match="external"):
        VerifierPolicy.load(candidate_manifest, candidate_root=candidate)
    # Candidate content is ignored: authority remains the explicit trusted path.
    again = VerifierPolicy.load(trusted_manifest, candidate_root=candidate)
    assert again.digest == policy.digest
    assert again.required_ids == ("unit.required",)


def test_manifest_rejects_duplicate_json_keys_ids_and_candidate_argv(tmp_path):
    _, trusted_manifest, candidate = _policy(tmp_path)
    trusted_manifest.write_text(
        '{"version":1,"version":1,"base_sha":"' + BASE + '","candidate_sha":"' + CANDIDATE + '","checks":[]}'
    )
    with pytest.raises(PolicyError, match="duplicate JSON key"):
        VerifierPolicy.load(trusted_manifest, candidate_root=candidate)
    with pytest.raises(PolicyError, match="candidate"):
        CheckSpec("x", "x.py", EMPTY_DIGEST, (
            "/usr/local/bin/python", "-I", "/opt/aq-trusted/checks/x.py", "/candidate/test.py"
        ), 2)


def test_positive_closure_requires_exact_path_and_result_coverage(tmp_path):
    policy, _, _ = _policy(tmp_path)
    decision = _verify(policy, [_result(policy)])
    assert decision.approved
    assert decision.reason_codes == ()
    assert len(decision.evidence_digest) == 64


@pytest.mark.parametrize(
    "results, expected_reason",
    [
        ([], "required_check_missing"),
        ("duplicate", "required_check_duplicate"),
        ("skipped", "required_test_skipped"),
        ("failed", "required_test_failed"),
        ("malformed", "observed_result_malformed"),
    ],
)
def test_missing_duplicate_skipped_failed_and_malformed_all_hold(tmp_path, results, expected_reason):
    policy, _, _ = _policy(tmp_path)
    good = _result(policy)
    if results == "duplicate":
        results = [good, good]
    elif results == "skipped":
        results = [_result(policy, status="skipped", exit_code=0)]
    elif results == "failed":
        results = [_result(policy, status="failed", exit_code=1)]
    elif results == "malformed":
        results = [{"check_id": "unit.required"}]
    decision = _verify(policy, results)
    assert decision.held
    assert expected_reason in decision.reason_codes


def test_changed_paths_require_bidirectional_coverage_and_protected_paths_hold(tmp_path):
    policy, _, _ = _policy(tmp_path)
    good = [_result(policy)]
    missing = _verify(policy, good, expected_changed_paths=("runner/*.py", "tests/*.py"))
    assert "expected_changed_path_missing" in missing.reason_codes
    uncovered = _verify(
        policy, good, changed_paths=("runner/code.py", "docs/surprise.md")
    )
    assert "changed_path_uncovered" in uncovered.reason_codes
    protected = _verify(
        policy, good, expected_changed_paths=("runner/*.py",), protected_paths=("runner/code.py",)
    )
    assert "protected_path" in protected.reason_codes


@pytest.mark.parametrize("field", ["base", "candidate", "tree", "manifest"])
def test_every_observation_is_bound_to_base_candidate_tree_and_manifest(tmp_path, field):
    policy, _, _ = _policy(tmp_path)
    if field == "base":
        result = _result(policy, binding=ResultBinding("4" * 40, CANDIDATE, TREE, policy.digest))
    elif field == "candidate":
        result = _result(policy, binding=ResultBinding(BASE, "4" * 40, TREE, policy.digest))
    elif field == "tree":
        result = _result(policy, binding=ResultBinding(BASE, CANDIDATE, "4" * 40, policy.digest))
    else:
        result = _result(policy, binding=ResultBinding(BASE, CANDIDATE, TREE, "4" * 64))
    decision = _verify(policy, [result])
    assert decision.held
    assert "evidence_binding_mismatch" in decision.reason_codes


def test_invocation_binding_mismatch_holds(tmp_path):
    policy, _, _ = _policy(tmp_path)
    result = _result(policy)
    assert "base_sha_mismatch" in _verify(policy, [result], base_sha="4" * 40).reason_codes
    assert "candidate_sha_mismatch" in _verify(policy, [result], candidate_sha="4" * 40).reason_codes
    assert "test_manifest_mismatch" in _verify(policy, [result], manifest_digest="4" * 64).reason_codes


def test_real_cli_path_hashes_receipt_and_emits_approval(tmp_path, monkeypatch, capsys):
    policy, manifest, candidate = _policy(tmp_path)
    checks_root = manifest.parent / "checks"
    receipt = {
        "protocol": "aq-trusted-check-v1",
        "id": policy.required_ids[0],
        "outcome": "passed",
        "passed_count": 1,
        "skipped_count": 0,
    }
    completed = verifier_module.subprocess.CompletedProcess(
        args=policy.checks[0].argv,
        returncode=0,
        stdout=("AQ_CHECK_RESULT:" + json.dumps(receipt) + "\n").encode(),
        stderr=b"",
    )
    monkeypatch.setattr(verifier_module.subprocess, "run", lambda *args, **kwargs: completed)
    assert verifier_module._main([str(manifest), str(checks_root), str(candidate)]) == 0
    emitted = json.loads(capsys.readouterr().out)
    assert emitted["verdict"] == "approve_local"
    assert emitted["policy_digest"] == policy.digest
    assert emitted["observations"][0]["stdout_sha256"] == hashlib.sha256(completed.stdout).hexdigest()
