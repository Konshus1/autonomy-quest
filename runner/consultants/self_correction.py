"""Pure counterexample-generalization consultant for the pre-DECIDE seam.

A human correction is treated as evidence about the rule that generated the
item.  Other graph nodes carrying the same generating-rule identity are reopened
as sibling hypotheses.  The existing impasse meta-mode governor decides whether
the bounded audit is worth its incremental cost.  This module only reads frozen
values and returns a value; it cannot apply the correction or mutate the graph.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
import hashlib
import re

from runner.consultants.seam import (
    ConsultantAction,
    ConsultantPass,
    ConsultantResult,
    Recommendation,
)
from runner.meta_mode import MetaMode, MetaModeDecision, choose_meta_mode

LEARNING_TYPE_ID = "counterexample_generalization.collection_member"
_VERSION_SUFFIX = re.compile(
    r"(?:[._-](?:v|rev|version)(?:\.?\d+)+)+$", re.IGNORECASE
)


def normalize_rule_identity(rule_id: str) -> str:
    """Return the stable base identity shared by case/version variants."""
    return _VERSION_SUFFIX.sub("", rule_id.strip().casefold())


def _semantic_rule_identity(rule_id: str, family_id: str | None) -> str:
    return normalize_rule_identity(family_id or rule_id)


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


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
    generating_rule_family_id: str | None = None
    corrected_at: datetime | None = None

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
        if self.generating_rule_family_id is not None and not self.generating_rule_family_id.strip():
            raise ValueError("generating rule family cannot be blank")
        if self.corrected_at is not None:
            _aware(self.corrected_at, "corrected_at")

    @property
    def semantic_rule_identity(self) -> str:
        return _semantic_rule_identity(
            self.generating_rule_id, self.generating_rule_family_id
        )


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
    generating_rule_family_id: str | None = None
    validated_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.principle_id.strip() or not self.generating_rule_id.strip():
            raise ValueError("principle and generating-rule identities are required")
        if not self.classification.strip():
            raise ValueError("principle classification is required")
        if self.validated_classification is not None and not self.validated_classification.strip():
            raise ValueError("validated classification cannot be blank")
        if self.validated_classification is not None and not (self.evidence_ref or "").strip():
            raise ValueError("validated content requires an evidence_ref")
        if self.generating_rule_family_id is not None and not self.generating_rule_family_id.strip():
            raise ValueError("generating rule family cannot be blank")
        if self.validated_at is not None and self.validated_classification is None:
            raise ValueError("validation time requires validated content")
        if self.validated_at is not None:
            _aware(self.validated_at, "validated_at")

    @property
    def semantic_rule_identity(self) -> str:
        return _semantic_rule_identity(
            self.generating_rule_id, self.generating_rule_family_id
        )


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
    learned_types: tuple[ReusableLearningType, ...] = ()

    def __post_init__(self) -> None:
        ids = [principle.principle_id for principle in self.principles]
        if len(ids) != len(set(ids)):
            raise ValueError("snapshot principle identities must be unique")


@dataclass(frozen=True)
class ReferenceEvent:
    """Uncompressed correction content tested against a learned type."""

    reference_kind: ReferenceKind
    generating_rule_id: str
    old_classification: str
    corrected_classification: str
    has_shared_generating_rule: bool
    generating_rule_family_id: str | None = None

    @property
    def semantic_rule_identity(self) -> str:
        return _semantic_rule_identity(
            self.generating_rule_id, self.generating_rule_family_id
        )


@dataclass(frozen=True)
class ReusableLearningType:
    """Reusable trigger-pattern -> response learned from a correction."""

    learning_type_id: str
    trigger_reference_kind: ReferenceKind
    trigger_rule_identity: str
    trigger_old_classification: str
    trigger_corrected_classification: str
    require_shared_generating_rule: bool
    response: ConsultantAction

    def fires_on(self, event: ReferenceEvent) -> bool:
        return (
            event.reference_kind == self.trigger_reference_kind
            and event.semantic_rule_identity == self.trigger_rule_identity
            and event.old_classification.casefold() == self.trigger_old_classification
            and event.corrected_classification.casefold()
            == self.trigger_corrected_classification
            and (not self.require_shared_generating_rule or event.has_shared_generating_rule)
        )


def classify_correction(correction: Correction) -> ReusableLearningType | None:
    """Derive a reusable type from the correction's actual semantic content.

    Item identity is deliberately excluded, while normalized rule family and the
    classification transition remain in the trigger.  This transfers across
    version/case variants and declared renames without turning every collection
    correction into the same contentless learning.
    """
    if correction.reference_kind != ReferenceKind.COLLECTION_MEMBER:
        return None
    signature = "\x1f".join((
        correction.semantic_rule_identity,
        correction.old_classification.casefold(),
        correction.corrected_classification.casefold(),
    ))
    digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:12]
    return ReusableLearningType(
        learning_type_id=f"{LEARNING_TYPE_ID}.{digest}.v1",
        trigger_reference_kind=ReferenceKind.COLLECTION_MEMBER,
        trigger_rule_identity=correction.semantic_rule_identity,
        trigger_old_classification=correction.old_classification.casefold(),
        trigger_corrected_classification=correction.corrected_classification.casefold(),
        require_shared_generating_rule=True,
        response=ConsultantAction.SIBLING_AUDIT,
    )


def sibling_hypotheses(snapshot: SelfCorrectionSnapshot) -> tuple[PrincipleHypothesis, ...]:
    """Re-open the normalized semantic class implied by the corrected rule."""
    correction = snapshot.correction
    if correction is None:
        return ()
    return tuple(
        principle
        for principle in snapshot.principles
        if principle.semantic_rule_identity == correction.semantic_rule_identity
        and principle.principle_id != correction.item_id
    )


def _corrected_item_rule_is_consistent(snapshot: SelfCorrectionSnapshot) -> bool:
    correction = snapshot.correction
    if correction is None:
        return True
    corrected_records = tuple(
        principle
        for principle in snapshot.principles
        if principle.principle_id == correction.item_id
    )
    return (
        len(corrected_records) == 1
        and corrected_records[0].semantic_rule_identity
        == correction.semantic_rule_identity
    )


def _event(correction: Correction, *, has_siblings: bool) -> ReferenceEvent:
    return ReferenceEvent(
        reference_kind=correction.reference_kind,
        generating_rule_id=correction.generating_rule_id,
        old_classification=correction.old_classification,
        corrected_classification=correction.corrected_classification,
        has_shared_generating_rule=has_siblings,
        generating_rule_family_id=correction.generating_rule_family_id,
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

    derived_learning_type = classify_correction(correction)
    if derived_learning_type is None:
        return _pass("correction is not a collection/member trigger; no sibling generalization")

    if not _corrected_item_rule_is_consistent(snapshot):
        return Recommendation(
            source="self_correction",
            action=ConsultantAction.SIBLING_AUDIT,
            rationale=(
                "corrected item's snapshot rule disagrees with the correction; "
                "class unknown / cannot enumerate siblings safely"
            ),
            cost_estimate=snapshot.audit_value.cost,
            requires_human=True,
        )

    siblings = sibling_hypotheses(snapshot)
    if not siblings:
        return _pass("corrected rule produced no sibling hypotheses; no change")

    event = _event(correction, has_siblings=True)
    learning_type = next(
        (
            learned
            for learned in (*snapshot.learned_types, derived_learning_type)
            if learned.fires_on(event)
        ),
        None,
    )
    if learning_type is None:
        return _pass("no learned correction type matched this sibling situation; no change")

    voi = audit_voi_decision(snapshot.audit_value)
    if voi.decision != "acquire" or voi.chosen_mode != MetaMode.INTERNAL_COMPUTATION:
        return _pass(
            "sibling audit not useful under impasse VoI governor "
            f"({voi.stop_reason or voi.decision}); no change"
        )

    def validation_is_strictly_newer(sibling: PrincipleHypothesis) -> bool:
        return (
            correction.corrected_at is not None
            and sibling.validated_at is not None
            and sibling.validated_at > correction.corrected_at
        )

    suspects = tuple(
        sibling
        for sibling in siblings
        if (
            sibling.validated_classification is not None
            and sibling.validated_classification.casefold()
            != sibling.classification.casefold()
        )
        or (
            sibling.classification.casefold()
            == correction.old_classification.casefold()
            and not validation_is_strictly_newer(sibling)
        )
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
                f"matched learned type {learning_type.learning_type_id}; reopened siblings "
                f"[{reopened_ids}]; suspect siblings [{suspect_ids}]; "
                f"unvalidated siblings [{unresolved_ids}]"
            ),
            cost_estimate=snapshot.audit_value.cost,
            requires_human=bool(unresolved),
        )

    return _pass(
        f"matched learned type {learning_type.learning_type_id}; reopened siblings "
        f"[{reopened_ids}]; all siblings validated, no change"
    )
