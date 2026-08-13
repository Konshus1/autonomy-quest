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


def test_actor_can_write_world_model_graph_if_present():
    _apply_migration()
    with psycopg2.connect(OWNER_DSN) as oc, oc.cursor() as ocur:
        ocur.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'age'")
        has_age = ocur.fetchone() is not None
    oc.close()
    if not has_age:
        pytest.skip("AGE not available on this server")

    conn = psycopg2.connect(ACTOR_DSN)
    try:
        with conn.cursor() as cur:
            try:
                cur.execute("LOAD 'age'")
            except Exception:
                pass
            cur.execute('SET search_path = public, ag_catalog, "$user"')
            cur.execute(
                "SELECT * FROM ag_catalog.cypher('world_model', $$ "
                "MERGE (a:Entity {name: %(a)s}) MERGE (b:Entity {name: %(b)s}) "
                "MERGE (a)-[:PROVES]->(b) $$) AS (v agtype)",
                {"a": "aqmem-actor", "b": "world-model-write"},
            )
        conn.commit()
    finally:
        conn.close()
