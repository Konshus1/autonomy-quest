"""Live-PG proof that the signups writer is INSERT-only + read-blind, and the table is append-only.

This is the executable proof of schema/033_signups.sql. Mirroring test_analytics_events_grant_boundary
(the "agent cannot forge its own scoreboard" boundary), here the load-bearing property is:

    the dedicated site-write credential (agentricity_signups_writer) can APPEND a signup and let ON CONFLICT
    DO NOTHING dedup — but it can NOT read the list back, nor update/delete/truncate it.

so a leak of the public-facing site credential cannot exfiltrate everyone's email or rewrite history.

Gated on a superuser/owner DSN. Provide ``SIGNUPS_ADMIN_DSN`` (e.g. postgresql://localhost:5432/postgres)
and the test provisions an ISOLATED throwaway database + the writer role, applies the migration, and
asserts the boundary. ``SIGNUPS_REQUIRE_GRANTS=1`` turns the skip into a hard failure so the boundary
can never look verified while never running.

IMPORTANT — point ``SIGNUPS_ADMIN_DSN`` at a DISPOSABLE Postgres, never at a server that hosts the
LIVE ``agentricity_signups_writer`` role. Roles are cluster-global, so this fixture creating/dropping the role
would clobber a production writer credential. The fixture now REFUSES (raises) if the role already
exists on the target server, and drops the role it creates on teardown — so a live deployment is
protected and disposable reruns stay clean.
"""
from __future__ import annotations

import os
from pathlib import Path

import psycopg2
from psycopg2 import errors as pg_errors
from psycopg2 import extensions, sql
import pytest

ADMIN_DSN = os.environ.get("SIGNUPS_ADMIN_DSN")
_REQUIRE = os.environ.get("SIGNUPS_REQUIRE_GRANTS", "").strip() in {"1", "true", "yes", "on"}
if _REQUIRE and not ADMIN_DSN:
    raise RuntimeError(
        "SIGNUPS_REQUIRE_GRANTS=1 but SIGNUPS_ADMIN_DSN is unset. The signups writer grant boundary "
        "would SKIP. Provide a superuser/owner DSN, or unset the gate. Refusing to report green "
        "without having run it."
    )

pytestmark = pytest.mark.skipif(
    not ADMIN_DSN,
    reason="set SIGNUPS_ADMIN_DSN (a superuser/owner DSN) to run the signups grant boundary proof",
)

_MIGRATION = Path(__file__).resolve().parents[1] / "schema" / "033_signups.sql"
_TEST_DB = "agentricity_signups_boundary_test"
_WRITER = "agentricity_signups_writer"
_WRITER_PW = "boundary_test_pw"


def _writer_dsn(admin_dsn: str, dbname: str) -> str:
    params = extensions.parse_dsn(admin_dsn)
    params.update({"dbname": dbname, "user": _WRITER, "password": _WRITER_PW})
    return " ".join(f"{k}={v}" for k, v in params.items())


@pytest.fixture(scope="module")
def provisioned():
    """Create an isolated DB + writer role, apply schema/033, yield the writer DSN, then clean up."""
    admin = psycopg2.connect(ADMIN_DSN)
    admin.autocommit = True
    with admin.cursor() as cur:
        # SAFETY: roles are CLUSTER-GLOBAL, not per-database. This fixture creates + DROPS the writer
        # role, so if one already exists we must NOT touch it — resetting its password would clobber a
        # LIVE deployment's writer credential (exactly the footgun of pointing SIGNUPS_ADMIN_DSN at a
        # server that hosts the live signups writer). Refuse loudly; demand a disposable cluster.
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (_WRITER,))
        if cur.fetchone() is not None:
            raise RuntimeError(
                f"Role {_WRITER!r} already exists on the server behind SIGNUPS_ADMIN_DSN. This test "
                f"creates and DROPS that cluster-global role; running it here would clobber a live "
                f"writer credential. Point SIGNUPS_ADMIN_DSN at a DISPOSABLE Postgres with no live "
                f"{_WRITER!r} role — NEVER a server hosting the production signups writer."
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

    # Apply the migration (which grants because the writer role now exists) in the fresh DB.
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
        # Drop the cluster-global role we created, so the cluster is left exactly as we found it and
        # repeat runs against a disposable Postgres stay clean (the pre-existence guard above then
        # passes each time because we always leave the role absent).
        cur.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(_WRITER)))
    admin.close()


def test_writer_can_insert_and_dedup(provisioned):
    """The writer MUST append + dedup with INSERT privilege ALONE.

    Dedup is read from rowcount (the command tag), NOT RETURNING — RETURNING would require SELECT,
    which the read-blind writer is deliberately denied. This is the exact code path the service uses.
    """
    conn = psycopg2.connect(provisioned)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agentricity_signups (email, source) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                ("writer@example.com", "test"),
            )
            assert cur.rowcount == 1  # newly recorded
            cur.execute(
                "INSERT INTO agentricity_signups (email, source) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                ("writer@example.com", "test"),
            )
            assert cur.rowcount == 0  # dedup: no new row, and no SELECT/RETURNING needed
    finally:
        conn.close()


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT * FROM agentricity_signups",                       # READ-BLIND: cannot read the list back
        "SELECT last_value FROM agentricity_signups_id_seq",       # nor read the sequence (leaks list SIZE)
        "UPDATE agentricity_signups SET email='x@y.com'",
        "DELETE FROM agentricity_signups",
        "TRUNCATE agentricity_signups",
    ],
)
def test_writer_cannot_read_or_mutate(provisioned, statement):
    """The boundary: select/update/delete/truncate are all refused at the grant layer."""
    conn = psycopg2.connect(provisioned)
    try:
        with conn.cursor() as cur:
            with pytest.raises(pg_errors.InsufficientPrivilege):
                cur.execute(statement)
        conn.rollback()
    finally:
        conn.close()


@pytest.mark.parametrize("statement", ["UPDATE agentricity_signups SET email='x@y.com'", "DELETE FROM agentricity_signups"])
def test_append_only_trigger_blocks_even_owner(provisioned, statement):
    """Even a privileged principal cannot mutate: the append-only trigger rejects UPDATE/DELETE."""
    admin_params = extensions.parse_dsn(ADMIN_DSN)
    admin_params["dbname"] = _TEST_DB
    conn = psycopg2.connect(" ".join(f"{k}={v}" for k, v in admin_params.items()))
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO agentricity_signups (email) VALUES ('trig@example.com') ON CONFLICT DO NOTHING")
            with pytest.raises(psycopg2.Error) as exc:
                cur.execute(statement)
            assert "append-only" in str(exc.value)
        conn.rollback()
    finally:
        conn.close()
