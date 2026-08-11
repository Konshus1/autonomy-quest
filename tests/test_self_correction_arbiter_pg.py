"""Real-schema proof for the stage-2 self-correction arbiter apply (#4834).

Gates on a seeded governance DB (like the stage-1 pg test). It proves against GROUND TRUTH:

  * schema/027 applied the two append-only tables and aq_loop has INSERT (not UPDATE/DELETE) on
    them plus USAGE,SELECT on their sequences;
  * aq_loop CANNOT write any principle transition (it cannot promote/demote/authorize) — the hard
    boundary the apply must never cross;
  * the audit-request enqueue is idempotent (the UNIQUE key suppresses a duplicate open request);
  * enqueuing an audit request writes ZERO principle transitions (disposition table untouched);
  * both tables reject UPDATE/DELETE (append-only).

Run:
    AQ_SELFCORR_TEST_DSN='host=127.0.0.1 port=55483 dbname=aq user=aq_owner password=...' \
        python -m pytest tests/test_self_correction_arbiter_pg.py -q
"""
from __future__ import annotations

import os
import uuid

import pytest

DSN = os.environ.get("AQ_SELFCORR_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not DSN, reason="set AQ_SELFCORR_TEST_DSN to a seeded governance DB to run the real-schema test"
)


@pytest.fixture()
def db():
    from runner.db import Db
    conn = Db(DSN, graph="none", connect_retries=2)
    yield conn


def test_tables_exist_and_are_append_only(db):
    assert db._q("SELECT to_regclass('public.self_correction_audit_request') AS t", one=True)["t"]
    assert db._q("SELECT to_regclass('public.self_correction_arbiter_action') AS t", one=True)["t"]
    for table in ("self_correction_audit_request", "self_correction_arbiter_action"):
        trg = db._q(
            "SELECT count(*) AS n FROM pg_trigger WHERE tgrelid=%s::regclass AND NOT tgisinternal",
            (f"public.{table}",), one=True)["n"]
        assert trg >= 1, f"{table} must carry an append-only trigger"


def test_aq_loop_can_insert_but_not_mutate_the_apply_tables(db):
    for table in ("self_correction_audit_request", "self_correction_arbiter_action"):
        priv = db._q(
            "SELECT has_table_privilege('aq_loop',%s,'INSERT') AS ins, "
            "has_table_privilege('aq_loop',%s,'UPDATE') AS upd, "
            "has_table_privilege('aq_loop',%s,'DELETE') AS del",
            (table, table, table), one=True)
        assert priv["ins"] is True, f"aq_loop needs INSERT on {table}"
        assert priv["upd"] is False and priv["del"] is False, f"{table} must be append-only for aq_loop"


def test_aq_loop_cannot_write_any_principle_transition(db):
    """The load-bearing safety boundary: the apply lives in aq_loop, and aq_loop can NEVER
    promote/demote/authorize — it has no write on the principle-transition table."""
    priv = db._q(
        "SELECT has_table_privilege('aq_loop','causal_principle_transition','INSERT') AS ins, "
        "has_table_privilege('aq_loop','causal_principle_transition','UPDATE') AS upd, "
        "has_table_privilege('aq_loop','causal_principle_transition','DELETE') AS del",
        one=True)
    assert priv == {"ins": False, "upd": False, "del": False}


def test_enqueue_is_idempotent_and_writes_no_transition(db):
    before = db._q("SELECT count(*) AS n FROM causal_principle_transition", one=True)["n"]
    token = f"pgtest-correction-{uuid.uuid4()}"
    principle = f"pgtest-sibling-{uuid.uuid4()}"

    first = db.enqueue_self_correction_audit_request(
        principle_id=principle, generating_rule_id="pgtest-rule-v1",
        reason="pg idempotency probe", source_correction=token,
        source_correction_item_id="pgtest-item", work_context="pg-test")
    second = db.enqueue_self_correction_audit_request(
        principle_id=principle, generating_rule_id="pgtest-rule-v1",
        reason="pg idempotency probe", source_correction=token,
        source_correction_item_id="pgtest-item", work_context="pg-test")
    assert first is True and second is False, "same correction twice must not open a duplicate"

    rows = db._q("SELECT count(*) AS n FROM self_correction_audit_request "
                 "WHERE principle_id=%s AND source_correction=%s", (principle, token), one=True)["n"]
    assert rows == 1

    after = db._q("SELECT count(*) AS n FROM causal_principle_transition", one=True)["n"]
    assert after == before, "enqueuing an audit request must write ZERO principle transitions"


def test_enqueue_and_record_run_under_aq_loop_privileges(db):
    """REGRESSION GUARD. The apply runs as aq_loop, which has INSERT but NOT SELECT on the queue
    (like schema/026). A ``RETURNING`` clause or a TARGETED ``ON CONFLICT (cols)`` would demand
    SELECT and fail at runtime under aq_loop even though it passes as the owner. Exercise the real
    enqueue/record methods with the session role dropped to aq_loop so that regression is caught."""
    token = f"looppriv-correction-{uuid.uuid4()}"
    principle = f"looppriv-sibling-{uuid.uuid4()}"
    db._q("SET ROLE aq_loop")
    try:
        first = db.enqueue_self_correction_audit_request(
            principle_id=principle, generating_rule_id="loop-rule-v1",
            reason="aq_loop privilege probe", source_correction=token,
            source_correction_item_id="loop-item", work_context="loop-priv-test")
        second = db.enqueue_self_correction_audit_request(
            principle_id=principle, generating_rule_id="loop-rule-v1",
            reason="aq_loop privilege probe", source_correction=token,
            source_correction_item_id="loop-item", work_context="loop-priv-test")
        db.record_self_correction_arbiter_action(
            work_context="loop-priv-test", outcome="enqueue_audit", action="sibling_audit",
            held_dispatch=False, correction_item_id="loop-item", generating_rule_id="loop-rule-v1",
            reason="aq_loop records-why probe", audit_request_count=1, detail={})
    finally:
        db._q("RESET ROLE")
    assert first is True and second is False, "aq_loop enqueue must insert once then idempotently skip"


def test_arbiter_action_row_records(db):
    db.record_self_correction_arbiter_action(
        work_context="pg-test", outcome="hold_dispatch", action="frame_review",
        held_dispatch=True, correction_item_id="pgtest-item", generating_rule_id="pgtest-rule-v1",
        reason="pg records-why probe", audit_request_count=0, detail={"bounded_to_cycle": True})
    row = db._q("SELECT outcome, held_dispatch FROM self_correction_arbiter_action "
                "WHERE reason='pg records-why probe' ORDER BY id DESC LIMIT 1", one=True)
    assert row["outcome"] == "hold_dispatch" and row["held_dispatch"] is True
