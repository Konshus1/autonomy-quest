"""Live-DB proof harness for the `aqmem` memory grants (#4834). SKIPPED unless the two DSNs are set.

Run against a real Postgres (the AGE+pgvector image) after applying schema/030_agent_memory.sql:

    AQ_MEM_TEST_OWNER_DSN=postgresql://aq_owner:...@localhost/aq \
    AQ_MEM_TEST_ACTOR_DSN=postgresql://aq_actor:...@localhost/aq \
    pytest -q tests/test_agent_mem_pg.py

Proves the least-privilege bar the brief calls out: as aq_actor, aqmem CAN write agent_memory, but
a raw INSERT into an authority table (learnings) STILL fails with permission denied — the new grant
did not over-reach. Also proves the migration is idempotent (applying it twice is a no-op) and that
the AGE world_model graph (if present) is writable by aq_actor.
"""

import json
import os
import pathlib

import pytest

psycopg2 = pytest.importorskip("psycopg2")

OWNER_DSN = os.environ.get("AQ_MEM_TEST_OWNER_DSN")
ACTOR_DSN = os.environ.get("AQ_MEM_TEST_ACTOR_DSN")
MIGRATION = pathlib.Path(__file__).resolve().parents[1] / "schema" / "030_agent_memory.sql"

pytestmark = pytest.mark.skipif(
    not (OWNER_DSN and ACTOR_DSN),
    reason="set AQ_MEM_TEST_OWNER_DSN and AQ_MEM_TEST_ACTOR_DSN to run the live memory-grant proof",
)

_ZERO_VEC = "[" + ",".join(["0"] * 384) + "]"


def _apply_migration():
    sql = MIGRATION.read_text()
    with psycopg2.connect(OWNER_DSN) as conn, conn.cursor() as cur:
        cur.execute(sql)
    conn.close()


def test_migration_is_idempotent():
    _apply_migration()
    _apply_migration()  # second application must not raise


def test_actor_can_write_memory():
    _apply_migration()
    with psycopg2.connect(ACTOR_DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_memory.notes (instance_id, kind, text, embedding) "
            "VALUES (%s, %s, %s, %s::vector) RETURNING id",
            ("pgtest", "learning", "aqmem live grant proof", _ZERO_VEC),
        )
        assert cur.fetchone()[0] > 0
        cur.execute("SELECT count(*) FROM agent_memory.notes WHERE instance_id = 'pgtest'")
        assert cur.fetchone()[0] >= 1
    conn.close()


def test_actor_cannot_write_authority_tables():
    _apply_migration()
    conn = psycopg2.connect(ACTOR_DSN)
    try:
        with conn.cursor() as cur:
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cur.execute(
                    "INSERT INTO learnings (insight, evidence, confidence) "
                    "VALUES ('x','y',0.5)"
                )
        conn.rollback()
    finally:
        conn.close()


# Authority/evidence tables the combined end-state (schema/999 default-deny reads + schema/030) must
# keep UNREADABLE to aq_actor. schema/030 grants only agent_memory + world_model; it must not undo
# 999's REVOKE SELECT ON ALL TABLES ... FROM aq_actor.
_AUTHORITY_TABLES = ["learnings", "runs", "work", "causal_edge"]


def test_actor_cannot_read_authority_tables():
    """Combined-grant proof: after 999 (default-deny reads) AND 030, aq_actor cannot SELECT the
    authority/evidence tables — 030's memory grants did not re-open a read path."""
    _apply_migration()
    conn = psycopg2.connect(ACTOR_DSN)
    try:
        for tbl in _AUTHORITY_TABLES:
            with conn.cursor() as cur:
                with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                    cur.execute("SELECT 1 FROM {} LIMIT 1".format(tbl))
            conn.rollback()
    finally:
        conn.close()


def test_actor_can_read_business_tables():
    """The business tables aqdb curates ARE readable — default-deny did not lock out the act phase."""
    _apply_migration()
    conn = psycopg2.connect(ACTOR_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM customers")
            cur.execute("SELECT count(*) FROM subscriptions")
        conn.rollback()
    finally:
        conn.close()


def _has_age(dsn=OWNER_DSN):
    with psycopg2.connect(dsn) as oc, oc.cursor() as ocur:
        ocur.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'age'")
        present = ocur.fetchone() is not None
    oc.close()
    return present


def _actor_prepare_age(cur):
    """Mirror aqmem._prepare_age: SAVEPOINT-guard the best-effort LOAD so a refused LOAD (aq_actor is
    not a superuser) does NOT poison the transaction."""
    cur.execute("SAVEPOINT sp_age")
    try:
        cur.execute("LOAD 'age'")
        cur.execute("RELEASE SAVEPOINT sp_age")
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT sp_age")
        cur.execute("RELEASE SAVEPOINT sp_age")
    cur.execute('SET search_path = public, ag_catalog, "$user"')


def test_actor_can_write_world_model_graph_if_present():
    _apply_migration()
    if not _has_age():
        pytest.skip("AGE not available on this server")
    conn = psycopg2.connect(ACTOR_DSN)
    try:
        with conn.cursor() as cur:
            _actor_prepare_age(cur)
            cur.execute(
                "SELECT * FROM ag_catalog.cypher('world_model', $$ "
                "MERGE (a:Entity {name: 'aqmem-actor'}) MERGE (b:Entity {name: 'world-model-write'}) "
                "MERGE (a)-[:PROVES]->(b) $$) AS (v agtype)"
            )
        conn.commit()
    finally:
        conn.close()


def test_world_model_backslash_injection_is_neutralized():
    """Live proof of the CRITICAL cypher-injection fix. Fire the exploit through the REAL aqmem
    cmd_relate against a live AGE graph and assert the graph is INTACT (the injected DETACH DELETE
    never ran) and the payload was stored as a literal entity name."""
    _apply_migration()
    if not _has_age():
        pytest.skip("AGE not available on this server")

    import importlib.machinery
    import importlib.util
    import pathlib as _pl
    aqmem_path = _pl.Path(__file__).resolve().parents[1] / "agent_skills" / "memory" / "aqmem"
    spec = importlib.util.spec_from_file_location(
        "aqmem_live", str(aqmem_path),
        loader=importlib.machinery.SourceFileLoader("aqmem_live", str(aqmem_path)))
    aqmem = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(aqmem)

    def _count(cur):
        _actor_prepare_age(cur)
        cur.execute("SELECT count(*) FROM ag_catalog.cypher('world_model', "
                    "$$ MATCH (n:Entity) RETURN n $$) AS (v agtype)")
        return cur.fetchone()[0]

    os.environ["AQ_DB_URL"] = ACTOR_DSN
    conn = psycopg2.connect(ACTOR_DSN)
    try:
        with conn.cursor() as cur:
            before = _count(cur)
        conn.commit()
    finally:
        conn.close()

    # Unique per run so both nodes are FRESH (MERGE creates them), making the wipe unmistakable: an
    # injected `MATCH (z:Entity) DETACH DELETE z` would drop ALL vertices -> after < before, not +2.
    import uuid
    marker = uuid.uuid4().hex[:8]
    a_name = "inj_{}\\".format(marker)  # terminal backslash: the exact breakout primitive
    payload_b = "}}) WITH 1 AS _ MATCH (z:Entity) DETACH DELETE z //{}".format(marker)
    rc = aqmem.main(["relate", a_name, "REL", payload_b])
    assert rc == 0

    conn = psycopg2.connect(ACTOR_DSN)
    try:
        with conn.cursor() as cur:
            after = _count(cur)
            # The graph GREW (2 new vertices), it was NOT wiped by an injected DETACH DELETE.
            assert after >= before + 2, "injection deleted vertices: %d -> %d" % (before, after)
            _actor_prepare_age(cur)
            cur.execute("SELECT * FROM ag_catalog.cypher('world_model', "
                        "$$ MATCH (n:Entity) RETURN n.name $$) AS (name agtype)")
            names = [str(r[0]) for r in cur.fetchall()]
            # The whole injection payload was stored as a LITERAL entity name (data, not tokens).
            assert any(payload_b in n for n in names), "payload not stored as a literal name"
        conn.commit()
    finally:
        conn.close()


def test_actor_cannot_tamper_another_graphs_catalog():
    """Live proof of the CRITICAL over-grant fix: aq_actor has NO write on ag_graph and no
    delete/update on ag_label, so it cannot drop or rename ANOTHER graph's catalog rows."""
    _apply_migration()
    if not _has_age():
        pytest.skip("AGE not available on this server")
    conn = psycopg2.connect(ACTOR_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute('SET search_path = public, ag_catalog, "$user"')
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cur.execute("DELETE FROM ag_catalog.ag_graph")
            conn.rollback()
        with conn.cursor() as cur:
            cur.execute('SET search_path = public, ag_catalog, "$user"')
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cur.execute("UPDATE ag_catalog.ag_label SET name = 'hijacked'")
            conn.rollback()
    finally:
        conn.close()
