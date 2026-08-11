#!/usr/bin/env python3
"""Deterministic acceptance proof for task #4834 self-correction rework."""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runner.consultants.seam import ConsultantPass, Recommendation
from runner.consultants.self_correction import (
    AuditValue,
    Correction,
    PrincipleHypothesis,
    ReferenceEvent,
    ReferenceKind,
    SelfCorrectionSnapshot,
    audit_voi_decision,
    classify_correction,
    consult,
    sibling_hypotheses,
)


CORRECTED_AT = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
BEFORE = datetime(2026, 8, 10, 11, 0, tzinfo=timezone.utc)
AFTER = datetime(2026, 8, 10, 13, 0, tzinfo=timezone.utc)


def p(
    pid: str,
    current: str,
    validated: str | None,
    *,
    rule: str = "rule-list-classifier",
    family: str | None = None,
    validated_at: datetime | None = AFTER,
) -> PrincipleHypothesis:
    return PrincipleHypothesis(
        principle_id=pid,
        generating_rule_id=rule,
        classification=current,
        validated_classification=validated,
        evidence_ref=f"fixture:{pid}" if validated is not None else None,
        generating_rule_family_id=family,
        validated_at=validated_at if validated is not None else None,
    )


def value() -> AuditValue:
    return AuditValue(Decimal("1"), Decimal("2"), Decimal("1"), "fixture:voi")


def correction(
    *, rule: str = "rule-list-classifier", family: str | None = None
) -> Correction:
    return Correction(
        "one", rule, "safe", "unsafe",
        generating_rule_family_id=family, corrected_at=CORRECTED_AT,
    )


def snapshot(*, gap: bool) -> SelfCorrectionSnapshot:
    sibling_current = "safe" if gap else "unsafe"
    sibling_validated = "unsafe" if gap else "unsafe"
    return SelfCorrectionSnapshot(
        correction(),
        (
            p("one", "unsafe", "unsafe"),
            p("two", sibling_current, sibling_validated),
            p("three", sibling_current, sibling_validated),
            p("non-sibling", "safe", "unsafe", rule="another-rule"),
        ),
        value(),
    )


def main() -> None:
    gap_result = consult(snapshot(gap=True))
    fine_result = consult(snapshot(gap=False))
    assert isinstance(gap_result, Recommendation)
    assert "suspect siblings [two, three]" in gap_result.rationale
    assert "non-sibling" not in gap_result.rationale
    assert isinstance(fine_result, ConsultantPass)
    assert "all siblings validated, no change" in fine_result.rationale

    stale = consult(SelfCorrectionSnapshot(
        correction(),
        (p("one", "unsafe", "unsafe"), p("two", "safe", "safe", validated_at=BEFORE)),
        value(),
    ))
    unknown_freshness = consult(SelfCorrectionSnapshot(
        correction(),
        (p("one", "unsafe", "unsafe"), p("two", "safe", "safe", validated_at=None)),
        value(),
    ))
    newer = consult(SelfCorrectionSnapshot(
        correction(),
        (p("one", "unsafe", "unsafe"), p("two", "safe", "safe", validated_at=AFTER)),
        value(),
    ))
    genuine_negative = consult(SelfCorrectionSnapshot(
        correction(),
        (p("one", "unsafe", "unsafe"), p("two", "unsafe", "unsafe", validated_at=BEFORE)),
        value(),
    ))
    assert isinstance(stale, Recommendation) and "suspect siblings [two]" in stale.rationale
    assert isinstance(unknown_freshness, Recommendation)
    assert "suspect siblings [two]" in unknown_freshness.rationale
    assert isinstance(newer, ConsultantPass)
    assert isinstance(genuine_negative, ConsultantPass)

    versioned_snapshot = SelfCorrectionSnapshot(
        correction(rule="rule-list-classifier.v2"),
        (
            p("one", "unsafe", "unsafe", rule="RULE-LIST-CLASSIFIER.V1"),
            p("two", "safe", "safe", rule="rule-list-classifier.v3", validated_at=BEFORE),
        ),
        value(),
    )
    versioned = consult(versioned_snapshot)
    assert isinstance(versioned, Recommendation)
    assert [item.principle_id for item in sibling_hypotheses(versioned_snapshot)] == ["two"]

    renamed_snapshot = SelfCorrectionSnapshot(
        correction(rule="renamed-classifier", family="list-safety"),
        (
            p("one", "unsafe", "unsafe", rule="renamed-classifier", family="list-safety"),
            p(
                "two", "safe", "safe", rule="copied-classifier",
                family="LIST-SAFETY.v2", validated_at=BEFORE,
            ),
        ),
        value(),
    )
    assert [item.principle_id for item in sibling_hypotheses(renamed_snapshot)] == ["two"]

    mismatch = consult(SelfCorrectionSnapshot(
        correction(),
        (
            p("one", "unsafe", "unsafe", rule="different-rule"),
            p("two", "safe", "safe", validated_at=BEFORE),
        ),
        value(),
    ))
    assert isinstance(mismatch, Recommendation) and mismatch.requires_human
    assert "class unknown / cannot enumerate siblings" in mismatch.rationale

    learned = classify_correction(Correction(
        "old", "rule-list-classifier.v1", "safe", "unsafe", corrected_at=BEFORE
    ))
    unrelated = classify_correction(Correction(
        "old", "rule-list-classifier.v1", "draft", "published", corrected_at=BEFORE
    ))
    assert learned is not None and unrelated is not None
    assert learned.learning_type_id != unrelated.learning_type_id
    new_match = learned.fires_on(ReferenceEvent(
        ReferenceKind.COLLECTION_MEMBER,
        "RULE-LIST-CLASSIFIER.v9",
        "safe",
        "unsafe",
        True,
    ))
    non_match = learned.fires_on(ReferenceEvent(
        ReferenceKind.COLLECTION_MEMBER,
        "rule-list-classifier.v9",
        "draft",
        "published",
        True,
    ))
    production_match = consult(SelfCorrectionSnapshot(
        correction(rule="RULE-LIST-CLASSIFIER.v9"),
        (
            p("one", "unsafe", "unsafe", rule="rule-list-classifier.v8"),
            p("two", "safe", "safe", rule="rule-list-classifier.v10", validated_at=BEFORE),
        ),
        value(),
        learned_types=(unrelated, learned),
    ))
    assert new_match and not non_match
    assert isinstance(production_match, Recommendation)
    assert f"matched learned type {learned.learning_type_id}" in production_match.rationale

    voi = audit_voi_decision(snapshot(gap=True).audit_value)
    packet = {
        "controls": {
            "A1_stale_agreeing_validation": "recommendation",
            "A2_unknown_validation_order": "recommendation",
            "A3_strictly_newer_validation": "PASS",
            "A4_no_overturned_label": "PASS",
            "A5_case_version_rule_identity": "recommendation",
            "A6_declared_rename_family": ["two"],
            "A7_corrected_record_mismatch": "class unknown / cannot enumerate siblings",
            "A8_content_driven_production_match": production_match.action.value,
        },
        "fine_siblings": {
            "outcome": "PASS",
            "rationale": fine_result.rationale,
        },
        "meta_learning": {
            "type": learned.learning_type_id,
            "unrelated_type": unrelated.learning_type_id,
            "fires_on_new_match": new_match,
            "fires_on_non_match": non_match,
            "production_consumer": "consult",
        },
        "real_gap": {
            "action": gap_result.action.value,
            "outcome": "recommendation",
            "rationale": gap_result.rationale,
        },
        "red_first": {
            "base_sha": "4a32c4b",
            "red_test_commit": "b7b4fb8",
            "command": "pytest -q tests/test_self_correction_consultant.py",
            "exit": 1,
            "result": "A1-A8 all failed before production changes",
        },
        "shared_seam": {
            "recommendation_fields": list(asdict(gap_result)),
            "source": gap_result.source,
        },
        "voi_governor": {
            "policy_version": voi.policy_version,
            "decision": voi.decision,
            "chosen_mode": voi.chosen_mode.value if voi.chosen_mode else None,
        },
    }
    print(json.dumps(packet, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
