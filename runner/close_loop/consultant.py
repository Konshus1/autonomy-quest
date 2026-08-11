"""Pure close-the-loop consultant for the shared pre-DECIDE seam.

It reads a supplied snapshot and returns a value.  It has no table, queue,
worker, gate, actuator, self-correction, or arbiter dependency.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping


@dataclass(frozen=True)
class CloseLoopSnapshot:
    admitted_tasks: tuple[Mapping[str, Any], ...]
    verified_candidates: tuple[Mapping[str, Any], ...]
    active_work_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        if not all(isinstance(item, Mapping) for item in self.admitted_tasks):
            raise TypeError("admitted_tasks must contain snapshot mappings")
        if not all(isinstance(item, Mapping) for item in self.verified_candidates):
            raise TypeError("verified_candidates must contain snapshot mappings")
        if not all(type(item) is int and item > 0 for item in self.active_work_ids):
            raise TypeError("active_work_ids must contain positive integers")


@dataclass(frozen=True)
class CloseLoopAction:
    kind: str
    source_task_id: str | None = None
    candidate_sha: str | None = None
    actuation_mode: str | None = None


@dataclass(frozen=True)
class Recommendation:
    source: str
    action: CloseLoopAction
    rationale: str
    cost_estimate: float
    requires_human: bool


def recommend_close_the_loop(snapshot: CloseLoopSnapshot) -> Recommendation:
    """Recommend merge, pull, or PASS without applying any action."""
    if not isinstance(snapshot, CloseLoopSnapshot):
        raise TypeError("close-the-loop consultant requires a CloseLoopSnapshot")
    if snapshot.verified_candidates:
        candidate = snapshot.verified_candidates[0]
        sha = candidate.get("candidate_sha")
        mode = candidate.get("actuation_mode", "shadow")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("verified candidate requires a full lowercase SHA")
        if mode not in {"shadow", "local", "public"}:
            raise ValueError("verified candidate has invalid actuation mode")
        return Recommendation(
            source="close_the_loop",
            action=CloseLoopAction(
                "merge_verified_branch", str(candidate.get("source_task_id")), sha, mode,
            ),
            rationale="A verified exact-SHA candidate is waiting at the merge gate.",
            cost_estimate=1.0,
            requires_human=mode == "public",
        )
    if snapshot.admitted_tasks and not snapshot.active_work_ids:
        task_id = snapshot.admitted_tasks[0].get("source_task_id")
        if task_id is None or not str(task_id).strip():
            raise ValueError("admitted task requires source_task_id")
        return Recommendation(
            source="close_the_loop",
            action=CloseLoopAction("pull_task", str(task_id)),
            rationale="An admitted queue item is ready and no close-loop work is active.",
            cost_estimate=2.0,
            requires_human=False,
        )
    return Recommendation(
        source="close_the_loop",
        action=CloseLoopAction("none"),
        rationale="No verified candidate or safely dispatchable admitted task is available.",
        cost_estimate=0.0,
        requires_human=False,
    )
