"""Stage-1 SHADOW wiring tests for the self-correction consultant (#4834).

The load-bearing property under review: the loop's decision and ALL its side effects are
identical with AQ_SELF_CORRECTION_SHADOW on vs off. The only permitted difference is the
append-only telemetry record. These tests assert recorded CONTENT (not mere existence), prove
the zero-behavior-change invariant, prove the flag-off no-op (consult never called), and prove
every failure mode (snapshot/consult/telemetry) is non-fatal.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from runner.consultants import self_correction_shadow as shadow
from runner.consultants.self_correction import ReferenceKind
from runner.executor import Usage
from runner.loop import Loop
from runner import prompts

from tests.test_consult_act_wiring import DecideExecutor, ParkDb, inst_external


CORRECTED_AT = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
AFTER = datetime(2026, 8, 10, 13, 0, tzinfo=timezone.utc)
RULE = "aq-governed-principle-v1"


def _correction_row(rule=RULE):
    # A human promote overturning a provisional disposition of principle id 100.
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
        "evidence_ref": (f"promote:{pid}" if validated is not None else None),
    }


class ShadowFakeDb(ParkDb):
    """A loop-capable FakeDb that also exposes the three stage-1 shadow surfaces.

    `correction` and `principles` are the canned governance reads; `shadow_log` captures the
    append-only telemetry writes. Failure modes are injectable to prove fail-safety.
    """

    def __init__(self, *, correction=None, principles=None,
                 fail_read=False, fail_record=False):
        super().__init__()
        self._correction = correction
        self._principles = principles or []
        self._fail_read = fail_read
        self._fail_record = fail_record
        self.shadow_log = []
        self.correction_read_calls = 0

    def self_correction_recent_correction(self, window="30 days"):
        self.correction_read_calls += 1
        if self._fail_read:
            raise RuntimeError("governance read exploded")
        return copy.deepcopy(self._correction)

    def self_correction_rule_principles(self, rule_version):
        return [copy.deepcopy(r) for r in self._principles if r["generating_rule_id"] == rule_version]

    def record_self_correction_shadow(self, **record):
        if self._fail_record:
            raise RuntimeError("telemetry write exploded")
        self.shadow_log.append(record)


# ---------------------------------------------------------------------------
# 1. snapshot builder + observe() content (no loop) — red-first on content.
# ---------------------------------------------------------------------------

def test_correction_with_suspect_sibling_records_sibling_audit_content():
    db = ShadowFakeDb(
        correction=_correction_row(),
        principles=[
            _principle_row(100, "promoted", validated="promoted"),   # the corrected item
            _principle_row(200, "provisional", validated=None),      # suspect + unresolved sibling
        ],
    )
    record = shadow.observe(db, work_context="ctx")
    assert record is not None
    assert len(db.shadow_log) == 1 and db.shadow_log[0] == record
    assert record["result_kind"] == "recommendation"
    assert record["action"] == "sibling_audit"
    assert record["correction_item_id"] == "100"
    assert record["generating_rule_id"] == RULE
    # the suspect sibling is named as reopened; the corrected item itself is not a sibling.
    assert record["reopened_principle_ids"] == ["200"]
    assert record["requires_human"] is True  # an unvalidated sibling requires a human
    assert "sibling_audit" in record["rationale"] or "reopened" in record["rationale"]


def test_genuinely_fine_siblings_record_pass_no_change():
    db = ShadowFakeDb(
        correction=_correction_row(),
        principles=[
            _principle_row(100, "promoted", validated="promoted"),
            # sibling fully human-validated AFTER the correction and matching its classification.
            _principle_row(200, "promoted", validated="promoted", validated_at=AFTER),
        ],
    )
    record = shadow.observe(db, work_context="ctx")
    assert record["result_kind"] == "pass"
    assert record["action"] is None
    assert record["requires_human"] is False
    assert "no change" in record["rationale"]


def test_no_recent_correction_records_no_snapshot():
    db = ShadowFakeDb(correction=None)
    record = shadow.observe(db, work_context="ctx")
    assert record["result_kind"] == "no_snapshot"
    assert record["action"] is None
    assert record["correction_item_id"] is None
    assert len(db.shadow_log) == 1


def test_correction_but_rule_minted_no_principles_is_no_snapshot():
    db = ShadowFakeDb(correction=_correction_row(), principles=[])
    record = shadow.observe(db, work_context="ctx")
    assert record["result_kind"] == "no_snapshot"


# ---------------------------------------------------------------------------
# 2. fail-safety: snapshot / consult / telemetry raising is non-fatal.
# ---------------------------------------------------------------------------

def test_snapshot_read_failure_is_non_fatal_and_logged_as_error():
    db = ShadowFakeDb(fail_read=True)
    record = shadow.observe(db, work_context="ctx")  # must NOT raise
    assert record is None
    # a best-effort error row is recorded (its own failure would be swallowed too).
    assert len(db.shadow_log) == 1 and db.shadow_log[0]["result_kind"] == "error"
    assert db.shadow_log[0]["detail"]["stage"] == "snapshot"


def test_consult_failure_is_non_fatal():
    db = ShadowFakeDb(
        correction=_correction_row(),
        principles=[_principle_row(100, "promoted", validated="promoted"),
                    _principle_row(200, "provisional")],
    )
    with patch.object(shadow, "consult", side_effect=RuntimeError("consult boom")):
        record = shadow.observe(db, work_context="ctx")
    assert record is None
    assert db.shadow_log and db.shadow_log[0]["result_kind"] == "error"
    assert db.shadow_log[0]["detail"]["stage"] == "consult"


def test_telemetry_write_failure_is_non_fatal():
    db = ShadowFakeDb(correction=None, fail_record=True)
    record = shadow.observe(db, work_context="ctx")  # must NOT raise even though record explodes
    assert record is None
    assert db.shadow_log == []  # nothing captured because the write itself failed


# ---------------------------------------------------------------------------
# 3. loop integration — the hook at DECIDE.
# ---------------------------------------------------------------------------

def _run_loop_cycle(db, *, flag):
    ex = DecideExecutor()
    env = {"AQ_SELF_CORRECTION_SHADOW": "1"} if flag else {}
    # Pin the loop's OWN nondeterminism (the random per-cycle plan id) so the on/off runs are
    # comparable like-for-like — the shadow is not the source of any variation here.
    with patch("runner.causal_sync.mgmt_base_url", return_value=None), \
         patch("runner.loop._new_global_plan_id", return_value="urn:uuid:fixed/plan/fixed"), \
         patch.dict("os.environ", env, clear=False):
        if not flag:
            # ensure the flag is truly absent for the off run.
            import os
            os.environ.pop("AQ_SELF_CORRECTION_SHADOW", None)
        cycle = Loop(inst_external(), db, ex).cycle()
    return cycle, ex


def _suspect_db():
    return ShadowFakeDb(
        correction=_correction_row(),
        principles=[_principle_row(100, "promoted", validated="promoted"),
                    _principle_row(200, "provisional")],
    )


def test_flag_on_loop_records_sibling_audit_at_decide():
    db = _suspect_db()
    cycle, _ = _run_loop_cycle(db, flag=True)
    assert cycle is not None                       # the loop still acts normally
    assert len(db.shadow_log) == 1
    row = db.shadow_log[0]
    assert row["result_kind"] == "recommendation" and row["action"] == "sibling_audit"
    assert row["reopened_principle_ids"] == ["200"]
    assert "kind=delivery" in row["work_context"]


def test_flag_off_is_no_op_consult_never_called():
    db = _suspect_db()
    with patch.object(shadow, "consult") as spy_consult:
        cycle, _ = _run_loop_cycle(db, flag=False)
    assert cycle is not None
    assert db.shadow_log == []               # no telemetry
    assert db.correction_read_calls == 0     # snapshot never even read
    assert spy_consult.call_count == 0       # consult never called


def test_zero_behavior_change_invariant_on_vs_off():
    """THE KEY TEST: decision + every non-telemetry side effect is identical on vs off."""
    db_off = _suspect_db()
    db_on = _suspect_db()

    cycle_off, ex_off = _run_loop_cycle(db_off, flag=False)
    cycle_on, ex_on = _run_loop_cycle(db_on, flag=True)

    # The returned Cycle is identical.
    assert cycle_off == cycle_on and cycle_off is not None

    # The executor saw the exact same sequence of schema calls (no extra model calls on).
    assert ex_off.schemas == ex_on.schemas

    # Every mutation-capturing surface of the DB is byte-identical...
    for attr in ("created", "started", "recorded", "completed", "learned", "failed",
                 "plan_predictions", "plan_evaluations", "support_events", "replans",
                 "events", "intent_checks", "parked", "park_notes"):
        assert getattr(db_off, attr) == getattr(db_on, attr), f"{attr} diverged on vs off"

    # ...EXCEPT the append-only shadow telemetry: empty when off, one recommendation when on.
    assert db_off.shadow_log == []
    assert len(db_on.shadow_log) == 1 and db_on.shadow_log[0]["action"] == "sibling_audit"


def test_flag_on_but_shadow_read_failure_does_not_disturb_the_cycle():
    """A governance-read explosion during the hook must not change the loop's behavior."""
    db_ok = _suspect_db()
    db_broken = ShadowFakeDb(fail_read=True)

    cycle_ok, _ = _run_loop_cycle(db_ok, flag=True)
    cycle_broken, _ = _run_loop_cycle(db_broken, flag=True)

    assert cycle_ok is not None and cycle_broken is not None
    for attr in ("created", "started", "recorded", "completed", "learned"):
        assert getattr(db_ok, attr) == getattr(db_broken, attr), f"{attr} disturbed by shadow failure"
    # the broken db recorded only a best-effort error row, no recommendation.
    assert [r["result_kind"] for r in db_broken.shadow_log] == ["error"]
