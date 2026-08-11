"""Real-Git acceptance tests for the Phase-0 worker/reviewer runtime."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from runner.close_loop.runtime import (
    AuthenticatedIdentity,
    RuntimeRefused,
    TestCommand as ReviewCommand,
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


def git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, env=GIT_ENV, capture_output=True, text=True
    )
    if check:
        assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def initialized_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    (repo / "README.md").write_text("base\n")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "base")
    return repo


def test_worker_commits_branch_and_distinct_reviewer_checks_exact_clean_detached_sha(
    tmp_path: Path,
) -> None:
    repo = initialized_repo(tmp_path)
    worker = AuthenticatedIdentity("worker-17", "authenticated-worker-session")
    reviewer = AuthenticatedIdentity("reviewer-42", "authenticated-review-session")
    candidate = produce_worker_commit(
        repo,
        branch="task/phase0",
        worker=worker,
        changes={"feature.txt": "verified content\n"},
    )

    assert candidate.candidate_sha != candidate.base_sha
    assert git(repo, "rev-parse", "refs/heads/task/phase0") == candidate.candidate_sha
    assert git(repo, "show", "-s", "--format=%an", candidate.candidate_sha) == "worker-17"
    assert git(repo, "status", "--porcelain", "--untracked-files=all") == ""

    verifier = """
import os
import subprocess
from pathlib import Path

def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=False)

expected = os.environ["EXPECTED_CANDIDATE_SHA"]
assert os.environ["AQ_REVIEWER_PRINCIPAL"] == "reviewer-42"
assert git("rev-parse", "HEAD^{commit}").stdout.strip() == expected
assert git("symbolic-ref", "-q", "HEAD").returncode != 0
assert git("status", "--porcelain", "--untracked-files=all").stdout == ""
assert Path("feature.txt").read_text() == "verified content\\n"
print("exact-sha detached clean")
"""
    # The expected object is passed through the test process environment rather
    # than interpolated into a shell command; the runtime accepts argv only.
    old = os.environ.get("EXPECTED_CANDIDATE_SHA")
    os.environ["EXPECTED_CANDIDATE_SHA"] = candidate.candidate_sha
    before = git(repo, "worktree", "list", "--porcelain")
    try:
        receipt = review_exact_candidate(
            candidate,
            reviewer=reviewer,
            manifest=(ReviewCommand.from_argv((sys.executable, "-c", verifier)),),
        )
    finally:
        if old is None:
            os.environ.pop("EXPECTED_CANDIDATE_SHA", None)
        else:
            os.environ["EXPECTED_CANDIDATE_SHA"] = old

    assert receipt.candidate_sha == candidate.candidate_sha
    assert receipt.worker_principal == "worker-17"
    assert receipt.reviewer_principal == "reviewer-42"
    assert receipt.exact_sha_verified and receipt.detached_worktree
    assert receipt.reviewer_authenticated
    assert receipt.observations[0].returncode == 0
    assert "exact-sha detached clean" in receipt.observations[0].stdout
    assert git(repo, "worktree", "list", "--porcelain") == before
    assert git(repo, "status", "--porcelain", "--untracked-files=all") == ""


def test_review_refuses_unauthenticated_or_same_principal(tmp_path: Path) -> None:
    repo = initialized_repo(tmp_path)
    candidate = produce_worker_commit(
        repo,
        branch="task/identity",
        worker=AuthenticatedIdentity("principal-one", "worker-auth"),
        changes={"identity.txt": "candidate\n"},
    )

    with pytest.raises(RuntimeRefused) as same:
        review_exact_candidate(
            candidate,
            reviewer=AuthenticatedIdentity("principal-one", "different-session"),
            manifest=(),
        )
    assert same.value.code == "role_separation_failed"

    with pytest.raises(RuntimeRefused) as anonymous:
        review_exact_candidate(
            candidate,
            reviewer=AuthenticatedIdentity("principal-two", ""),
            manifest=(),
        )
    assert anonymous.value.code == "reviewer_not_authenticated"
