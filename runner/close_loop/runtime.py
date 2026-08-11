"""Small, real git runtime for the Phase-0 close-loop proof.

The runtime deliberately has no queue, database, or remote-push integration.  It
materializes a worker commit and lets a separately authenticated principal run a
fixed verifier manifest at that exact object in a disposable detached worktree.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


class RuntimeRefused(RuntimeError):
    """Fail-closed refusal at a worker/reviewer trust boundary."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AuthenticatedIdentity:
    principal: str
    authentication: str

    @property
    def authenticated(self) -> bool:
        return bool(self.principal.strip() and self.authentication.strip())


@dataclass(frozen=True)
class WorkerCandidate:
    repo: Path
    branch: str
    base_sha: str
    candidate_sha: str
    worker: AuthenticatedIdentity
    source_worktree: Path


@dataclass(frozen=True)
class TestCommand:
    """An argv-only verifier command.  Shell strings are intentionally rejected."""

    argv: tuple[str, ...]
    kind: str = "command"  # ``pytest`` enables collected/pass/skip accounting.

    def __post_init__(self) -> None:
        if not self.argv or not all(isinstance(part, str) and part for part in self.argv):
            raise ValueError("test command must contain non-empty argv")
        if self.kind not in {"command", "pytest"}:
            raise ValueError("test command kind must be command or pytest")

    @classmethod
    def from_argv(cls, argv: Sequence[str], *, kind: str = "command") -> "TestCommand":
        return cls(tuple(argv), kind)


@dataclass(frozen=True)
class TestObservation:
    command: TestCommand
    returncode: int
    stdout: str
    stderr: str
    collected: int
    passed: int
    skipped: int


@dataclass(frozen=True)
class ReviewReceipt:
    candidate_sha: str
    worker_principal: str
    reviewer_principal: str
    reviewer_authenticated: bool
    exact_sha_verified: bool
    detached_worktree: bool
    manifest: tuple[TestCommand, ...]
    observations: tuple[TestObservation, ...]


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, timeout=60, check=False
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeRefused("git_command_failed", f"git {' '.join(args)} failed: {detail}")
    return proc


def _identity_env(identity: AuthenticatedIdentity) -> dict[str, str]:
    # Authentication is checked, but never copied to the child process or commit.
    if not identity.authenticated:
        raise RuntimeRefused("worker_not_authenticated", "worker identity is not authenticated")
    safe = identity.principal.strip()
    return {
        **os.environ,
        "GIT_AUTHOR_NAME": safe,
        "GIT_AUTHOR_EMAIL": f"{safe.replace(' ', '.')}@aq.invalid",
        "GIT_COMMITTER_NAME": safe,
        "GIT_COMMITTER_EMAIL": f"{safe.replace(' ', '.')}@aq.invalid",
    }


def produce_worker_commit(
    repo: str | Path,
    *,
    branch: str,
    worker: AuthenticatedIdentity,
    changes: Mapping[str, str | None],
    message: str = "phase0 worker candidate",
    base_ref: str = "HEAD",
) -> WorkerCandidate:
    """Create a real branch and commit from ``base_ref`` in ``repo``.

    A ``None`` value deletes a tracked path. Paths cannot escape the repository.
    The caller owns the repository; no remote operation is performed.
    """
    root = Path(repo).resolve()
    commit_env = _identity_env(worker)  # authenticate before any filesystem/ref mutation
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", branch) or ".." in branch:
        raise RuntimeRefused("invalid_branch", "unsafe worker branch name")
    validated_changes: list[tuple[Path, str | None]] = []
    for rel, content in changes.items():
        path = (root / rel).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise RuntimeRefused("path_escape", f"change path escapes repository: {rel}") from exc
        validated_changes.append((path, content))
    if _git(root, "status", "--porcelain").stdout:
        raise RuntimeRefused("candidate_dirty_or_untracked", "worker source worktree is not clean")
    base_sha = _git(root, "rev-parse", "--verify", f"{base_ref}^{{commit}}").stdout.strip()
    _git(root, "switch", "-c", branch, base_sha)
    for path, content in validated_changes:
        if content is None:
            if path.exists():
                path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    _git(root, "add", "-A")
    if _git(root, "diff", "--cached", "--quiet", check=False).returncode == 0:
        # Leave the real branch in place: the deterministic gate must prove this
        # exact "done but no commit" defect rather than receiving a fake packet.
        return WorkerCandidate(root, branch, base_sha, base_sha, worker, root)
    proc = subprocess.run(
        ["git", "commit", "-m", message],
        cwd=root,
        env=commit_env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeRefused("git_commit_failed", (proc.stderr or proc.stdout).strip())
    candidate_sha = _git(root, "rev-parse", "HEAD^{commit}").stdout.strip()
    return WorkerCandidate(root, branch, base_sha, candidate_sha, worker, root)


def _pytest_counts(text: str) -> tuple[int, int, int]:
    def count(label: str) -> int:
        hits = re.findall(rf"(?:^|[ ,])([0-9]+)\s+{label}\b", text, re.MULTILINE)
        return sum(int(hit) for hit in hits[-1:])

    passed = count("passed")
    skipped = count("skipped")
    # pytest summaries do not always print the collected total. The observed
    # executed outcomes are enough to reject vacuous all-skipped/no-test runs.
    collected_match = re.findall(r"collected\s+([0-9]+)\s+items?", text)
    collected = int(collected_match[-1]) if collected_match else passed + skipped
    return collected, passed, skipped


def review_exact_candidate(
    candidate: WorkerCandidate,
    *,
    reviewer: AuthenticatedIdentity,
    manifest: Sequence[TestCommand],
) -> ReviewReceipt:
    """Run ``manifest`` at the exact candidate SHA as a distinct reviewer.

    The worktree is always detached and always removed in ``finally``. A receipt
    is returned for failing tests; authentication, role separation, or inability
    to materialize the exact object are hard refusals.
    """
    if not reviewer.authenticated:
        raise RuntimeRefused("reviewer_not_authenticated", "reviewer identity is not authenticated")
    if reviewer.principal == candidate.worker.principal:
        raise RuntimeRefused("role_separation_failed", "worker and reviewer principals must differ")
    commands = tuple(manifest)
    parent = Path(tempfile.mkdtemp(prefix="aq-review-"))
    worktree = parent / "candidate"
    observations: list[TestObservation] = []
    try:
        _git(candidate.repo, "cat-file", "-e", f"{candidate.candidate_sha}^{{commit}}")
        _git(candidate.repo, "worktree", "add", "--detach", str(worktree), candidate.candidate_sha)
        actual = _git(worktree, "rev-parse", "HEAD^{commit}").stdout.strip()
        detached = _git(worktree, "symbolic-ref", "-q", "HEAD", check=False).returncode != 0
        if actual != candidate.candidate_sha or not detached:
            raise RuntimeRefused("evidence_sha_mismatch", "review worktree is not detached at exact SHA")
        env = {**os.environ, "AQ_REVIEWER_PRINCIPAL": reviewer.principal}
        for command in commands:
            try:
                proc = subprocess.run(
                    list(command.argv), cwd=worktree, env=env, capture_output=True,
                    text=True, timeout=120, check=False,
                )
                stdout, stderr, returncode = proc.stdout, proc.stderr, proc.returncode
            except (OSError, subprocess.TimeoutExpired) as exc:
                stdout, stderr, returncode = "", str(exc), -1
            if command.kind == "pytest":
                collected, passed, skipped = _pytest_counts(stdout + "\n" + stderr)
            else:
                collected, passed, skipped = 1, int(returncode == 0), 0
            observations.append(TestObservation(
                command, returncode, stdout, stderr, collected, passed, skipped
            ))
        return ReviewReceipt(
            actual, candidate.worker.principal, reviewer.principal, True, True,
            True, commands, tuple(observations),
        )
    finally:
        _git(candidate.repo, "worktree", "remove", "--force", str(worktree), check=False)
        shutil.rmtree(parent, ignore_errors=True)


TestCommand.__test__ = False
TestObservation.__test__ = False
