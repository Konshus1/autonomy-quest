"""Pure counterexample-generalization consultant for the pre-DECIDE seam.

A human correction is treated as evidence about the rule that generated the
item.  Other graph nodes carrying the same generating-rule identity are reopened
as sibling hypotheses.  The existing impasse meta-mode governor decides whether
the bounded audit is worth its incremental cost.  This module only reads frozen
values and returns a value; it cannot apply the correction or mutate the graph.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from runner.consultants.seam import (
    ConsultantAction,
    ConsultantPass,
    ConsultantResult,
    Recommendation,
)
from runner.meta_mode import MetaMode, MetaModeDecision, choose_meta_mode

LEARNING_TYPE_ID = "counterexample_generalization.collection_member.v1"


class ReferenceKind(StrEnum):
    COLLECTION_MEMBER = "collection_member"
    STANDALONE_ITEM = "standalone_item"


@dataclass(frozen=True)
class Correction:
    item_id: str
    generating_rule_id: str
    old_classification: str
    corrected_classification: str
    reference_kind: ReferenceKind = ReferenceKind.COLLECTION_MEMBER

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.item_id,
                self.generating_rule_id,
                self.old_classification,
                self.corrected_classification,
            )
        ):
            raise ValueError("correction fields must be non-empty")
        if self.old_classification == self.corrected_classification:
            raise ValueError("a correction must change the classification")


@dataclass(frozen=True)
class PrincipleHypothesis:
    """Read-only projection of schema/009+ causal-principle graph state.

    ``generating_rule_id`` is the implicated rule/edge identity that defines the
    equivalence class. ``validated_classification`` is separately observed audit
    evidence, not a mutation target.  ``None`` means the sibling is unresolved.
    """

    principle_id: str
    generating_rule_id: str
    classification: str
    validated_classification: str | None
    evidence_ref: str | None = None

    def __post_init__(self) -> None:
        if not self.principle_id.strip() or not self.generating_rule_id.strip():
            raise ValueError("principle and generating-rule identities are required")
        if not self.classification.strip():
            raise ValueError("principle classification is required")
        if self.validated_classification is not None and not self.validated_classification.strip():
            raise ValueError("validated classification cannot be blank")
        if self.validated_classification is not None and not (self.evidence_ref or "").strip():
            raise ValueError("validated content requires an evidence_ref")


@dataclass(frozen=True)
class AuditValue:
    """Coarse utility bands for the existing impasse VoI governor (0..4)."""

    direct_value: Decimal
    information_value: Decimal
    cost: Decimal
    evidence_ref: str

    def __post_init__(self) -> None:
        values = (self.direct_value, self.information_value, self.cost)
        if any(not value.is_finite() or value < 0 or value > 4 for value in values):
            raise ValueError("audit utility inputs must be finite values in the 0..4 rubric")
        if not self.evidence_ref.strip():
            raise ValueError("audit value requires evidence provenance")


@dataclass(frozen=True)
class SelfCorrectionSnapshot:
    """Caller-built, immutable world-state snapshot for one consultation."""

    correction: Correction | None
    principles: tuple[PrincipleHypothesis, ...]
    audit_value: AuditValue

    def __post_init__(self) -> None:
        ids = [principle.principle_id for principle in self.principles]
        if len(ids) != len(set(ids)):
            raise ValueError("snapshot principle identities must be unique")


@dataclass(frozen=True)
class ReferenceEvent:
    """A later situation tested against a learned correction type."""

    reference_kind: ReferenceKind
    has_shared_generating_rule: bool


@dataclass(frozen=True)
class ReusableLearningType:
    """Reusable trigger-pattern -> response learned from a correction."""

    learning_type_id: str
    trigger_reference_kind: ReferenceKind
    require_shared_generating_rule: bool
    response: ConsultantAction

    def fires_on(self, event: ReferenceEvent) -> bool:
        return (
            event.reference_kind == self.trigger_reference_kind
            and (not self.require_shared_generating_rule or event.has_shared_generating_rule)
        )


def classify_correction(correction: Correction) -> ReusableLearningType | None:
    """Classify an instance correction into Kevin's reusable TYPE.

    The learned trigger intentionally contains no item, collection, or rule ID, so
    it transfers to a new list/member situation. A standalone correction does not
    satisfy the pattern and produces no broad audit rule.
    """
    if correction.reference_kind != ReferenceKind.COLLECTION_MEMBER:
        return None
    return ReusableLearningType(
        learning_type_id=LEARNING_TYPE_ID,
        trigger_reference_kind=ReferenceKind.COLLECTION_MEMBER,
        require_shared_generating_rule=True,
        response=ConsultantAction.SIBLING_AUDIT,
    )


def sibling_hypotheses(snapshot: SelfCorrectionSnapshot) -> tuple[PrincipleHypothesis, ...]:
    """Re-open the exact equivalence class implied by the corrected rule/edge."""
    correction = snapshot.correction
    if correction is None:
        return ()
    return tuple(
        principle
        for principle in snapshot.principles
        if principle.generating_rule_id == correction.generating_rule_id
        and principle.principle_id != correction.item_id
    )


def _bounded_option(mode: MetaMode, value: AuditValue) -> dict[str, object]:
    return {
        "mode": mode.value,
        "state": "bounded",
        "direct_value": {"low": str(value.direct_value), "high": str(value.direct_value)},
        "information_value": {
            "low": str(value.information_value),
            "high": str(value.information_value),
        },
        "cost": {"low": str(value.cost), "high": str(value.cost)},
        "evidence_refs": [value.evidence_ref],
        "rationale": "bounded content audit of hypotheses sharing the corrected rule",
        "instruction": "audit reopened sibling hypotheses",
        "expected_expense_usd": 0,
        "blast_radius_level": 0,
        "reversible": True,
        "spends_money": False,
        "touches_human": False,
        "commits": False,
    }


def _blocked_option(mode: MetaMode) -> dict[str, object]:
    return {
        "mode": mode.value,
        "state": "blocked",
        "direct_value": None,
        "information_value": None,
        "cost": None,
        "evidence_refs": ["self-correction:read-only-consultant-boundary"],
        "rationale": "the pure consultant can perform only an internal snapshot audit",
        "instruction": "",
        "block_reason": "outside the read-only consultant authority",
        "wake_condition": None,
    }


def audit_voi_decision(value: AuditValue) -> MetaModeDecision:
    """Reuse the impasse meta-mode governor; do not invent a second VoI rule."""
    options = [_bounded_option(MetaMode.INTERNAL_COMPUTATION, value)]
    options.append({
        "mode": MetaMode.ABSTAIN.value,
        "state": "bounded",
        "direct_value": {"low": "0", "high": "0"},
        "information_value": {"low": "0", "high": "0"},
        "cost": {"low": "0", "high": "0"},
        "evidence_refs": [value.evidence_ref],
        "rationale": "do not audit when its conservative value does not beat zero",
        "instruction": "",
        "expected_expense_usd": 0,
        "blast_radius_level": 0,
        "reversible": True,
        "spends_money": False,
        "touches_human": False,
        "commits": False,
        "wake_condition": "new correction or lower-cost audit evidence",
    })
    options.extend(
        _blocked_option(mode)
        for mode in MetaMode
        if mode not in {MetaMode.INTERNAL_COMPUTATION, MetaMode.ABSTAIN}
    )
    return choose_meta_mode(options)


def _pass(rationale: str) -> ConsultantPass:
    return ConsultantPass(source="self_correction", rationale=rationale)


def consult(snapshot: SelfCorrectionSnapshot) -> ConsultantResult:
    """Return a sibling-audit recommendation or an explicit negative PASS."""
    correction = snapshot.correction
    if correction is None:
        return _pass("no human correction in this snapshot; no change")

    learning_type = classify_correction(correction)
    if learning_type is None:
        return _pass("correction is not a collection/member trigger; no sibling generalization")

    siblings = sibling_hypotheses(snapshot)
    if not siblings:
        return _pass("corrected rule produced no sibling hypotheses; no change")

    voi = audit_voi_decision(snapshot.audit_value)
    if voi.decision != "acquire" or voi.chosen_mode != MetaMode.INTERNAL_COMPUTATION:
        return _pass(
            "sibling audit not useful under impasse VoI governor "
            f"({voi.stop_reason or voi.decision}); no change"
        )

    suspects = tuple(
        sibling
        for sibling in siblings
        if sibling.validated_classification is not None
        and sibling.validated_classification != sibling.classification
    )
    unresolved = tuple(
        sibling for sibling in siblings if sibling.validated_classification is None
    )
    reopened_ids = ", ".join(sibling.principle_id for sibling in siblings)

    if suspects or unresolved:
        suspect_ids = ", ".join(sibling.principle_id for sibling in suspects) or "none"
        unresolved_ids = ", ".join(sibling.principle_id for sibling in unresolved) or "none"
        return Recommendation(
            source="self_correction",
            action=learning_type.response,
            rationale=(
                f"learned type {learning_type.learning_type_id}; reopened siblings "
                f"[{reopened_ids}]; suspect siblings [{suspect_ids}]; "
                f"unvalidated siblings [{unresolved_ids}]"
            ),
            cost_estimate=snapshot.audit_value.cost,
            requires_human=bool(unresolved),
        )

    return _pass(
        f"learned type {learning_type.learning_type_id}; reopened siblings "
        f"[{reopened_ids}]; all siblings validated, no change"
    )
