"""Task #4407 — manager-gated cohort→main decision packets."""

from ralph_portable.manager_merge_gate import validate_manager_merge_decision


def test_approve_merge_ok() -> None:
    errors = validate_manager_merge_decision(
        {
            "decision": "approve_merge",
            "manager_handle": "manager-primary",
            "cohort_id": "cohort-42",
            "rationale": "Integration tests green; no material risk noted.",
        }
    )
    assert errors == []


def test_escalate_requires_uncertainty_note() -> None:
    errors = validate_manager_merge_decision(
        {
            "decision": "escalate_human",
            "manager_handle": "manager-primary",
            "cohort_id": "cohort-42",
            "rationale": "Schema migration looks borderline.",
        }
    )
    assert any("uncertainty_note" in e for e in errors)


def test_escalate_ok() -> None:
    errors = validate_manager_merge_decision(
        {
            "decision": "escalate_human",
            "manager_handle": "manager-primary",
            "cohort_id": "cohort-42",
            "rationale": "Schema migration looks borderline.",
            "uncertainty_note": "Unsure whether AGE migration is reversible.",
        }
    )
    assert errors == []


def test_rejects_operator_override() -> None:
    errors = validate_manager_merge_decision(
        {
            "decision": "approve_merge",
            "manager_handle": "manager-primary",
            "cohort_id": "cohort-42",
            "rationale": "Looks fine.",
            "operator_override": True,
        }
    )
    assert any("operator_override" in e for e in errors)
