"""Counterexample-generalization self-correction consultant (red-first stub).

This first commit intentionally models the defect under test: it learns only the
corrected item and never propagates to siblings.  The acceptance control must go
red before this is replaced.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from runner.consultants.seam import ConsultantPass, ConsultantResult


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


@dataclass(frozen=True)
class PrincipleHypothesis:
    principle_id: str
    generating_rule_id: str
    classification: str
    validated_classification: str | None
    evidence_ref: str | None = None


@dataclass(frozen=True)
class AuditValue:
    direct_value: Decimal
    information_value: Decimal
    cost: Decimal
    evidence_ref: str


@dataclass(frozen=True)
class SelfCorrectionSnapshot:
    correction: Correction | None
    principles: tuple[PrincipleHypothesis, ...]
    audit_value: AuditValue


def consult(snapshot: SelfCorrectionSnapshot) -> ConsultantResult:
    # Deliberate mutant: instance-only correction, no sibling propagation.
    return ConsultantPass("self_correction", "corrected item learned; no sibling review")
