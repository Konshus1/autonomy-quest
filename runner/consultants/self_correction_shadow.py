"""Stage-1 SHADOW wiring for the self-correction consultant (#4834).

OBSERVE-ONLY. This module reads the live governed causal-principle tables, assembles a
read-only ``SelfCorrectionSnapshot``, runs the PURE ``self_correction.consult()``, and records
the resulting recommendation to an append-only telemetry log. It NEVER applies the
recommendation, never mutates governance state, and never influences the loop's decision. The
arbiter that may select/apply a recommendation is STAGE 2 and is deliberately NOT built here.

Mapping from the governed principle tables (schema/010,021,022,024) to the consultant model:

  * principle           -> a (cause, effect, scope) transition lineage in
                           ``causal_principle_transition``.
  * classification      -> the ``to_status`` of the lineage's LATEST transition
                           (provisional | promoted | demoted).
  * generating_rule_id  -> the ``rule_version`` recorded on the lineage's mined transition.
                           Version suffixes collapse via ``normalize_rule_identity``, so v1/v2 of
                           the same rule form one sibling equivalence class.
  * a human CORRECTION  -> the most recent NON-automatic ``promote`` transition: an independent
                           human adjudicator overturned a principle's prior automatic
                           (provisional/demoted) disposition to ``promoted``.
  * validated_*         -> a sibling's OWN most-recent human ``promote`` transition
                           (``validated_classification`` / ``validated_at`` / ``evidence_ref``).

Every read here is SELECT-only; the single write is one append-only telemetry INSERT.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from runner.consultants.seam import ConsultantResult, Recommendation
from runner.consultants.self_correction import (
    AuditValue,
    Correction,
    PrincipleHypothesis,
    ReferenceKind,
    SelfCorrectionSnapshot,
    consult,
    sibling_hypotheses,
)

log = logging.getLogger("aq.self_correction.shadow")

FLAG = "AQ_SELF_CORRECTION_SHADOW"
_ENABLED = {"1", "true", "yes", "on"}
DEFAULT_WINDOW = "30 days"


def shadow_enabled(env: dict[str, str] | None = None) -> bool:
    """The stage-1 shadow is OFF unless AQ_SELF_CORRECTION_SHADOW is explicitly truthy."""
    source = env if env is not None else os.environ
    return str(source.get(FLAG) or "").strip().lower() in _ENABLED


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _audit_value(rule: str, principle_count: int) -> AuditValue:
    """A conservative bounded-audit value in the 0..4 impasse rubric.

    The information value scales with how many sibling hypotheses share the corrected rule
    (capped at the rubric ceiling); the direct value and cost are a fixed bounded internal audit.
    This only decides whether the shadow would recommend a bounded audit vs a VoI-declined pass —
    it applies nothing.
    """
    return AuditValue(
        direct_value=Decimal("1"),
        information_value=Decimal(str(min(max(principle_count, 1), 4))),
        cost=Decimal("1"),
        evidence_ref=f"self-correction-shadow:governance-rule:{rule}",
    )


def _principle_from_row(row: dict[str, Any]) -> PrincipleHypothesis:
    validated = row.get("validated_classification")
    return PrincipleHypothesis(
        principle_id=str(row["principle_id"]),
        generating_rule_id=str(row["generating_rule_id"]),
        classification=str(row["classification"]),
        validated_classification=(str(validated) if validated is not None else None),
        evidence_ref=(str(row["evidence_ref"]) if validated is not None else None),
        validated_at=(_aware(row.get("validated_at")) if validated is not None else None),
    )


def build_snapshot(db, *, window: str = DEFAULT_WINDOW) -> SelfCorrectionSnapshot | None:
    """READ-ONLY. Assemble the consultation snapshot from the governed principle tables.

    Returns ``None`` (an honest empty snapshot) when there is no recent human correction or the
    corrected rule minted no principles — in which case the consult is simply skipped.
    """
    correction_row = db.self_correction_recent_correction(window)
    if not correction_row:
        return None
    rule = str(correction_row["rule_version"])
    principle_rows = db.self_correction_rule_principles(rule) or []
    principles = tuple(_principle_from_row(row) for row in principle_rows)
    if not principles:
        return None
    correction = Correction(
        item_id=str(correction_row["principle_id"]),
        generating_rule_id=rule,
        old_classification=str(correction_row["from_status"]),
        corrected_classification=str(correction_row["to_status"]),
        reference_kind=ReferenceKind.COLLECTION_MEMBER,
        corrected_at=_aware(correction_row["corrected_at"]),
    )
    return SelfCorrectionSnapshot(
        correction=correction,
        principles=principles,
        audit_value=_audit_value(rule, len(principles)),
    )


def _no_snapshot_record(work_context: str) -> dict[str, Any]:
    return {
        "work_context": work_context,
        "correction_item_id": None,
        "generating_rule_id": None,
        "result_kind": "no_snapshot",
        "action": None,
        "rationale": "no recent human correction in the governed principle tables; consult skipped",
        "reopened_principle_ids": [],
        "requires_human": False,
        "detail": {},
    }


def _result_record(
    work_context: str, snapshot: SelfCorrectionSnapshot, result: ConsultantResult
) -> dict[str, Any]:
    correction = snapshot.correction
    reopened = [sibling.principle_id for sibling in sibling_hypotheses(snapshot)]
    base = {
        "work_context": work_context,
        "correction_item_id": (correction.item_id if correction else None),
        "generating_rule_id": (correction.generating_rule_id if correction else None),
        "reopened_principle_ids": reopened,
        "detail": {"source": result.source},
    }
    if isinstance(result, Recommendation):
        return {
            **base,
            "result_kind": "recommendation",
            "action": result.action.value,
            "rationale": result.rationale,
            "requires_human": bool(result.requires_human),
        }
    # ConsultantPass — an explicit negative result is evidence, not a missing return.
    return {
        **base,
        "result_kind": "pass",
        "action": None,
        "rationale": result.rationale,
        "requires_human": False,
    }


def _error_record(work_context: str, stage: str) -> dict[str, Any]:
    return {
        "work_context": work_context,
        "correction_item_id": None,
        "generating_rule_id": None,
        "result_kind": "error",
        "action": None,
        "rationale": f"self-correction shadow degraded at {stage}; observed nothing, applied nothing",
        "reopened_principle_ids": [],
        "requires_human": False,
        "detail": {"stage": stage},
    }


def _try_record_error(db, work_context: str, stage: str) -> None:
    """Best-effort error telemetry; its own failure is swallowed (the write may be what broke)."""
    try:
        db.record_self_correction_shadow(**_error_record(work_context, stage))
    except Exception:  # pragma: no cover - error telemetry is itself best-effort
        log.debug("self-correction shadow: could not record error telemetry (non-fatal)", exc_info=True)


def observe(db, *, work_context: str, window: str = DEFAULT_WINDOW) -> dict[str, Any] | None:
    """The fail-safe shadow observation: snapshot -> consult -> append-only telemetry.

    This function NEVER raises. Any failure in the snapshot build, the pure consult, or the
    telemetry write degrades to a logged non-event so the caller's loop continues exactly as if
    the shadow were absent. It returns the recorded telemetry dict, or ``None`` when nothing was
    recorded. It applies nothing.
    """
    try:
        snapshot = build_snapshot(db, window=window)
    except Exception:
        log.warning("self-correction shadow: snapshot build failed (non-fatal)", exc_info=True)
        _try_record_error(db, work_context, "snapshot")
        return None

    if snapshot is None:
        record = _no_snapshot_record(work_context)
    else:
        try:
            result = consult(snapshot)
        except Exception:
            log.warning("self-correction shadow: consult failed (non-fatal)", exc_info=True)
            _try_record_error(db, work_context, "consult")
            return None
        record = _result_record(work_context, snapshot, result)

    try:
        db.record_self_correction_shadow(**record)
    except Exception:
        log.warning("self-correction shadow: telemetry write failed (non-fatal)", exc_info=True)
        return None
    return record
