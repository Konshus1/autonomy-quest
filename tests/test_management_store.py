"""Task #4407 v6 — durable state store: in-memory contract + optional Pg integration.

The in-memory store is exercised fully here. The Postgres store is integration-tested
only when AQ_TEST_DB_URL points at a reachable database (otherwise skipped), so the
default smoke run needs no live DB. The manager ran the Pg path live against the
container's Postgres; see CHECKPOINT_v6.md.
"""

from __future__ import annotations

import os

import pytest

from management.api.store import InMemoryStore, build_store


def test_in_memory_seeds_default_workstream() -> None:
    s = InMemoryStore()
    ws = s.workstreams()
    assert len(ws) == 1
    assert ws[0]["id"] == "ws-default"
    assert s.backing == "in_memory_stub"


def test_in_memory_task_ids_and_shape() -> None:
    s = InMemoryStore()
    a = s.create_task("first", "ws-default")
    b = s.create_task("second", "ws-default")
    assert a == {"id": "task-1", "title": "first", "workstream_id": "ws-default", "status": "open"}
    assert b["id"] == "task-2"
    assert [t["id"] for t in s.tasks()] == ["task-1", "task-2"]


def test_in_memory_replication_and_merge_roundtrip() -> None:
    s = InMemoryStore()
    s.add_replication({"mission_id": "m1", "status": "awaiting_operator"})
    s.add_replication({"mission_id": "m2", "status": "executed"})
    s.add_merge({"decision": "approve_merge"})
    reps = s.replications()
    assert len(reps) == 2
    pending = sum(1 for r in reps if r.get("status") != "executed")
    assert pending == 1
    assert s.merges() == [{"decision": "approve_merge"}]


def test_build_store_defaults_to_in_memory_without_db_env() -> None:
    # No DB env keys -> in-memory (the smoke/no-DB default).
    s = build_store(env={"PATH": "/usr/bin"})
    assert isinstance(s, InMemoryStore)
    assert s.backing == "in_memory_stub"


def test_build_store_falls_back_when_dsn_unreachable() -> None:
    # A syntactically-plausible but dead DSN must fall back, never raise.
    s = build_store(env={"AQ_MGMT_DB_URL": "postgresql://nobody@127.0.0.1:1/none"})
    assert isinstance(s, InMemoryStore)


@pytest.mark.skipif(
    not os.environ.get("AQ_TEST_DB_URL"),
    reason="set AQ_TEST_DB_URL to a reachable Postgres to run the PgStore integration test",
)
def test_pg_store_roundtrip_when_db_available() -> None:
    from management.api.store import PgStore

    s = PgStore(os.environ["AQ_TEST_DB_URL"])
    assert s.backing == "postgres"
    ws = s.workstreams()
    assert any(w["id"] == "ws-default" for w in ws)
    t = s.create_task("pg-task", "ws-default")
    assert t["id"].startswith("task-")
    assert any(x["id"] == t["id"] for x in s.tasks())
    s.add_replication({"mission_id": "pg-m", "status": "awaiting_operator"})
    assert any(r.get("mission_id") == "pg-m" for r in s.replications())
    s.add_merge({"decision": "escalate_human"})
    assert any(m.get("decision") == "escalate_human" for m in s.merges())
