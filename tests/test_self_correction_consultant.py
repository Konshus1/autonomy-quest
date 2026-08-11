from decimal import Decimal

from runner.consultants.seam import ConsultantAction, Recommendation
from runner.consultants.self_correction import (
    AuditValue, Correction, PrincipleHypothesis, SelfCorrectionSnapshot, consult,
)


def _value(*, useful: bool = True) -> AuditValue:
    return AuditValue(
        direct_value=Decimal("1") if useful else Decimal("0"),
        information_value=Decimal("2") if useful else Decimal("0"),
        cost=Decimal("1"),
        evidence_ref="fixture:bounded-audit-value",
    )


def _gap_snapshot() -> SelfCorrectionSnapshot:
    return SelfCorrectionSnapshot(
        correction=Correction("alpha", "rule-taxonomy-v1", "safe", "unsafe"),
        principles=(
            PrincipleHypothesis("alpha", "rule-taxonomy-v1", "safe", "unsafe", "human:alpha"),
            PrincipleHypothesis("beta", "rule-taxonomy-v1", "safe", "unsafe", "review:beta"),
            PrincipleHypothesis("gamma", "rule-taxonomy-v1", "safe", "unsafe", "review:gamma"),
            PrincipleHypothesis("other-rule", "rule-independent", "safe", "unsafe", "review:other"),
        ),
        audit_value=_value(),
    )


def test_real_gap_reopens_only_same_rule_siblings_and_names_suspects():
    result = consult(_gap_snapshot())
    assert isinstance(result, Recommendation)
    assert result.source == "self_correction"
    assert result.action == ConsultantAction.SIBLING_AUDIT
    assert "beta" in result.rationale and "gamma" in result.rationale
    assert "other-rule" not in result.rationale
