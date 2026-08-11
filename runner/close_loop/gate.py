"""Deterministic, fail-closed Phase-0 merge gate."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Sequence

from .runtime import ReviewReceipt, TestCommand

GATE_VERSION = "aq-close-loop-phase0-v1"
DEFAULT_PROTECTED_PREFIXES = (
    ".github/", ".gitlab/", "runner/close_loop/", "secrets/", "deploy/",
    "deployment/", "infra/", "pyproject.toml", "tox.ini",
)


@dataclass(frozen=True)
class GateRequest:
    repo: Path
    base_sha: str
    candidate_sha: str
    worker_principal: str
    reviewer_principal: str
    receipt: ReviewReceipt | None
    required_manifest: tuple[TestCommand, ...]
    source_worktree: Path | None = None
    protected_prefixes: tuple[str, ...] = DEFAULT_PROTECTED_PREFIXES
    observed_target_sha: str | None = None
    # Used only by the refusal proof for the post-push read-back boundary.  The
    # Phase-0 actuator never attempts a public push.
    require_remote_verification: bool = False
    push_succeeded: bool | None = None
    remote_readback_sha: str | None = None
    expected_remote_sha: str | None = None
    source_intent_hash: str | None = None
    admitted_source_intent_hash: str | None = None
    mission_hash: str | None = None
    admitted_mission_hash: str | None = None


@dataclass(frozen=True)
class GateDecision:
    approved: bool
    reason: str
    gate_version: str
    base_sha: str
    candidate_sha: str
    changed_paths: tuple[str, ...] = ()
    integration_tree_sha: str | None = None


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    argv = ["git", *args]
    try:
        return subprocess.run(
            argv, cwd=repo, capture_output=True, text=True,
            timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        # A gate must turn missing repositories/tools and timeouts into a
        # deterministic refusal, never leak an infrastructure exception as an
        # accidental approval path.
        return subprocess.CompletedProcess(argv, -1, "", str(exc))


def _refuse(req: GateRequest, code: str, paths: Sequence[str] = ()) -> GateDecision:
    return GateDecision(False, code, GATE_VERSION, req.base_sha, req.candidate_sha, tuple(paths))


def _valid_commit(repo: Path, sha: str) -> bool:
    return _git(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def _changed_paths(repo: Path, base: str, candidate: str) -> tuple[str, ...] | None:
    proc = _git(repo, "diff", "--name-only", "--diff-filter=ACDMRTUXB", base, candidate)
    if proc.returncode != 0:
        return None
    return tuple(sorted(line for line in proc.stdout.splitlines() if line))


def _protected(path: str, prefixes: Sequence[str]) -> bool:
    normalized = PurePosixPath(path).as_posix().lstrip("./")
    return any(normalized == prefix.rstrip("/") or normalized.startswith(prefix) for prefix in prefixes)


def _integration_tree(repo: Path, base: str, candidate: str) -> tuple[str | None, bool]:
    """Return a real integration tree object, or a conflict flag.

    ``git merge-tree --write-tree`` performs no ref/worktree mutation. A Git
    version without this plumbing command fails closed as an integration refusal.
    """
    proc = _git(repo, "merge-tree", "--write-tree", base, candidate)
    if proc.returncode == 0:
        tree = proc.stdout.splitlines()[0].strip() if proc.stdout else ""
        return (tree or None), False
    # merge-tree exits 1 for conflicts; avoid treating diagnostics as a tree.
    return None, True


def evaluate_gate(request: GateRequest) -> GateDecision:
    """Evaluate all authority inputs in a fixed order and return one reason code."""
    req = request
    repo = Path(req.repo).resolve()
    if (req.admitted_source_intent_hash is not None
            and req.source_intent_hash != req.admitted_source_intent_hash):
        return _refuse(req, "source_intent_changed")
    if (req.admitted_mission_hash is not None
            and req.mission_hash != req.admitted_mission_hash):
        return _refuse(req, "mission_boundary_changed")
    if not _valid_commit(repo, req.base_sha) or not _valid_commit(repo, req.candidate_sha):
        return _refuse(req, "candidate_missing")

    # A status claim without a commit is the first negative control.
    if req.candidate_sha == req.base_sha:
        return _refuse(req, "candidate_empty")
    ahead = _git(repo, "rev-list", "--count", f"{req.base_sha}..{req.candidate_sha}")
    if ahead.returncode != 0 or int((ahead.stdout or "0").strip() or "0") == 0:
        return _refuse(req, "candidate_empty")

    if req.source_worktree is not None:
        status = _git(Path(req.source_worktree), "status", "--porcelain")
        if status.returncode != 0 or bool(status.stdout.strip()):
            return _refuse(req, "candidate_dirty_or_untracked")

    receipt = req.receipt
    if req.worker_principal == req.reviewer_principal:
        return _refuse(req, "role_separation_failed")
    if receipt is None or not receipt.reviewer_authenticated:
        return _refuse(req, "reviewer_not_authenticated")
    if receipt.worker_principal == receipt.reviewer_principal:
        return _refuse(req, "role_separation_failed")
    if (
        receipt.worker_principal != req.worker_principal
        or receipt.reviewer_principal != req.reviewer_principal
    ):
        return _refuse(req, "evidence_identity_mismatch")
    if (
        receipt.candidate_sha != req.candidate_sha
        or not receipt.exact_sha_verified
        or not receipt.detached_worktree
    ):
        return _refuse(req, "evidence_sha_mismatch")

    if tuple(receipt.manifest) != tuple(req.required_manifest):
        return _refuse(req, "test_manifest_mismatch")
    if not req.required_manifest:
        return _refuse(req, "required_test_missing")

    paths = _changed_paths(repo, req.base_sha, req.candidate_sha)
    if paths is None:
        return _refuse(req, "candidate_diff_failed")
    if any(_protected(path, req.protected_prefixes) for path in paths):
        return _refuse(req, "protected_path", paths)

    tree, conflict = _integration_tree(repo, req.base_sha, req.candidate_sha)
    if conflict:
        return _refuse(req, "integration_conflict", paths)

    if len(receipt.observations) != len(req.required_manifest):
        return _refuse(req, "required_test_missing", paths)
    for expected, observation in zip(req.required_manifest, receipt.observations):
        if observation.command != expected:
            return _refuse(req, "test_manifest_mismatch", paths)
        if observation.returncode != 0:
            return _refuse(req, "required_test_failed", paths)
        if observation.skipped > 0:
            return _refuse(req, "required_test_skipped", paths)
        if expected.kind == "pytest":
            if observation.collected <= 0 or observation.passed <= 0:
                return _refuse(req, "required_test_missing", paths)

    if req.observed_target_sha is not None and req.observed_target_sha != req.base_sha:
        return _refuse(req, "stale_base", paths)

    if req.require_remote_verification:
        if not req.push_succeeded:
            return _refuse(req, "remote_not_verified", paths)
        if (
            not req.expected_remote_sha
            or req.remote_readback_sha != req.expected_remote_sha
        ):
            return _refuse(req, "remote_not_verified", paths)

    return GateDecision(
        True, "approved", GATE_VERSION, req.base_sha, req.candidate_sha,
        paths, tree,
    )
