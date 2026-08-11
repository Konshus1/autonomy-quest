"""The shared, mutation-free seam between pre-DECIDE consultants and the loop.

A consultant receives a caller-built, read-only snapshot and returns one value.
It has no database or executor handle.  The future arbiter is the only component
allowed to select/apply a recommendation.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TypeAlias


class ConsultantAction(StrEnum):
    FRAME_REVIEW = "frame_review"
    SIBLING_AUDIT = "sibling_audit"
    PULL_TASK = "pull_task"
    MERGE_VERIFIED_BRANCH = "merge_verified_branch"


@dataclass(frozen=True)
class Recommendation:
    """Exact shared-seam recommendation shape frozen in task #4834."""

    source: str
    action: ConsultantAction
    rationale: str
    cost_estimate: Decimal
    requires_human: bool

    def __post_init__(self) -> None:
        if self.source not in {"self_correction", "close_the_loop"}:
            raise ValueError("unknown consultant source")
        if not self.rationale.strip():
            raise ValueError("recommendation rationale must be substantive")
        if not self.cost_estimate.is_finite() or self.cost_estimate < 0:
            raise ValueError("cost_estimate must be finite and non-negative")


@dataclass(frozen=True)
class ConsultantPass:
    """An explicit negative result; PASS is evidence, not a missing return."""

    source: str
    rationale: str

    def __post_init__(self) -> None:
        if self.source not in {"self_correction", "close_the_loop"}:
            raise ValueError("unknown consultant source")
        if not self.rationale.strip():
            raise ValueError("PASS requires an inspectable rationale")


ConsultantResult: TypeAlias = Recommendation | ConsultantPass
