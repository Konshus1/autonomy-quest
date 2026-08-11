"""Fail-closed gate matrix and local-only actuator acceptance tests."""
from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from runner.close_loop.actuator import ActuationRefused, actuate
from runner.close_loop.gate import GateRequest, evaluate_gate
from runner.close_loop.runtime import (
    AuthenticatedIdentity,
    TestCommand,
    produce_worker_commit,
    review_exact_candidate,
)


GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "fixture owner",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "fixture owner",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
}


def run_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", *args], cwd=repo, env=GIT_ENV, capture_output=True, text=True
    )
    if check:
        assert proc.returncode == 0, proc.stderr
    return proc


def git(repo: Path, *args: str) -> str:
    return run_git(repo, *args).stdout.strip()


def fixture_request(tmp_path: Path) -> tuple[Path, Path, GateRequest]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    repo.mkdir()
    run_git(repo, "init", "-b", "main")
    (repo / "README.md").write_text("base\n")
    run_git(repo, "add", "README.md")
    run_git(repo, "commit", "-m", "base")
    base_sha = git(repo, "rev-parse", "refs/heads/main")

    remote.mkdir()
    run_git(remote, "init", "--bare")
    run_git(repo, "remote", "add", "origin", str(remote))
    run_git(repo, "push", "-u", "origin", "main")

    worker = AuthenticatedIdentity("worker-gate", "worker-auth")
    reviewer = AuthenticatedIdentity("reviewer-gate", "reviewer-auth")
    candidate = produce_worker_commit(
        repo,
        branch="task/gate",
        worker=worker,
        changes={"feature.txt": "gate candidate\n"},
    )
    command = TestCommand.from_argv(
        (
            sys.executable,
            "-c",
            "from pathlib import Path; assert Path('feature.txt').read_text() == 'gate candidate\\n'",
        )
    )
    receipt = review_exact_candidate(candidate, reviewer=reviewer, manifest=(command,))
    assert receipt.observations[0].returncode == 0
    request = GateRequest(
        repo=repo,
        base_sha=base_sha,
        candidate_sha=candidate.candidate_sha,
        worker_principal=worker.principal,
        reviewer_principal=reviewer.principal,
        receipt=receipt,
        required_manifest=(command,),
        source_worktree=repo,
        protected_prefixes=("runner/close_loop/", "deployment/"),
        observed_target_sha=git(repo, "rev-parse", "refs/heads/main"),
    )
    return repo, remote, request


def initialized_repo(tmp_path: Path) -> Path:
    """Compatibility helper for the conflict negative control below."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init", "-b", "main")
    (repo / "README.md").write_text("base\n")
    run_git(repo, "add", "README.md")
    run_git(repo, "commit", "-m", "base")
    return repo


def fixture(tmp_path: Path):
    """Expanded view used by mutation-focused negative controls."""
    repo, _remote, request = fixture_request(tmp_path)
    assert request.receipt is not None
    candidate = SimpleNamespace(candidate_sha=request.candidate_sha)
    return repo, candidate, request.receipt, request.required_manifest[0], request


def refs(repo: Path) -> dict[str, str]:
    output = git(repo, "for-each-ref", "--format=%(refname) %(objectname)")
    return dict(line.split(" ", 1) for line in output.splitlines() if line)


def test_gate_positive_and_negative_matrix(tmp_path: Path) -> None:
    repo, remote, request = fixture_request(tmp_path)
    approved = evaluate_gate(request)
    assert approved.approved is True
    assert approved.reason == "approved"
    assert approved.changed_paths == ("feature.txt",)
    assert approved.integration_tree_sha

    receipt = request.receipt
    assert receipt is not None
    observation = receipt.observations[0]
    alternate = TestCommand.from_argv((sys.executable, "-c", "raise SystemExit(0)"))
    pytest_command = TestCommand.from_argv((sys.executable, "-m", "pytest"), kind="pytest")

    cases = [
        ("candidate_empty", replace(request, candidate_sha=request.base_sha)),
        ("role_separation_failed", replace(request, reviewer_principal=request.worker_principal)),
        ("reviewer_not_authenticated", replace(request, receipt=None)),
        (
            "evidence_identity_mismatch",
            replace(request, reviewer_principal="different-authenticated-reviewer"),
        ),
        (
            "evidence_sha_mismatch",
            replace(request, receipt=replace(receipt, candidate_sha=request.base_sha)),
        ),
        ("test_manifest_mismatch", replace(request, required_manifest=(alternate,))),
        (
            "required_test_missing",
            replace(request, receipt=replace(receipt, manifest=(), observations=()), required_manifest=()),
        ),
        (
            "required_test_missing",
            replace(request, receipt=replace(receipt, observations=())),
        ),
        (
            "required_test_failed",
            replace(
                request,
                receipt=replace(
                    receipt,
                    observations=(replace(observation, returncode=1, stderr="failed"),),
                ),
            ),
        ),
        (
            "required_test_missing",
            replace(
                request,
                required_manifest=(pytest_command,),
                receipt=replace(
                    receipt,
                    manifest=(pytest_command,),
                    observations=(replace(
                        observation, command=pytest_command, collected=0, passed=0, skipped=0
                    ),),
                ),
            ),
        ),
        (
            "required_test_skipped",
            replace(
                request,
                required_manifest=(pytest_command,),
                receipt=replace(
                    receipt,
                    manifest=(pytest_command,),
                    observations=(replace(
                        observation, command=pytest_command, collected=2, passed=1, skipped=1
                    ),),
                ),
            ),
        ),
        ("protected_path", replace(request, protected_prefixes=("feature.txt",))),
        ("stale_base", replace(request, observed_target_sha=request.candidate_sha)),
        (
            "remote_not_verified",
            replace(request, require_remote_verification=True, push_succeeded=False),
        ),
        ("candidate_missing", replace(request, repo=tmp_path / "missing-repo")),
    ]
    task_projection = {"status": "review_ready"}
    for expected, negative in cases:
        refs_before = refs(repo)
        remote_refs_before = refs(remote)
        worktrees_before = git(repo, "worktree", "list", "--porcelain")
        task_before = dict(task_projection)
        decision = evaluate_gate(negative)
        assert decision.approved is False, expected
        assert decision.reason == expected
        assert refs(repo) == refs_before
        assert refs(remote) == remote_refs_before
        assert task_projection == task_before
        assert git(repo, "worktree", "list", "--porcelain") == worktrees_before

    # The branch now names a second real object while the receipt still names
    # the reviewed object. A caller that read the moved ref cannot reuse it.
    (repo / "later.txt").write_text("later\n")
    run_git(repo, "add", "later.txt")
    run_git(repo, "commit", "-m", "move candidate ref")
    moved_ref_sha = git(repo, "rev-parse", "refs/heads/task/gate")
    mismatch = evaluate_gate(replace(request, candidate_sha=moved_ref_sha))
    assert mismatch.approved is False
    assert mismatch.reason == "evidence_sha_mismatch"


def test_dirty_or_untracked_source_is_refused(tmp_path: Path) -> None:
    repo, _remote, request = fixture_request(tmp_path)
    refs_before = refs(repo)
    worktrees_before = git(repo, "worktree", "list", "--porcelain")
    (repo / "untracked.txt").write_text("not in candidate\n")
    decision = evaluate_gate(request)
    assert not decision.approved
    assert decision.reason == "candidate_dirty_or_untracked"
    assert refs(repo) == refs_before
    assert git(repo, "worktree", "list", "--porcelain") == worktrees_before


def test_actuator_creates_only_aq_local_ref_and_public_always_refuses(tmp_path: Path) -> None:
    repo, remote, request = fixture_request(tmp_path)
    decision = evaluate_gate(request)
    assert decision.approved

    local_before = refs(repo)
    remote_before = refs(remote)
    main_before = git(repo, "rev-parse", "refs/heads/main")

    shadow = actuate(repo, decision=decision, mode="shadow", attempt="shadow-1")
    assert shadow.status == "would_approve" and shadow.ref is None
    assert refs(repo) == local_before
    assert refs(remote) == remote_before

    result = actuate(repo, decision=decision, mode="local", attempt="phase0-1")
    expected_ref = "refs/aq/integration/phase0-1"
    assert result.ref == expected_ref
    assert result.sha
    local_after = refs(repo)
    assert set(local_after) - set(local_before) == {expected_ref}
    assert local_after[expected_ref] == result.sha
    assert git(repo, "show", "-s", "--format=%P", result.sha).split() == [
        request.base_sha, request.candidate_sha
    ]
    assert git(repo, "show", "-s", "--format=%T", result.sha) == decision.integration_tree_sha
    assert git(repo, "rev-parse", "refs/heads/main") == main_before
    assert refs(remote) == remote_before

    # The local integration commit is reproducible for identical authority input.
    run_git(repo, "update-ref", "-d", expected_ref)
    recreated = actuate(repo, decision=decision, mode="local", attempt="phase0-1")
    assert recreated.sha == result.sha

    with pytest.raises(ActuationRefused) as duplicate:
        actuate(repo, decision=decision, mode="local", attempt="phase0-1")
    assert duplicate.value.code == "local_ref_update_failed"

    before_public_local = refs(repo)
    before_public_remote = refs(remote)
    with pytest.raises(ActuationRefused) as public:
        actuate(repo, decision=decision, mode="public", attempt="claimed-approved")
    assert public.value.code == "public_interlock_pending"
    assert "refusal proof" in str(public.value)
    assert "c2f-4834-analogies-research" in str(public.value)
    assert "Kevin" in str(public.value)
    assert refs(repo) == before_public_local
    assert refs(remote) == before_public_remote




def test_real_merge_tree_conflict_refuses_without_ref_or_cleanup_mutation(tmp_path: Path) -> None:
    repo, _remote, request = fixture_request(tmp_path)
    run_git(repo, "switch", "main")
    (repo / "feature.txt").write_text("target-side content\n")
    run_git(repo, "add", "feature.txt")
    run_git(repo, "commit", "-m", "move target with conflicting content")
    moved_target = git(repo, "rev-parse", "HEAD")
    run_git(repo, "switch", "task/gate")
    conflict = replace(
        request, base_sha=moved_target, observed_target_sha=moved_target
    )
    refs_before = refs(repo)
    worktrees_before = git(repo, "worktree", "list", "--porcelain")
    decision = evaluate_gate(conflict)
    assert decision.approved is False
    assert decision.reason == "integration_conflict"
    assert refs(repo) == refs_before
    assert git(repo, "worktree", "list", "--porcelain") == worktrees_before


def test_intent_and_mission_hashes_refuse_before_any_ref_mutation(tmp_path):
    repo,candidate,receipt,command,request = fixture(tmp_path)
    before = refs(repo)
    intent = replace(request, source_intent_hash="a"*64, admitted_source_intent_hash="b"*64)
    assert evaluate_gate(intent).reason == "source_intent_changed"
    mission = replace(request, mission_hash="c"*64, admitted_mission_hash="d"*64)
    assert evaluate_gate(mission).reason == "mission_boundary_changed"
    assert refs(repo) == before
