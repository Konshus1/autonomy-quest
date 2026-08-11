"""Stage-2 LIVE arbiter for the self-correction consultant (#4834).

Stage 1 (``self_correction_shadow``) only OBSERVED. Stage 2 lets a single ARBITER APPLY at most one
bounded, reversible action per cycle, behind the SEPARATE flag ``AQ_SELF_CORRECTION_LIVE``
(default OFF => complete no-op; the loop behaves exactly as stage 1).

Two layers, kept apart on purpose:

  * ``arbiter(results)`` is a PURE decision. Given the ordered pre-DECIDE consultant chain's
    results, it selects AT MOST ONE action by the fixed priority frozen in
    ``#4834.details.shared_seam_contract``:

        1. a human-gated recommendation ESCALATES (park/notify) — never auto-applied;
        2. else a ``frame_review`` HOLDS routine dispatch this cycle (outranks a routine dispatch);
        3. else a ``sibling_audit`` ENQUEUEs a bounded re-examination request;
        4. else PROCEED to the normal DECIDE.

    It touches no database and no executor; it returns a value the loop applies.

  * ``apply(db, decision, snapshot, ...)`` performs the loop-side effects of the chosen action,
    all bounded and reversible: append-only audit requests (never a disposition change), an
    append-only "records why" row for a hold/escalate, and a human notification for an escalate.
    ``run(...)`` ties snapshot -> consult -> arbiter -> apply together and is FAIL-SAFE: any
    exception degrades to "do not hold dispatch" (fail OPEN to normal behavior, because a
    hold-forever would be a DoS).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable, Sequence

from runner.consultants.seam import (
    ConsultantAction,
    ConsultantResult,
    Recommendation,
)
from runner.consultants.self_correction import (
    SelfCorrectionSnapshot,
    consult,
    sibling_audit_targets,
)
from runner.consultants.self_correction_shadow import build_snapshot

log = logging.getLogger("aq.self_correction.arbiter")

FLAG = "AQ_SELF_CORRECTION_LIVE"
_ENABLED = {"1", "true", "yes", "on"}


def live_enabled(env: dict[str, str] | None = None) -> bool:
    """Stage-2 LIVE apply is OFF unless AQ_SELF_CORRECTION_LIVE is explicitly truthy.

    Deliberately independent of AQ_SELF_CORRECTION_SHADOW: shadow can observe without live ever
    applying, and live is what a human flips to turn the apply on.
    """
    source = env if env is not None else os.environ
    return str(source.get(FLAG) or "").strip().lower() in _ENABLED


class ArbiterOutcome(StrEnum):
    ESCALATE = "escalate"
    HOLD_DISPATCH = "hold_dispatch"
    ENQUEUE_AUDIT = "enqueue_audit"
    PROCEED = "proceed"


@dataclass(frozen=True)
class ArbiterDecision:
    """The single action the arbiter chose this cycle (or PROCEED — no action)."""

    outcome: ArbiterOutcome
    recommendation: Recommendation | None
    rationale: str

    @property
    def holds_dispatch(self) -> bool:
        """Only a frame_review HOLD stops routine mission dispatch for the cycle."""
        return self.outcome is ArbiterOutcome.HOLD_DISPATCH


def _recommendations(results: Sequence[ConsultantResult]) -> list[Recommendation]:
    return [r for r in results if isinstance(r, Recommendation)]


def arbiter(results: Sequence[ConsultantResult]) -> ArbiterDecision:
    """PURE. Pick AT MOST ONE action from the ordered consultant chain by fixed priority.

    The input order is the consultant chain order (self_correction first today; later consultants
    append). Within that order the priority is: any human-gated recommendation escalates; else the
    first frame_review holds dispatch; else the first sibling_audit enqueues an audit; else proceed.
    """
    recs = _recommendations(results)

    # 1. HUMAN-GATED escalates. A recommendation that says it needs a human is never auto-applied,
    #    whatever its action — the human decides.
    for rec in recs:
        if rec.requires_human:
            return ArbiterDecision(
                ArbiterOutcome.ESCALATE, rec,
                f"human-gated {rec.source}/{rec.action.value}: {rec.rationale}",
            )

    # 2. FRAME_REVIEW outranks a routine dispatch: hold this cycle, re-evaluate next.
    for rec in recs:
        if rec.action is ConsultantAction.FRAME_REVIEW:
            return ArbiterDecision(
                ArbiterOutcome.HOLD_DISPATCH, rec,
                f"frame_review holds routine dispatch this cycle: {rec.rationale}",
            )

    # 3. SIBLING_AUDIT enqueues a bounded, reversible re-examination request; dispatch proceeds.
    for rec in recs:
        if rec.action is ConsultantAction.SIBLING_AUDIT:
            return ArbiterDecision(
                ArbiterOutcome.ENQUEUE_AUDIT, rec,
                f"sibling_audit enqueues re-examination requests: {rec.rationale}",
            )

    # 4. Nothing to apply — normal DECIDE.
    return ArbiterDecision(ArbiterOutcome.PROCEED, None, "no applicable recommendation; normal DECIDE")


def _correction_identity(snapshot: SelfCorrectionSnapshot) -> tuple[str | None, Any, str]:
    """(corrected_item_id, corrected_at, stable_correction_token) for idempotency + readability."""
    correction = snapshot.correction
    if correction is None:
        return None, None, ""
    corrected_at = correction.corrected_at
    at_token = corrected_at.isoformat() if corrected_at is not None else "unknown"
    return correction.item_id, corrected_at, f"{correction.item_id}@{at_token}"


def _apply_enqueue_audit(
    db, decision: ArbiterDecision, snapshot: SelfCorrectionSnapshot, work_context: str,
) -> int:
    """Enqueue ONE append-only, idempotent audit request per suspect sibling. Returns the count of
    NEW requests actually inserted (duplicates are suppressed by the UNIQUE key). NEVER changes a
    disposition — an audit request only asks the evaluator/governance to re-examine."""
    correction = snapshot.correction
    item_id, corrected_at, token = _correction_identity(snapshot)
    generating_rule_id = correction.generating_rule_id if correction is not None else None
    targets = sibling_audit_targets(snapshot)
    inserted = 0
    for sibling in targets:
        reason = (
            f"same-rule sibling of corrected principle {item_id}; re-examine after "
            f"{decision.recommendation.action.value if decision.recommendation else 'sibling_audit'}"
        )
        if db.enqueue_self_correction_audit_request(
            principle_id=sibling.principle_id,
            generating_rule_id=sibling.generating_rule_id,
            reason=reason,
            source_correction=token,
            source_correction_item_id=item_id,
            source_corrected_at=corrected_at,
            work_context=work_context,
            detail={
                "current_classification": sibling.classification,
                "validated_classification": sibling.validated_classification,
            },
        ):
            inserted += 1
    return inserted


def apply(
    db,
    decision: ArbiterDecision,
    snapshot: SelfCorrectionSnapshot | None,
    *,
    work_context: str,
    notify: Callable[[str, str], None] | None = None,
) -> bool:
    """Apply the arbiter's chosen action. Returns True iff routine dispatch is HELD this cycle.

    PROCEED does nothing. ESCALATE records the action and notifies the human (never auto-applies).
    ENQUEUE_AUDIT enqueues the bounded audit requests and records how many were opened. HOLD_DISPATCH
    records why and returns True. Every write here is append-only; none is a disposition change.
    """
    if decision.outcome is ArbiterOutcome.PROCEED or snapshot is None:
        return False

    correction = snapshot.correction
    item_id = correction.item_id if correction is not None else None
    rule = correction.generating_rule_id if correction is not None else None

    if decision.outcome is ArbiterOutcome.ENQUEUE_AUDIT:
        count = _apply_enqueue_audit(db, decision, snapshot, work_context)
        db.record_self_correction_arbiter_action(
            work_context=work_context, outcome="enqueue_audit",
            action=(decision.recommendation.action.value if decision.recommendation else None),
            held_dispatch=False, correction_item_id=item_id, generating_rule_id=rule,
            reason=decision.rationale, audit_request_count=count,
            detail={"new_requests": count},
        )
        return False

    if decision.outcome is ArbiterOutcome.ESCALATE:
        db.record_self_correction_arbiter_action(
            work_context=work_context, outcome="escalate",
            action=(decision.recommendation.action.value if decision.recommendation else None),
            held_dispatch=False, correction_item_id=item_id, generating_rule_id=rule,
            reason=decision.rationale, audit_request_count=0, detail={},
        )
        if notify is not None:
            notify(
                "[autonomy-quest] self-correction needs a human decision",
                f"A governance correction on principle {item_id} produced a recommendation that "
                f"requires human judgement and was NOT auto-applied:\n\n{decision.rationale}\n\n"
                f"The loop continues its mission work normally; nothing was reopened or changed.",
            )
        return False

    # HOLD_DISPATCH — bounded to this cycle, recorded, no other side effect.
    db.record_self_correction_arbiter_action(
        work_context=work_context, outcome="hold_dispatch",
        action=(decision.recommendation.action.value if decision.recommendation else None),
        held_dispatch=True, correction_item_id=item_id, generating_rule_id=rule,
        reason=decision.rationale, audit_request_count=0, detail={"bounded_to_cycle": True},
    )
    return True


def run(db, *, work_context: str, window: str | None = None,
        notify: Callable[[str, str], None] | None = None) -> bool:
    """FAIL-SAFE. snapshot -> consult -> arbiter -> apply. Returns True iff dispatch is HELD.

    Any exception anywhere degrades to False (fail OPEN to normal dispatch): a snapshot/consult
    error, an apply-write error, or a hold-recording error must never leave the loop stuck or in a
    bad partial state. A frame_review HOLD is only returned once it has been safely recorded.
    """
    try:
        snapshot = build_snapshot(db) if window is None else build_snapshot(db, window=window)
    except Exception:
        log.warning("self-correction live: snapshot build failed (non-fatal, fail-open)", exc_info=True)
        return False
    if snapshot is None:
        return False
    try:
        result = consult(snapshot)
    except Exception:
        log.warning("self-correction live: consult failed (non-fatal, fail-open)", exc_info=True)
        return False
    try:
        decision = arbiter([result])
    except Exception:
        log.warning("self-correction live: arbiter failed (non-fatal, fail-open)", exc_info=True)
        return False
    try:
        return apply(db, decision, snapshot, work_context=work_context, notify=notify)
    except Exception:
        # A hold that errors falls back to normal dispatch; an enqueue/escalate that errors simply
        # did not happen. Never hold-forever, never a bad partial state.
        log.warning("self-correction live: apply failed (non-fatal, fail-open to normal dispatch)",
                    exc_info=True)
        return False
