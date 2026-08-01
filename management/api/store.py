"""Durable state backing for the management API (Task #4407, slice v6).

Two interchangeable stores behind one interface so the API response *shapes* never
change (contract locked with ccf-kevin-bootstrap's React shell, v7):

- ``InMemoryStore``  — process-local lists. Default when no DB is configured, so
  pytest and a no-DB local uvicorn keep working unchanged.
- ``PgStore``        — Postgres-backed (psycopg2). Selected when a DB URL is present
  and reachable. Tables are ``ralph_``-prefixed and created with
  ``CREATE TABLE IF NOT EXISTS`` (additive; never touches the AQ mission schema).

``build_store()`` picks Pg when possible and *falls back to in-memory* on any
connection/psycopg2 failure — a DB blip must never 500 the management API.

Only ``state.backing`` changes observably ("in_memory_stub" -> "postgres"); every
field shape stays identical.
"""

from __future__ import annotations

import json
import os
from typing import Any

_DEFAULT_WORKSTREAM = {"id": "ws-default", "title": "Default workstream", "status": "active"}
_DB_ENV_ORDER = ("AQ_MGMT_DB_URL", "AQ_DB_URL")


class InMemoryStore:
    """Process-local backing (original v0.1 behavior)."""

    backing = "in_memory_stub"

    def __init__(self) -> None:
        self._workstreams: list[dict[str, Any]] = [dict(_DEFAULT_WORKSTREAM)]
        self._tasks: list[dict[str, Any]] = []
        self._comms: list[dict[str, Any]] = []
        self._replication: list[dict[str, Any]] = []
        self._merges: list[dict[str, Any]] = []

    def workstreams(self) -> list[dict[str, Any]]:
        return list(self._workstreams)

    def tasks(self) -> list[dict[str, Any]]:
        return list(self._tasks)

    def create_task(self, title: str, workstream_id: str) -> dict[str, Any]:
        item = {
            "id": f"task-{len(self._tasks) + 1}",
            "title": title,
            "workstream_id": workstream_id,
            "status": "open",
        }
        self._tasks.append(item)
        return item

    def comms(self) -> list[dict[str, Any]]:
        return list(self._comms)

    def replications(self) -> list[dict[str, Any]]:
        return list(self._replication)

    def add_replication(self, record: dict[str, Any]) -> dict[str, Any]:
        self._replication.append(record)
        return record

    def merges(self) -> list[dict[str, Any]]:
        return list(self._merges)

    def add_merge(self, record: dict[str, Any]) -> dict[str, Any]:
        self._merges.append(record)
        return record


class PgStore:
    """Postgres-backed store. Short-lived connection per op (low-QPS admin API)."""

    backing = "postgres"

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._init_schema()

    # -- connection helper -------------------------------------------------
    def _connect(self):  # pragma: no cover - thin wrapper
        import psycopg2  # lazy: in-memory path never imports psycopg2

        conn = psycopg2.connect(self._dsn)
        conn.autocommit = True
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS ralph_workstreams (
                    id text PRIMARY KEY, title text NOT NULL, status text NOT NULL);
                CREATE TABLE IF NOT EXISTS ralph_tasks (
                    seq bigserial PRIMARY KEY, id text UNIQUE NOT NULL,
                    title text NOT NULL, workstream_id text NOT NULL, status text NOT NULL);
                CREATE TABLE IF NOT EXISTS ralph_comms (
                    seq bigserial PRIMARY KEY, payload jsonb NOT NULL);
                CREATE TABLE IF NOT EXISTS ralph_replication (
                    seq bigserial PRIMARY KEY, payload jsonb NOT NULL);
                CREATE TABLE IF NOT EXISTS ralph_merges (
                    seq bigserial PRIMARY KEY, payload jsonb NOT NULL);
                """
            )
            # Seed the default workstream once (idempotent).
            cur.execute(
                "INSERT INTO ralph_workstreams (id, title, status) VALUES (%s, %s, %s) "
                "ON CONFLICT (id) DO NOTHING",
                (_DEFAULT_WORKSTREAM["id"], _DEFAULT_WORKSTREAM["title"], _DEFAULT_WORKSTREAM["status"]),
            )

    def workstreams(self) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT id, title, status FROM ralph_workstreams ORDER BY id")
            return [{"id": r[0], "title": r[1], "status": r[2]} for r in cur.fetchall()]

    def tasks(self) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, title, workstream_id, status FROM ralph_tasks ORDER BY seq"
            )
            return [
                {"id": r[0], "title": r[1], "workstream_id": r[2], "status": r[3]}
                for r in cur.fetchall()
            ]

    def create_task(self, title: str, workstream_id: str) -> dict[str, Any]:
        with self._connect() as conn, conn.cursor() as cur:
            # id shape "task-N" preserved via the row's own serial seq.
            cur.execute(
                "INSERT INTO ralph_tasks (id, title, workstream_id, status) "
                "VALUES ('task-' || (SELECT COALESCE(MAX(seq), 0) + 1 FROM ralph_tasks), "
                "%s, %s, 'open') RETURNING id, title, workstream_id, status",
                (title, workstream_id),
            )
            r = cur.fetchone()
            return {"id": r[0], "title": r[1], "workstream_id": r[2], "status": r[3]}

    def comms(self) -> list[dict[str, Any]]:
        return self._list_jsonb("ralph_comms")

    def replications(self) -> list[dict[str, Any]]:
        return self._list_jsonb("ralph_replication")

    def add_replication(self, record: dict[str, Any]) -> dict[str, Any]:
        self._insert_jsonb("ralph_replication", record)
        return record

    def merges(self) -> list[dict[str, Any]]:
        return self._list_jsonb("ralph_merges")

    def add_merge(self, record: dict[str, Any]) -> dict[str, Any]:
        self._insert_jsonb("ralph_merges", record)
        return record

    def _list_jsonb(self, table: str) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT payload FROM {table} ORDER BY seq")  # noqa: S608 (fixed names)
            return [r[0] for r in cur.fetchall()]

    def _insert_jsonb(self, table: str, record: dict[str, Any]) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {table} (payload) VALUES (%s)",  # noqa: S608 (fixed names)
                (json.dumps(record),),
            )


def build_store(env: dict[str, str] | None = None) -> Any:
    """Select a store: Postgres when a reachable DB URL is configured, else in-memory.

    Any failure importing psycopg2 or connecting falls back to InMemoryStore so the
    API always comes up.
    """
    source = env if env is not None else os.environ
    dsn = next((source[k] for k in _DB_ENV_ORDER if source.get(k)), None)
    if not dsn:
        return InMemoryStore()
    try:
        return PgStore(dsn)
    except Exception as exc:  # pragma: no cover - defensive fallback
        print(f"[management] Postgres store unavailable ({exc}); using in-memory backing.")
        return InMemoryStore()
