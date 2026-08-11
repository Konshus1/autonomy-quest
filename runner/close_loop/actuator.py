"""Phase-0 actuator: shadow or AQ-local refs only; public always refuses."""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .gate import GateDecision

PUBLIC_INTERLOCK_REQUIREMENTS = (
    "fresh passing refusal proof for exact gate version and platform",
    "approval from c2f-4834-analogies-research",
    "approval from Kevin",
)


class ActuationRefused(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ActuationResult:
    mode: str
    status: str
    ref: str | None
    sha: str | None


def _git(
    repo: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo, env=env, capture_output=True, text=True,
        timeout=60, check=False,
    )


def actuate(
    repo: str | Path,
    *,
    decision: GateDecision,
    mode: str = "shadow",
    attempt: str,
) -> ActuationResult:
    """Apply a previously approved result without ever contacting a remote.

    ``local`` may create exactly ``refs/aq/integration/<attempt>``. There is no
    subprocess path to ``git push`` in this module. Public remains a boot-time
    refusal even if callers claim that approvals exist; Phase 0 has no approval
    ledger or fresh proof record from which to authenticate that claim.
    """
    root = Path(repo).resolve()
    if mode == "public":
        raise ActuationRefused(
            "public_interlock_pending",
            "public actuation unavailable pending " + "; ".join(PUBLIC_INTERLOCK_REQUIREMENTS),
        )
    if mode not in {"off", "shadow", "local"}:
        raise ActuationRefused("invalid_mode", f"unsupported merge mode: {mode}")
    if not decision.approved:
        raise ActuationRefused(decision.reason, "gate refused candidate")
    if mode == "off":
        return ActuationResult(mode, "off", None, None)
    if mode == "shadow":
        return ActuationResult(mode, "would_approve", None, decision.candidate_sha)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", attempt):
        raise ActuationRefused("invalid_attempt", "attempt is not safe for an AQ ref")
    ref = f"refs/aq/integration/{attempt}"
    if not decision.integration_tree_sha:
        raise ActuationRefused("integration_tree_missing", "gate did not bind an integration tree")
    # Build a deterministic no-ff topology from the gate-bound tree. This creates
    # an object only; update-ref below is the sole ref mutation and it cannot name
    # a branch, tag, or remote.
    commit_env = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
    }
    made = _git(
        root, "-c", "user.name=aq-local-actuator",
        "-c", "user.email=aq-local-actuator@invalid",
        "commit-tree", decision.integration_tree_sha,
        "-p", decision.base_sha, "-p", decision.candidate_sha,
        "-m", f"AQ local integration {attempt}", env=commit_env,
    )
    if made.returncode != 0:
        raise ActuationRefused("integration_commit_failed", (made.stderr or made.stdout).strip())
    integration_sha = made.stdout.strip()
    proc = _git(root, "update-ref", ref, integration_sha, "0" * 40)
    if proc.returncode != 0:
        raise ActuationRefused("local_ref_update_failed", (proc.stderr or proc.stdout).strip())
    readback = _git(root, "rev-parse", "--verify", ref).stdout.strip()
    if readback != integration_sha:
        raise ActuationRefused("local_ref_not_verified", "AQ ref read-back differs")
    return ActuationResult(mode, "created", ref, readback)
