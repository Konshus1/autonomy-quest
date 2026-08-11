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

from runner.consultants.seam import ConsultantAction, ConsultantPass, Recommendation
from runner.consultants.self_correction import (
    AuditValue,
    Correction,
    PrincipleHypothesis,
    ReferenceEvent,
    ReferenceKind,
    ReusableLearningType,
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

    semantic_number_snapshot = SelfCorrectionSnapshot(
        correction(rule="policy-1"),
        (
            p("one", "unsafe", "unsafe", rule="policy-1"),
            p("two", "safe", "safe", rule="policy-2", validated_at=BEFORE),
        ),
        value(),
    )
    assert sibling_hypotheses(semantic_number_snapshot) == ()

    case_variant_stale = consult(
        SelfCorrectionSnapshot(
            correction(),
            (
                p("one", "unsafe", "unsafe"),
                p("two", "Safe", "Safe", validated_at=BEFORE),
            ),
            value(),
        )
    )
    assert isinstance(case_variant_stale, Recommendation)
    assert "suspect siblings [two]" in case_variant_stale.rationale

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
    persisted = ReusableLearningType(
        learning_type_id="persisted.custom_review.v7",
        trigger_reference_kind=ReferenceKind.COLLECTION_MEMBER,
        trigger_rule_identity=learned.trigger_rule_identity,
        trigger_old_classification="safe",
        trigger_corrected_classification="unsafe",
        require_shared_generating_rule=True,
        response=ConsultantAction.FRAME_REVIEW,
    )
    production_principles = (
        p("one", "unsafe", "unsafe", rule="rule-list-classifier.v8"),
        p("two", "safe", "safe", rule="rule-list-classifier.v10", validated_at=BEFORE),
    )
    production_without_prior = consult(
        SelfCorrectionSnapshot(
            correction(rule="RULE-LIST-CLASSIFIER.v9"),
            production_principles,
            value(),
        )
    )
    production_match = consult(
        SelfCorrectionSnapshot(
            correction(rule="RULE-LIST-CLASSIFIER.v9"),
            production_principles,
            value(),
            learned_types=(unrelated, persisted),
        )
    )
    assert new_match and not non_match
    assert persisted.learning_type_id != learned.learning_type_id
    assert isinstance(production_without_prior, Recommendation)
    assert production_without_prior.action == ConsultantAction.SIBLING_AUDIT
    assert isinstance(production_match, Recommendation)
    assert production_match.action == ConsultantAction.FRAME_REVIEW
    assert f"matched learned type {persisted.learning_type_id}" in production_match.rationale
    assert production_match != production_without_prior

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
            "A8_behaviorally_necessary_prior_type": {
                "without_prior": production_without_prior.action.value,
                "with_prior": production_match.action.value,
            },
            "R2_case_variant_stale_label": "recommendation",
            "R2_explicit_version_siblings": ["two"],
            "R2_semantic_bare_numeric_ids": [],
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
            "persisted_type": persisted.learning_type_id,
            "persisted_type_changes_output": production_match != production_without_prior,
        },
        "real_gap": {
            "action": gap_result.action.value,
            "outcome": "recommendation",
            "rationale": gap_result.rationale,
        },
        "red_first": {
            "base_sha": "e471110",
            "red_test_commit": "e7c03b5",
            "command": (
                "pytest -q tests/test_self_correction_consultant.py "
                "-k 'r2_bare_numeric or r2_case_variant or r2_prior'"
            ),
            "exit": 1,
            "result": "2 failed, 1 passed before round-2 production changes",
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
