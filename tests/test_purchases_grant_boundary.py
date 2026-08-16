"""Live-PG proof that the purchases writer is INSERT-only + read-blind, and the ledger is append-only.

The executable proof of schema/034_purchases.sql — the mirror of test_signups_grant_boundary. The
load-bearing property: the dedicated site-write credential (aq_purchases_writer) can APPEND a verified
Stripe event and let ``ON CONFLICT DO NOTHING`` dedup, but it can NOT read the buyer ledger back, nor
update/delete/truncate it. So a leak of the public webhook container's credential cannot exfiltrate who
bought or rewrite purchase history.

Gated on a superuser/owner DSN. Provide ``PURCHASES_ADMIN_DSN`` (a superuser/owner DSN) and the test
provisions an ISOLATED throwaway database + the writer role, applies the migration, and asserts the
boundary. ``PURCHASES_REQUIRE_GRANTS=1`` turns the skip into a hard failure.

IMPORTANT — point ``PURCHASES_ADMIN_DSN`` at a DISPOSABLE Postgres, never a server that hosts the LIVE
``aq_purchases_writer`` role. Roles are cluster-global, so this fixture creating/dropping the role would
clobber a production writer credential. The fixture REFUSES (raises) if the role already exists on the
target server, and drops the role it creates on teardown.
"""
from __future__ import annotations

import os
from pathlib import Path

import psycopg2
from psycopg2 import errors as pg_errors
from psycopg2 import extensions, sql
import pytest

ADMIN_DSN = os.environ.get("PURCHASES_ADMIN_DSN")
_REQUIRE = os.environ.get("PURCHASES_REQUIRE_GRANTS", "").strip() in {"1", "true", "yes", "on"}
if _REQUIRE and not ADMIN_DSN:
    raise RuntimeError(
        "PURCHASES_REQUIRE_GRANTS=1 but PURCHASES_ADMIN_DSN is unset. The purchases writer grant "
        "boundary would SKIP. Provide a superuser/owner DSN, or unset the gate. Refusing to report "
        "green without having run it."
    )

pytestmark = pytest.mark.skipif(
    not ADMIN_DSN,
    reason="set PURCHASES_ADMIN_DSN (a superuser/owner DSN) to run the purchases grant boundary proof",
)

_MIGRATION = Path(__file__).resolve().parents[1] / "schema" / "034_purchases.sql"
_TEST_DB = "agentricity_purchases_boundary_test"
_WRITER = "aq_purchases_writer"
_WRITER_PW = "boundary_test_pw"


def _writer_dsn(admin_dsn: str, dbname: str) -> str:
    params = extensions.parse_dsn(admin_dsn)
    params.update({"dbname": dbname, "user": _WRITER, "password": _WRITER_PW})
    return " ".join(f"{k}={v}" for k, v in params.items())


@pytest.fixture(scope="module")
def provisioned():
    """Create an isolated DB + writer role, apply schema/034, yield the writer DSN, then clean up."""
    admin = psycopg2.connect(ADMIN_DSN)
    admin.autocommit = True
    with admin.cursor() as cur:
        # SAFETY: roles are CLUSTER-GLOBAL. This fixture creates + DROPS the writer role, so if one
        # already exists we must NOT touch it — resetting its password would clobber a LIVE deployment's
        # writer credential. Refuse loudly; demand a disposable cluster.
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (_WRITER,))
        if cur.fetchone() is not None:
            raise RuntimeError(
                f"Role {_WRITER!r} already exists on the server behind PURCHASES_ADMIN_DSN. This test "
                f"creates and DROPS that cluster-global role; running it here would clobber a live "
                f"writer credential. Point PURCHASES_ADMIN_DSN at a DISPOSABLE Postgres with no live "
                f"{_WRITER!r} role — NEVER a server hosting the production purchases writer."
            )
        cur.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD %s").format(sql.Identifier(_WRITER)), (_WRITER_PW,)
        )
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s", (_TEST_DB,)
        )
        cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(_TEST_DB)))
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(_TEST_DB)))
    admin.close()

    admin_params = extensions.parse_dsn(ADMIN_DSN)
    admin_params["dbname"] = _TEST_DB
    db = psycopg2.connect(" ".join(f"{k}={v}" for k, v in admin_params.items()))
    db.autocommit = True
    with db.cursor() as cur:
        cur.execute(_MIGRATION.read_text())
        cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(_WRITER)))
    db.close()

    yield _writer_dsn(ADMIN_DSN, _TEST_DB)

    admin = psycopg2.connect(ADMIN_DSN)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s", (_TEST_DB,)
        )
        cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(_TEST_DB)))
        cur.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(_WRITER)))
    admin.close()


def test_writer_can_insert_and_dedup(provisioned):
    """The writer MUST append + dedup with INSERT privilege ALONE (dedup via rowcount, not RETURNING)."""
    conn = psycopg2.connect(provisioned)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO aq_purchases (stripe_event_id, event_type) VALUES (%s,%s) "
                "ON CONFLICT DO NOTHING",
                ("evt_boundary_1", "checkout.session.completed"),
            )
            assert cur.rowcount == 1
            cur.execute(
                "INSERT INTO aq_purchases (stripe_event_id, event_type) VALUES (%s,%s) "
                "ON CONFLICT DO NOTHING",
                ("evt_boundary_1", "checkout.session.completed"),
            )
            assert cur.rowcount == 0  # dedup on stripe_event_id, no SELECT/RETURNING needed
    finally:
        conn.close()


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT * FROM aq_purchases",                    # READ-BLIND: cannot read the buyer ledger
        "SELECT last_value FROM aq_purchases_id_seq",    # nor read the sequence (leaks buyer count)
        "UPDATE aq_purchases SET customer_email='x@y.com'",
        "DELETE FROM aq_purchases",
        "TRUNCATE aq_purchases",
    ],
)
def test_writer_cannot_read_or_mutate(provisioned, statement):
    conn = psycopg2.connect(provisioned)
    try:
        with conn.cursor() as cur:
            with pytest.raises(pg_errors.InsufficientPrivilege):
                cur.execute(statement)
        conn.rollback()
    finally:
        conn.close()


@pytest.mark.parametrize(
    "statement",
    ["UPDATE aq_purchases SET customer_email='x@y.com'", "DELETE FROM aq_purchases"],
)
def test_append_only_trigger_blocks_even_owner(provisioned, statement):
    admin_params = extensions.parse_dsn(ADMIN_DSN)
    admin_params["dbname"] = _TEST_DB
    conn = psycopg2.connect(" ".join(f"{k}={v}" for k, v in admin_params.items()))
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO aq_purchases (stripe_event_id, event_type) "
                        "VALUES ('evt_trig','charge.refunded') ON CONFLICT DO NOTHING")
            with pytest.raises(psycopg2.Error) as exc:
                cur.execute(statement)
            assert "append-only" in str(exc.value)
        conn.rollback()
    finally:
        conn.close()
