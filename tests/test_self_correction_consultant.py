from dataclasses import FrozenInstanceError, asdict
from decimal import Decimal

import pytest

from runner.consultants.seam import ConsultantAction, ConsultantPass, Recommendation
from runner.consultants.self_correction import (
    LEARNING_TYPE_ID,
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
from runner.meta_mode import MetaMode, POLICY_VERSION


def _value(*, useful: bool = True) -> AuditValue:
    return AuditValue(
        direct_value=Decimal("1") if useful else Decimal("0"),
        information_value=Decimal("2") if useful else Decimal("0"),
        cost=Decimal("1"),
        evidence_ref="fixture:bounded-audit-value",
    )


def _principle(
    principle_id: str,
    current: str = "safe",
    validated: str | None = "safe",
    *,
    rule: str = "rule-taxonomy-v1",
) -> PrincipleHypothesis:
    return PrincipleHypothesis(
        principle_id,
        rule,
        current,
        validated,
        f"review:{principle_id}" if validated is not None else None,
    )


def _gap_snapshot() -> SelfCorrectionSnapshot:
    return SelfCorrectionSnapshot(
        correction=Correction("alpha", "rule-taxonomy-v1", "safe", "unsafe"),
        principles=(
            _principle("alpha", validated="unsafe"),
            _principle("beta", validated="unsafe"),
            _principle("gamma", validated="unsafe"),
            _principle("other-rule", validated="unsafe", rule="rule-independent"),
        ),
        audit_value=_value(),
    )


def _fine_snapshot() -> SelfCorrectionSnapshot:
    return SelfCorrectionSnapshot(
        correction=Correction("alpha", "rule-taxonomy-v1", "safe", "unsafe"),
        principles=(
            _principle("alpha", validated="unsafe"),
            _principle("beta"),
            _principle("gamma"),
        ),
        audit_value=_value(),
    )


def test_real_gap_reopens_only_same_rule_siblings_and_names_suspects():
    result = consult(_gap_snapshot())
    assert isinstance(result, Recommendation)
    assert result.source == "self_correction"
    assert result.action == ConsultantAction.SIBLING_AUDIT
    assert "suspect siblings [beta, gamma]" in result.rationale
    assert "other-rule" not in result.rationale
    assert result.requires_human is False


def test_fine_siblings_are_a_contentful_negative_not_permanent_anxiety():
    result = consult(_fine_snapshot())
    assert isinstance(result, ConsultantPass)
    assert "all siblings validated, no change" in result.rationale
    assert "beta, gamma" in result.rationale


def test_equivalence_class_is_rule_edge_identity_not_collection_proximity():
    siblings = sibling_hypotheses(_gap_snapshot())
    assert [s.principle_id for s in siblings] == ["beta", "gamma"]


def test_voi_governor_can_abstain_when_audit_is_not_worth_cost():
    snapshot = SelfCorrectionSnapshot(
        correction=_fine_snapshot().correction,
        principles=_fine_snapshot().principles,
        audit_value=_value(useful=False),
    )
    decision = audit_voi_decision(snapshot.audit_value)
    assert decision.policy_version == POLICY_VERSION
    assert decision.chosen_mode == MetaMode.ABSTAIN
    result = consult(snapshot)
    assert isinstance(result, ConsultantPass)
    assert "not useful" in result.rationale


def test_meta_learning_fires_on_new_matching_situation_not_specific_rule():
    learned = classify_correction(
        Correction("old-item", "old-rule", "class-a", "class-b")
    )
    assert learned is not None
    assert learned.learning_type_id == LEARNING_TYPE_ID
    # New collection and rule: only the reusable TYPE matches.
    assert learned.fires_on(
        ReferenceEvent(ReferenceKind.COLLECTION_MEMBER, has_shared_generating_rule=True)
    )


def test_meta_learning_does_not_fire_on_nonmatch_or_unrelated_members():
    learned = classify_correction(
        Correction("old-item", "old-rule", "class-a", "class-b")
    )
    assert learned is not None
    assert not learned.fires_on(
        ReferenceEvent(ReferenceKind.STANDALONE_ITEM, has_shared_generating_rule=False)
    )
    assert not learned.fires_on(
        ReferenceEvent(ReferenceKind.COLLECTION_MEMBER, has_shared_generating_rule=False)
    )
    assert classify_correction(
        Correction(
            "solo", "unused-rule", "class-a", "class-b",
            reference_kind=ReferenceKind.STANDALONE_ITEM,
        )
    ) is None


def test_unvalidated_sibling_requests_human_without_calling_it_wrong():
    snapshot = SelfCorrectionSnapshot(
        correction=_fine_snapshot().correction,
        principles=(
            _principle("alpha", validated="unsafe"),
            _principle("beta", validated=None),
        ),
        audit_value=_value(),
    )
    result = consult(snapshot)
    assert isinstance(result, Recommendation)
    assert result.requires_human is True
    assert "suspect siblings [none]" in result.rationale
    assert "unvalidated siblings [beta]" in result.rationale


def test_no_correction_and_no_siblings_are_explicit_negative_results():
    absent = SelfCorrectionSnapshot(None, (), _value())
    assert "no human correction" in consult(absent).rationale
    alone = SelfCorrectionSnapshot(
        Correction("alpha", "rule", "a", "b"), (_principle("alpha", rule="rule"),), _value()
    )
    assert "no sibling hypotheses" in consult(alone).rationale


def test_shared_seam_shape_is_exact_and_cost_is_governed_input():
    result = consult(_gap_snapshot())
    assert isinstance(result, Recommendation)
    assert set(asdict(result)) == {
        "source", "action", "rationale", "cost_estimate", "requires_human"
    }
    assert result.cost_estimate == Decimal("1")


def test_consultation_is_deterministic_and_snapshot_is_read_only():
    snapshot = _gap_snapshot()
    assert consult(snapshot) == consult(snapshot)
    with pytest.raises(FrozenInstanceError):
        snapshot.correction = None
    with pytest.raises(FrozenInstanceError):
        snapshot.principles[0].classification = "mutated"


def test_content_validation_requires_evidence_not_just_existence():
    with pytest.raises(ValueError, match="evidence_ref"):
        PrincipleHypothesis("beta", "rule", "safe", "unsafe", None)


def test_correction_that_does_not_change_content_is_rejected():
    with pytest.raises(ValueError, match="must change"):
        Correction("alpha", "rule", "same", "same")
