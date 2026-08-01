"""Durable causal-edge store (slice 1c) — Postgres-backed, identity-keyed.

Mirrors ``InMemoryCausalEdgeStore`` (ralph_portable.causal_edge_store) with the SAME interface,
persisting to a ``ralph_causal_edges`` table whose PRIMARY KEY is the immutable edge_identity
(cause, effect, scope). The full edge (dials + certainty + support + evidence) is stored as
jsonb — the mutable state is never part of the key (the ccf-1557 C2 invariant). InMemory
remains the default when no DB URL is configured, so the contract stays testable without a DB.
"""

from __future__ import annotations

import json
import os
from typing import Any

from management.api.store import StoreUnavailable, _db_guard
from ralph_portable.causal_edge_store import InMemoryCausalEdgeStore
from ralph_portable.causal_edges import (
    edge_identity,
    plan_certainty,
    propose_update,
    validate_causal_edge,
)

_DB_ENV_ORDER = ("AQ_MGMT_DB_URL", "AQ_DB_URL")


class PgCausalEdgeStore:
    backing = "postgres"

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._init_schema()

    def _connect(self):  # pragma: no cover - thin wrapper
        import psycopg2

        conn = psycopg2.connect(self._dsn)
        conn.autocommit = True
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS ralph_causal_edges (
                    cause  text NOT NULL,
                    effect text NOT NULL,
                    scope  text NOT NULL,
                    edge   jsonb NOT NULL,
                    PRIMARY KEY (cause, effect, scope)
                );
                """
            )

    @_db_guard
    def put(self, edge: dict[str, Any]) -> tuple[str, str, str]:
        errors = validate_causal_edge(edge)
        if errors:
            raise ValueError("; ".join(errors))
        cause, effect, scope = edge_identity(edge)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT edge FROM ralph_causal_edges WHERE cause=%s AND effect=%s AND scope=%s",
                (cause, effect, scope),
            )
            row = cur.fetchone()
            new = dict(edge)
            if row is not None:  # UPSERT preserves accumulated support/evidence across a dial change
                old = row[0]
                new.setdefault("support_count", old.get("support_count", 0))
                new.setdefault("evidence", old.get("evidence", []))
            cur.execute(
                "INSERT INTO ralph_causal_edges (cause, effect, scope, edge) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (cause, effect, scope) DO UPDATE SET edge = EXCLUDED.edge",
                (cause, effect, scope, json.dumps(new)),
            )
        return (cause, effect, scope)

    @_db_guard
    def get(self, ident: tuple[str, str, str]) -> dict[str, Any] | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT edge FROM ralph_causal_edges WHERE cause=%s AND effect=%s AND scope=%s",
                (ident[0], ident[1], ident[2]),
            )
            row = cur.fetchone()
            return row[0] if row else None

    @_db_guard
    def all(self) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT edge FROM ralph_causal_edges ORDER BY cause, effect, scope")
            return [r[0] for r in cur.fetchall()]

    def governing(self, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        keys = {(str(s.get("action")), str(s.get("effect"))) for s in steps}
        return [e for e in self.all() if (str(e["cause"]), str(e["effect"])) in keys]

    def assess_plan(self, steps: list[dict[str, Any]]) -> dict[str, Any]:
        return plan_certainty(steps, self.all())

    @_db_guard
    def record_evidence(self, ident: tuple[str, str, str], surprise_result: dict[str, Any]) -> dict[str, Any]:
        edge = self.get(ident)
        if edge is None:
            raise KeyError(f"no edge with identity {ident!r}")
        edge.setdefault("evidence", []).append(surprise_result)
        if surprise_result.get("signal") == "confirm":
            edge["support_count"] = int(edge.get("support_count") or 0) + 1
        # persist the updated mutable state at the SAME immutable identity
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE ralph_causal_edges SET edge=%s WHERE cause=%s AND effect=%s AND scope=%s",
                (json.dumps(edge), ident[0], ident[1], ident[2]),
            )
        return propose_update(edge, surprise_result)


    @_db_guard
    def mine_from_mission_loop(self) -> dict[str, Any]:
        """Mine candidate FUZZY causal edges from the mission loop's own outcomes.

        Joins completed+succeeded runs to their work and learning; a run whose measure moved
        becomes evidence that ``work.kind`` caused that move. Mined edges are upserted with
        provenance (BB #764). Returns {mined, edges}.
        """
        import psycopg2.errors

        from ralph_portable.principle_mining import mine_causal_edges

        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT w.kind, r.succeeded, r.measure_before, r.measure_after,
                           l.insight, l.confidence, r.id, w.id, l.id
                    FROM runs r
                    JOIN work w      ON w.id = r.work_id
                    JOIN learnings l ON l.run_id = r.id
                    WHERE r.completed_at IS NOT NULL AND r.succeeded = true
                    """
                )
                rows = cur.fetchall()
        except psycopg2.errors.UndefinedTable as exc:
            # The causal DB is reachable but lacks the mission schema (a split-DB deployment:
            # ralph_causal_edges present, runs/work/learnings absent). Degrade to a stable 503
            # instead of a 500 traceback — nothing to mine from here.
            raise StoreUnavailable(
                "mission tables (runs/work/learnings) are not present in the causal DB"
            ) from exc
        obs = [
            {
                "work_kind": row[0], "succeeded": row[1],
                "measure_before": float(row[2]) if row[2] is not None else None,
                "measure_after": float(row[3]) if row[3] is not None else None,
                "learning_insight": row[4],
                "learning_confidence": float(row[5]) if row[5] is not None else 0.0,
                "run_id": row[6], "work_id": row[7], "learning_id": row[8],
            }
            for row in rows
        ]
        edges = mine_causal_edges(obs)
        for e in edges:
            self.put(e)
        return {"mined": len(edges), "edges": edges}


def build_causal_store(env: dict[str, str] | None = None) -> Any:
    """Postgres store when a DB URL is reachable, else in-memory (a DB blip never 500s)."""
    source = env if env is not None else os.environ
    dsn = next((source[k] for k in _DB_ENV_ORDER if source.get(k)), None)
    if not dsn:
        return InMemoryCausalEdgeStore()
    try:
        return PgCausalEdgeStore(dsn)
    except Exception as exc:  # pragma: no cover - defensive fallback
        print(f"[causal] Postgres store unavailable ({exc}); using in-memory backing.")
        return InMemoryCausalEdgeStore()
