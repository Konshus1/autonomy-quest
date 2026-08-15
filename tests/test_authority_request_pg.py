"""Real-schema proof for the Slice-3 authority-change request store (design_authority_envelope.md).

Gates on a seeded governance DB (like the self-correction PG tests). It proves against GROUND TRUTH:

  * schema/031 applied the two append-only tables with their append-only triggers;
  * aq_loop can INSERT a REQUEST (+ its sequence) but has NO UPDATE/DELETE on it (append-only);
  * THE SELF-GRANT BOUNDARY: aq_loop has NO privilege at all on the DISPOSITION table — it cannot
    INSERT (record its own approval), UPDATE, or DELETE. aq_governance (host authority) CAN INSERT a
    disposition. This is the DB-level guarantee that the actor cannot approve its own request;
  * the request enqueue is idempotent (the UNIQUE dedupe key suppresses a duplicate open request);
  * both tables reject UPDATE/DELETE (append-only trigger).

Run:
    AQ_SELFCORR_TEST_DSN='host=127.0.0.1 port=55483 dbname=aq user=aq_owner password=...' \
        python -m pytest tests/test_authority_request_pg.py -q
"""
from __future__ import annotations

import os

import pytest

DSN = os.environ.get("AQ_SELFCORR_TEST_DSN") or os.environ.get("AQ_AUTHREQ_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not DSN, reason="set AQ_SELFCORR_TEST_DSN to a seeded governance DB to run the real-schema test")


@pytest.fixture()
def db():
    from runner.db import Db
    conn = Db(DSN, graph="none", connect_retries=2)
    yield conn


def test_tables_exist_and_are_append_only(db):
    for table in ("authority_change_request", "authority_change_disposition"):
        assert db._q("SELECT to_regclass(%s) AS t", (f"public.{table}",), one=True)["t"]
        trg = db._q(
            "SELECT count(*) AS n FROM pg_trigger WHERE tgrelid=%s::regclass AND NOT tgisinternal",
            (f"public.{table}",), one=True)["n"]
        assert trg >= 1, f"{table} must carry an append-only trigger"


def test_aq_loop_can_insert_request_but_not_mutate_it(db):
    priv = db._q(
        "SELECT has_table_privilege('aq_loop','authority_change_request','INSERT') AS ins, "
        "has_table_privilege('aq_loop','authority_change_request','UPDATE') AS upd, "
        "has_table_privilege('aq_loop','authority_change_request','DELETE') AS del", one=True)
    assert priv["ins"] is True, "aq_loop must be able to EMIT a request"
    assert priv["upd"] is False and priv["del"] is False, "request table must be append-only for aq_loop"


def test_aq_loop_has_no_write_on_disposition_the_self_grant_boundary(db):
    """The load-bearing boundary: the actor's process credential can NEVER record an approval."""
    priv = db._q(
        "SELECT has_table_privilege('aq_loop','authority_change_disposition','INSERT') AS ins, "
        "has_table_privilege('aq_loop','authority_change_disposition','UPDATE') AS upd, "
        "has_table_privilege('aq_loop','authority_change_disposition','DELETE') AS del", one=True)
    assert priv["ins"] is False, "aq_loop must NOT be able to write a disposition (would be self-grant)"
    assert priv["upd"] is False and priv["del"] is False


def test_aq_actor_has_no_access_to_either_authority_table(db):
    for table in ("authority_change_request", "authority_change_disposition"):
        priv = db._q(
            "SELECT has_table_privilege('aq_actor',%s,'INSERT') AS ins, "
            "has_table_privilege('aq_actor',%s,'SELECT') AS sel",
            (table, table), one=True)
        assert priv["ins"] is False, f"aq_actor must not write {table}"
        assert priv["sel"] is False, f"aq_actor must not read {table}"


def test_aq_governance_can_record_a_disposition(db):
    ins = db._q(
        "SELECT has_table_privilege('aq_governance','authority_change_disposition','INSERT') AS ins",
        one=True)["ins"]
    assert ins is True, "aq_governance (host authority) must be able to record approved/denied"


def test_request_enqueue_is_idempotent(db):
    from runner.authority_request import AuthorityChangeRequest
    import uuid
    digest = uuid.uuid4().hex
    req = AuthorityChangeRequest(
        instance_id="pg-test", capability_class="spend_money", proposed_action_digest=digest,
        rationale="test", reversibility="reversible", requested_scope="test", requested_bound="one",
        reason_kind="gated_class", work_id=1)
    assert db.emit_authority_change_request(req) is True
    assert db.emit_authority_change_request(req) is False, "duplicate (instance,class,digest) suppressed"


def test_request_table_rejects_update_and_delete(db):
    from runner.authority_request import AuthorityChangeRequest
    import uuid
    digest = uuid.uuid4().hex
    req = AuthorityChangeRequest(
        instance_id="pg-test", capability_class="commit", proposed_action_digest=digest,
        rationale="test", reversibility="reversible", requested_scope="test", requested_bound="one",
        reason_kind="gated_class", work_id=2)
    assert db.emit_authority_change_request(req) is True
    rid = db._q("SELECT id FROM authority_change_request WHERE proposed_action_digest=%s",
                (digest,), one=True)["id"]
    for stmt, args in (
        ("UPDATE authority_change_request SET rationale='x' WHERE id=%s", (rid,)),
        ("DELETE FROM authority_change_request WHERE id=%s", (rid,)),
    ):
        with pytest.raises(Exception):
            db._q(stmt, args)
