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


def test_in_memory_comms_create_and_shape() -> None:
    s = InMemoryStore()
    assert s.comms() == []
    item = s.create_comm("mgr-1", "worker-2", "status?", "question")
    assert item["id"] == "comm-1"
    assert item["from_handle"] == "mgr-1"
    assert item["to_handle"] == "worker-2"
    assert item["kind"] == "question"
    assert isinstance(item["ts"], str) and "T" in item["ts"]
    listed = s.comms()
    assert len(listed) == 1
    assert listed[0]["id"] == "comm-1"
    # broadcast (no target) allowed
    b = s.create_comm("mgr-1", None, "all hands", "message")
    assert b["id"] == "comm-2" and b["to_handle"] is None


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
    c = s.create_comm("mgr-1", "worker-2", "pg comm", "message")
    assert c["id"].startswith("comm-")
    assert any(x["id"] == c["id"] and x["text"] == "pg comm" for x in s.comms())


@pytest.mark.skipif(
    not os.environ.get("AQ_TEST_DB_URL"),
    reason="set AQ_TEST_DB_URL to a reachable Postgres to run the concurrency regression",
)
def test_pg_concurrent_task_creates_have_no_id_collision() -> None:
    """Regression for codex #45623: MAX(seq)+1 raced (49/200 succeeded). Seq-derived ids
    must let N concurrent creators all succeed with unique ids."""
    import concurrent.futures

    from management.api.store import PgStore

    n = 60
    ids: list[str] = []
    errors: list[str] = []

    def _create(i: int) -> None:
        try:
            s = PgStore(os.environ["AQ_TEST_DB_URL"])  # per-thread store (own connections)
            ids.append(s.create_task(f"race-{i}", "ws-default")["id"])
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
        list(pool.map(_create, range(n)))

    assert not errors, f"concurrent creates raised: {errors[:3]}"
    assert len(ids) == n
    assert len(set(ids)) == n, "task id collision under concurrency"
