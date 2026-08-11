"""Stage-2 LIVE arbiter tests for the self-correction consultant (#4834).

RED-FIRST. The load-bearing properties under review:

  * Flag OFF (AQ_SELF_CORRECTION_LIVE unset) => the arbiter apply is a COMPLETE no-op: snapshot
    never read, consult never called, nothing enqueued/recorded, loop identical to stage-1.
  * Flag ON + sibling_audit => an append-only audit-request row per suspect sibling; NO principle
    transition; disposition unchanged; idempotent (same correction twice => no duplicate).
  * Flag ON + frame_review => routine dispatch HELD this cycle (no work created, no DECIDE call),
    recorded, and not permanently blocked (a later cycle dispatches).
  * requires_human => ESCALATE (notify), never auto-applied.
  * Any exception in arbiter/apply => fail-safe: normal DECIDE, no partial state, no held-forever.

The pg-level grant proof (aq_loop cannot write a principle transition; the migration + append-only
enforcement) lives in tests/test_self_correction_arbiter_pg.py.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from runner.consultants import self_correction_arbiter as arb
from runner.consultants.seam import ConsultantAction, ConsultantPass, Recommendation
from runner.consultants.self_correction import (
    Correction,
    PrincipleHypothesis,
    ReferenceKind,
    SelfCorrectionSnapshot,
    consult,
    sibling_audit_targets,
)
from runner.consultants.self_correction_shadow import _audit_value
from runner.loop import Loop

from tests.test_consult_act_wiring import DecideExecutor, ParkDb, inst_external

CORRECTED_AT = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
AFTER = datetime(2026, 8, 10, 13, 0, tzinfo=timezone.utc)
RULE = "aq-governed-principle-v1"


def _correction_row(rule=RULE):
    return {
        "principle_id": 100,
        "from_status": "provisional",
        "to_status": "promoted",
        "rule_version": rule,
        "corrected_at": CORRECTED_AT,
    }


def _principle_row(pid, classification, validated=None, validated_at=AFTER, rule=RULE):
    return {
        "principle_id": pid,
        "generating_rule_id": rule,
        "classification": classification,
        "validated_classification": validated,
        "validated_at": (validated_at if validated is not None else None),
        "evidence_ref": (f"overturn:{pid}" if validated is not None else None),
    }


class ArbiterFakeDb(ParkDb):
    """A loop-capable FakeDb exposing the stage-1 shadow READS plus the stage-2 apply WRITES.

    `audit_requests` captures the append-only sibling_audit queue (with the UNIQUE(principle_id,
    source_correction) idempotency enforced in-memory, mirroring ON CONFLICT DO NOTHING).
    `arbiter_actions` captures the append-only "records why" trail. There is deliberately NO
    principle-transition surface here: the apply must never need one.
    """

    def __init__(self, *, correction=None, principles=None,
                 fail_read=False, fail_enqueue=False, fail_record=False):
        super().__init__()
        self._correction = correction
        self._principles = principles or []
        self._fail_read = fail_read
        self._fail_enqueue = fail_enqueue
        self._fail_record = fail_record
        self.audit_requests = []
        self.arbiter_actions = []
        self._audit_keys = set()
        self.correction_read_calls = 0

    # -- stage-1 read surfaces (SELECT-only) --
    def self_correction_recent_correction(self, window="30 days"):
        self.correction_read_calls += 1
        if self._fail_read:
            raise RuntimeError("governance read exploded")
        return copy.deepcopy(self._correction)

    def self_correction_rule_principles(self, rule_version):
        return [copy.deepcopy(r) for r in self._principles
                if r["generating_rule_id"] == rule_version]

    # -- stage-2 append-only write surfaces --
    def enqueue_self_correction_audit_request(self, *, principle_id, generating_rule_id, reason,
                                              source_correction, source_correction_item_id=None,
                                              source_corrected_at=None, work_context=None,
                                              detail=None):
        if self._fail_enqueue:
            raise RuntimeError("enqueue exploded")
        key = (str(principle_id), str(source_correction))
        if key in self._audit_keys:  # ON CONFLICT DO NOTHING
            return False
        self._audit_keys.add(key)
        self.audit_requests.append({
            "principle_id": str(principle_id), "generating_rule_id": str(generating_rule_id),
            "reason": reason, "source_correction": str(source_correction),
            "source_correction_item_id": source_correction_item_id,
            "source_corrected_at": source_corrected_at, "work_context": work_context,
            "detail": detail or {},
        })
        return True

    def record_self_correction_arbiter_action(self, **row):
        if self._fail_record:
            raise RuntimeError("action record exploded")
        self.arbiter_actions.append(row)


def _snapshot(*, correction, principles, learned=()):
    return SelfCorrectionSnapshot(
        correction=correction, principles=tuple(principles),
        audit_value=_audit_value(RULE, len(principles)),
        learned_types=tuple(learned),
    )


# ---------------------------------------------------------------------------
# 1. The PURE arbiter — fixed priority, at most one action.
# ---------------------------------------------------------------------------

def _rec(action, *, requires_human=False, source="self_correction"):
    return Recommendation(source=source, action=action, rationale="r",
                          cost_estimate=Decimal("1"), requires_human=requires_human)


def test_arbiter_pass_only_proceeds():
    d = arb.arbiter([ConsultantPass(source="self_correction", rationale="no change")])
    assert d.outcome is arb.ArbiterOutcome.PROCEED and d.holds_dispatch is False


def test_arbiter_human_gated_escalates_over_everything():
    d = arb.arbiter([
        _rec(ConsultantAction.SIBLING_AUDIT, requires_human=True),
        _rec(ConsultantAction.FRAME_REVIEW),
    ])
    assert d.outcome is arb.ArbiterOutcome.ESCALATE
    assert d.holds_dispatch is False  # escalate never holds mission dispatch


def test_arbiter_frame_review_outranks_sibling_audit():
    d = arb.arbiter([
        _rec(ConsultantAction.SIBLING_AUDIT),
        _rec(ConsultantAction.FRAME_REVIEW),
    ])
    assert d.outcome is arb.ArbiterOutcome.HOLD_DISPATCH and d.holds_dispatch is True


def test_arbiter_sibling_audit_when_alone_enqueues():
    d = arb.arbiter([_rec(ConsultantAction.SIBLING_AUDIT)])
    assert d.outcome is arb.ArbiterOutcome.ENQUEUE_AUDIT and d.holds_dispatch is False


def test_arbiter_applies_at_most_one_action():
    # Two frame_reviews + a sibling_audit still yield exactly one HOLD.
    d = arb.arbiter([
        _rec(ConsultantAction.FRAME_REVIEW),
        _rec(ConsultantAction.FRAME_REVIEW),
        _rec(ConsultantAction.SIBLING_AUDIT),
    ])
    assert d.outcome is arb.ArbiterOutcome.HOLD_DISPATCH


# ---------------------------------------------------------------------------
# 2. apply() — bounded, reversible, never a disposition change.
# ---------------------------------------------------------------------------

def _suspect_snapshot():
    """A suspect sibling (validation disagrees) with NO unresolved sibling => sibling_audit,
    requires_human False => the ENQUEUE_AUDIT path."""
    correction = Correction(
        item_id="100", generating_rule_id=RULE, old_classification="provisional",
        corrected_classification="promoted", reference_kind=ReferenceKind.COLLECTION_MEMBER,
        corrected_at=CORRECTED_AT,
    )
    principles = [
        PrincipleHypothesis("100", RULE, "promoted", "promoted", "overturn:100", validated_at=AFTER),
        PrincipleHypothesis("200", RULE, "provisional", "demoted", "overturn:200", validated_at=AFTER),
    ]
    return _snapshot(correction=correction, principles=principles)


def test_apply_enqueue_audit_writes_one_request_per_suspect_no_transition():
    snap = _suspect_snapshot()
    assert [s.principle_id for s in sibling_audit_targets(snap)] == ["200"]
    db = ArbiterFakeDb()
    decision = arb.arbiter([consult(snap)])
    held = arb.apply(db, decision, snap, work_context="ctx", notify=None)
    assert held is False
    assert len(db.audit_requests) == 1
    req = db.audit_requests[0]
    assert req["principle_id"] == "200"
    assert req["source_correction_item_id"] == "100"
    assert req["generating_rule_id"] == RULE
    # the corrected item is never enqueued as its own sibling.
    assert all(r["principle_id"] != "100" for r in db.audit_requests)
    # a "records why" row was written; NO disposition/transition surface exists on the db at all.
    assert len(db.arbiter_actions) == 1
    act = db.arbiter_actions[0]
    assert act["outcome"] == "enqueue_audit" and act["held_dispatch"] is False
    assert act["audit_request_count"] == 1
    assert not hasattr(db, "record_causal_transition")


def test_apply_enqueue_audit_is_idempotent():
    snap = _suspect_snapshot()
    from runner.consultants.self_correction import consult
    decision = arb.arbiter([consult(snap)])
    db = ArbiterFakeDb()
    arb.apply(db, decision, snap, work_context="ctx")
    arb.apply(db, decision, snap, work_context="ctx")  # same correction twice
    assert len(db.audit_requests) == 1  # no duplicate open request
    # the second action still records that it applied, but opened zero new requests.
    assert [a["audit_request_count"] for a in db.arbiter_actions] == [1, 0]


def test_apply_escalate_notifies_and_never_auto_applies():
    correction = Correction(
        item_id="100", generating_rule_id=RULE, old_classification="provisional",
        corrected_classification="promoted", reference_kind=ReferenceKind.COLLECTION_MEMBER,
        corrected_at=CORRECTED_AT,
    )
    # an UNRESOLVED sibling (never validated) => requires_human True => escalate.
    principles = [
        PrincipleHypothesis("100", RULE, "promoted", "promoted", "overturn:100", validated_at=AFTER),
        PrincipleHypothesis("200", RULE, "provisional", None),
    ]
    snap = _snapshot(correction=correction, principles=principles)
    from runner.consultants.self_correction import consult
    result = consult(snap)
    assert isinstance(result, Recommendation) and result.requires_human is True
    decision = arb.arbiter([result])
    assert decision.outcome is arb.ArbiterOutcome.ESCALATE

    notes = []
    db = ArbiterFakeDb()
    held = arb.apply(db, decision, snap, work_context="ctx",
                     notify=lambda subject, body: notes.append((subject, body)))
    assert held is False
    assert db.audit_requests == []          # NOTHING auto-applied
    assert len(notes) == 1 and "human" in notes[0][1].lower()
    assert db.arbiter_actions[0]["outcome"] == "escalate"


def test_apply_frame_review_holds_dispatch_and_records():
    snap = _suspect_snapshot()
    decision = arb.ArbiterDecision(
        arb.ArbiterOutcome.HOLD_DISPATCH,
        _rec(ConsultantAction.FRAME_REVIEW), "frame_review holds routine dispatch",
    )
    db = ArbiterFakeDb()
    held = arb.apply(db, decision, snap, work_context="ctx")
    assert held is True
    assert db.audit_requests == []  # a hold has no other side effect
    act = db.arbiter_actions[0]
    assert act["outcome"] == "hold_dispatch" and act["held_dispatch"] is True


# ---------------------------------------------------------------------------
# 3. run() fail-safety — every stage raising is non-fatal and fails OPEN.
# ---------------------------------------------------------------------------

def test_run_snapshot_failure_fails_open_to_normal_dispatch():
    db = ArbiterFakeDb(fail_read=True)
    assert arb.run(db, work_context="ctx") is False
    assert db.audit_requests == [] and db.arbiter_actions == []


def test_run_consult_failure_fails_open():
    db = ArbiterFakeDb(correction=_correction_row(),
                       principles=[_principle_row(100, "promoted", validated="promoted"),
                                   _principle_row(200, "provisional", validated="demoted")])
    with patch.object(arb, "consult", side_effect=RuntimeError("consult boom")):
        assert arb.run(db, work_context="ctx") is False
    assert db.arbiter_actions == []


def test_run_apply_write_failure_fails_open_no_hold():
    db = ArbiterFakeDb(correction=_correction_row(),
                       principles=[_principle_row(100, "promoted", validated="promoted"),
                                   _principle_row(200, "provisional", validated="demoted")],
                       fail_enqueue=True)
    # even though the natural result is a sibling_audit, the enqueue explodes -> fail open (False),
    # never a partial hold.
    assert arb.run(db, work_context="ctx") is False


def test_run_frame_review_hold_recording_failure_fails_open():
    """A frame_review HOLD whose record write errors must NOT hold-forever: fail open to False."""
    db = ArbiterFakeDb(correction=_correction_row(),
                       principles=[_principle_row(100, "promoted", validated="promoted"),
                                   _principle_row(200, "provisional", validated="demoted")],
                       fail_record=True)
    frame_rec = _rec(ConsultantAction.FRAME_REVIEW)
    with patch.object(arb, "consult", return_value=frame_rec):
        assert arb.run(db, work_context="ctx") is False


# ---------------------------------------------------------------------------
# 4. Loop integration — the hook BEFORE DECIDE.
# ---------------------------------------------------------------------------

def _enqueue_db():
    return ArbiterFakeDb(
        correction=_correction_row(),
        principles=[_principle_row(100, "promoted", validated="promoted"),
                    _principle_row(200, "provisional", validated="demoted")],
    )


def _run_cycle(db, *, live, patch_consult=None):
    ex = DecideExecutor()
    import runner.consultants.self_correction_arbiter as arbmod
    import runner.prompts as prompts  # noqa: F401
    env = {"AQ_SELF_CORRECTION_LIVE": "1"} if live else {}
    with patch("runner.causal_sync.mgmt_base_url", return_value=None), \
         patch("runner.loop._new_global_plan_id", return_value="urn:uuid:fixed/plan/fixed"), \
         patch.dict("os.environ", env, clear=False):
        if not live:
            import os
            os.environ.pop("AQ_SELF_CORRECTION_LIVE", None)
        cm = patch.object(arbmod, "consult", side_effect=patch_consult) if patch_consult else None
        if cm:
            with cm:
                cycle = Loop(inst_external(), db, ex).cycle()
        else:
            cycle = Loop(inst_external(), db, ex).cycle()
    return cycle, ex


def test_loop_flag_off_is_complete_no_op():
    db = _enqueue_db()
    with patch.object(arb, "consult") as spy, patch.object(arb, "arbiter") as spy_arb:
        cycle, ex = _run_cycle(db, live=False)
    assert cycle is not None                    # normal dispatch
    assert db.correction_read_calls == 0        # snapshot never read
    assert spy.call_count == 0 and spy_arb.call_count == 0
    assert db.audit_requests == [] and db.arbiter_actions == []
    import runner.prompts as prompts
    assert prompts.DECIDE_SCHEMA in ex.schemas  # DECIDE ran normally


def test_loop_flag_on_sibling_audit_enqueues_and_dispatch_proceeds():
    db_on = _enqueue_db()
    cycle_on, ex_on = _run_cycle(db_on, live=True)
    assert cycle_on is not None                 # dispatch STILL proceeds after enqueue
    assert [r["principle_id"] for r in db_on.audit_requests] == ["200"]
    assert db_on.arbiter_actions and db_on.arbiter_actions[0]["outcome"] == "enqueue_audit"

    # zero-behavior-change on dispatch: created/started/completed identical to the flag-off run,
    # the ONLY difference being the append-only audit request + action rows.
    db_off = _enqueue_db()
    cycle_off, ex_off = _run_cycle(db_off, live=False)
    assert cycle_off == cycle_on
    assert ex_off.schemas == ex_on.schemas
    for attr in ("created", "started", "completed", "learned", "recorded", "parked"):
        assert getattr(db_off, attr) == getattr(db_on, attr), f"{attr} diverged on vs off"
    assert db_off.audit_requests == [] and db_off.arbiter_actions == []


def test_loop_flag_on_frame_review_holds_dispatch_no_work_no_decide():
    frame_rec = _rec(ConsultantAction.FRAME_REVIEW)
    db = _enqueue_db()
    cycle, ex = _run_cycle(db, live=True, patch_consult=lambda snap: frame_rec)
    assert cycle is None                        # dispatch HELD this cycle
    assert db.created == [] and db.started == []
    import runner.prompts as prompts
    assert prompts.DECIDE_SCHEMA not in ex.schemas   # no DECIDE model call either
    assert db.arbiter_actions[0]["outcome"] == "hold_dispatch"
    assert db.arbiter_actions[0]["held_dispatch"] is True


def test_loop_frame_review_hold_is_bounded_not_permanent():
    """The hold carries no persistent blocking state: the very next cycle, with the frame_review
    no longer firing, dispatches normally."""
    db1 = _enqueue_db()
    cycle_held, _ = _run_cycle(db1, live=True, patch_consult=lambda snap: _rec(ConsultantAction.FRAME_REVIEW))
    assert cycle_held is None

    db2 = _enqueue_db()  # a fresh cycle where frame_review no longer fires -> normal enqueue+dispatch
    cycle_next, _ = _run_cycle(db2, live=True)
    assert cycle_next is not None


def test_loop_flag_on_requires_human_escalates_not_applied():
    db = ArbiterFakeDb(
        correction=_correction_row(),
        principles=[_principle_row(100, "promoted", validated="promoted"),
                    _principle_row(200, "provisional")],  # unresolved sibling => requires_human
    )
    cycle, ex = _run_cycle(db, live=True)
    assert cycle is not None                    # escalate does not hold mission dispatch
    assert db.audit_requests == []              # nothing auto-applied
    assert db.arbiter_actions and db.arbiter_actions[0]["outcome"] == "escalate"


def test_loop_flag_on_hook_error_fails_open_to_normal_dispatch():
    db = _enqueue_db()
    with patch.object(arb, "run", side_effect=RuntimeError("hook boom")):
        cycle, ex = _run_cycle(db, live=True)
    assert cycle is not None                    # defense-in-depth: the loop still dispatched
