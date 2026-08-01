"""Manager-gated cohort→main decision packet (Task #4407).

Doctrine: after integration cohort tests pass, a coding-agent manager reviews.
On clear pass → approve_merge. On close calls / uncertainty → escalate_human.
Not operator-gated by default.
"""

from __future__ import annotations

from typing import Any

ALLOWED_DECISIONS = frozenset({"approve_merge", "escalate_human"})


def validate_manager_merge_decision(packet: Any) -> list[str]:
    """Return validation errors for a manager merge decision packet."""
    if not isinstance(packet, dict):
        return ["manager_merge_decision must be a mapping"]

    errors: list[str] = []
    decision = str(packet.get("decision") or "").strip()
    if decision not in ALLOWED_DECISIONS:
        errors.append(
            "decision must be approve_merge or escalate_human "
            "(manager-gated; not operator_gated)"
        )

    manager = str(packet.get("manager_handle") or "").strip()
    if not manager:
        errors.append("manager_handle is required")

    cohort_id = str(packet.get("cohort_id") or "").strip()
    if not cohort_id:
        errors.append("cohort_id is required")

    rationale = str(packet.get("rationale") or "").strip()
    if len(rationale) < 8:
        errors.append("rationale must be a non-trivial explanation")

    if decision == "escalate_human":
        uncertainty = str(packet.get("uncertainty_note") or "").strip()
        if not uncertainty:
            errors.append(
                "uncertainty_note is required when escalating to human"
            )

    if packet.get("operator_override") is True:
        errors.append(
            "operator_override is not a valid path for cohort→main "
            "(use escalate_human if a human must decide)"
        )

    return errors
