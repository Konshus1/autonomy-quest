"""Shared approval invariants.

Approval changes authorization, not reality. These checks are deliberately consequence-free:
they inspect the approved work shape and either accept it or raise before any act can run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


class ApprovalInvalid(RuntimeError):
    """Approved work is not in the only state that may execute from a human approval."""


@dataclass(frozen=True)
class Approval:
    work_id: int
    summary: str = ""


def _get(row: Mapping[str, Any] | Any, key: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def assert_valid_approval(row: Mapping[str, Any] | Any) -> Approval:
    """Validate an approved work row without mutating anything.

    The only executable approval shape is:
      * an existing work id,
      * status == "pending",
      * approved_at is present.

    Anything else is either unapproved work, already-running work, completed work, or a stale UI
    event. The UI and the runner both call this function so approval safety is structural.
    """
    if row is None:
        raise ApprovalInvalid("approval row is missing")

    work_id = _get(row, "id")
    if work_id is None:
        raise ApprovalInvalid("approval row has no work id")

    status = _get(row, "status")
    if status != "pending":
        raise ApprovalInvalid(f"work #{work_id} is not pending after approval (status={status!r})")

    if _get(row, "approved_at") is None:
        raise ApprovalInvalid(f"work #{work_id} has no approved_at timestamp")

    return Approval(work_id=int(work_id), summary=str(_get(row, "summary", "") or ""))
