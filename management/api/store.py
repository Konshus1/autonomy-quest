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

import functools
import json
import os
from datetime import datetime, timezone
from typing import Any

_DEFAULT_WORKSTREAM = {"id": "ws-default", "title": "Default workstream", "status": "active"}
_DB_ENV_ORDER = ("AQ_MGMT_DB_URL", "AQ_DB_URL")


class StoreUnavailable(RuntimeError):
    """The durable store is temporarily unreachable (mid-session DB outage).

    Raised instead of letting a raw psycopg2 error surface as an HTTP 500. The API
    maps this to a stable 503 so a transient DB blip degrades honestly rather than
    leaking a traceback or silently returning empty (which would be data confusion).
    """


def _db_guard(fn):
    """Convert connection-level psycopg2 failures into StoreUnavailable (-> 503)."""

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        import psycopg2

        try:
            return fn(self, *args, **kwargs)
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as exc:
            raise StoreUnavailable(str(exc)) from exc

    return wrapper


def _comm_record(from_handle: str, to_handle: str | None, text: str, kind: str) -> dict[str, Any]:
    """One agent-comms message. ``ts`` is UTC ISO-8601 (both stores stamp it the same way)."""
    return {
        "from_handle": from_handle,
        "to_handle": to_handle,
        "text": text,
        "kind": kind,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


class InMemoryStore:
    """Process-local backing (original v0.1 behavior)."""

    backing = "in_memory_stub"
    # Whether a durable DB was CONFIGURED (so the comms write path must fail closed rather than
    # accept from this process-local memory). build_store() sets this to True on a fallback.
    durability_required = False

    def __init__(self) -> None:
        self._workstreams: list[dict[str, Any]] = [dict(_DEFAULT_WORKSTREAM)]
        self._tasks: list[dict[str, Any]] = []
        self._comms: list[dict[str, Any]] = []
        self._envelopes: list[dict[str, Any]] = []
        # idempotency index: (origin_instance_id, idempotency_key) -> stored envelope
        self._envelope_idem: dict[tuple[str, str], dict[str, Any]] = {}
        # global-id index (relay idempotency + fast lookup for verification).
        self._envelope_by_id: dict[str, dict[str, Any]] = {}
        # parent-owned verification state (#4834 comms Phase 2): envelope_id -> record.
        self._verifications: dict[str, dict[str, Any]] = {}
        # parent-owned IMPORT state (#4834 comms Phase 3): inbound work.request envelope_id -> record.
        # Storage of an inbound work.request is inert; this SEPARATE record tracks the flag-gated
        # importer's outcome (imported/rejected + work_id + gate) without mutating the immutable inbox
        # envelope. It makes re-import idempotent and proves storage alone never actuated.
        self._imports: dict[str, dict[str, Any]] = {}
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
        """Legacy read projection (design §4.1): stored envelopes projected to the old row shape,
        plus any legacy demo rows. Keeps GET /api/agent-comms unbroken for the React poll."""
        from management.api.comms_envelope import legacy_projection

        legacy = [{**c, "id": f"comm-{i + 1}"} for i, c in enumerate(self._comms)]
        projected = [legacy_projection(e, row_id=e["id"]) for e in self._envelopes]
        return legacy + projected

    def create_comm(
        self, from_handle: str, to_handle: str | None, text: str, kind: str
    ) -> dict[str, Any]:
        record = _comm_record(from_handle, to_handle, text, kind)
        self._comms.append(record)
        return {**record, "id": f"comm-{len(self._comms)}"}

    def envelopes(self) -> list[dict[str, Any]]:
        return list(self._envelopes)

    def create_envelope(self, envelope: dict[str, Any]) -> dict[str, Any]:
        """Append a versioned envelope, suppressing an idempotency-key retry (design §4.1).

        Returns ``{"envelope": <stored>, "duplicate": bool}``. A repeat of an existing
        ``(origin_instance_id, idempotency_key)`` returns the ORIGINAL stored envelope with no new
        row — at-least-once delivery without duplicate effect.
        """
        idem = envelope.get("idempotency_key")
        origin = envelope.get("origin_instance_id") or ""
        if idem:
            key = (origin, idem)
            existing = self._envelope_idem.get(key)
            if existing is not None:
                return {"envelope": existing, "duplicate": True}
        self._envelopes.append(envelope)
        eid = envelope.get("id")
        if eid:
            self._envelope_by_id[eid] = envelope
        if idem:
            self._envelope_idem[(origin, idem)] = envelope
        return {"envelope": envelope, "duplicate": False}

    # -- Phase 2: outbox cursor + idempotent relay + verification -------------
    def envelopes_after(self, after_seq: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        """Return ``[{"seq": n, "envelope": ...}]`` for envelopes past a monotonic cursor.

        The host outbox relay (Phase 2) pulls with ``after_seq`` = its per-replica watermark. ``seq``
        is 1-based insertion order — monotonic and stable — so a restarted relay resumes exactly where
        it left off without re-relaying (belt to the idempotency-key/global-id suspenders).
        """
        limit = max(1, min(int(limit), 1000))
        out: list[dict[str, Any]] = []
        for i, env in enumerate(self._envelopes):
            seq = i + 1
            if seq > int(after_seq):
                out.append({"seq": seq, "envelope": env})
                if len(out) >= limit:
                    break
        return out

    def relay_envelope(self, envelope: dict[str, Any]) -> dict[str, Any]:
        """Copy an envelope into THIS journal idempotently on its GLOBAL id (design §4.3).

        The relay preserves the original ``id`` (globally unique + sortable), so re-relaying the same
        envelope — after an at-least-once retry, a relay restart, or teardown mid-transfer — is a no-op
        that returns the already-stored copy. Distinct from ``create_envelope`` (which dedups on the
        author's idempotency_key): the relay dedups on the immutable global id it is copying.
        """
        eid = envelope.get("id")
        if eid and eid in self._envelope_by_id:
            return {"envelope": self._envelope_by_id[eid], "duplicate": True}
        self._envelopes.append(envelope)
        if eid:
            self._envelope_by_id[eid] = envelope
        idem = envelope.get("idempotency_key")
        origin = envelope.get("origin_instance_id") or ""
        if idem:
            self._envelope_idem.setdefault((origin, idem), envelope)
        return {"envelope": envelope, "duplicate": False}

    def set_verification(self, envelope_id: str, *, state: str, verifier: str,
                         reason: str = "", evidence: dict[str, Any] | None = None,
                         verified_at: str | None = None) -> dict[str, Any]:
        """Parent-owned verification state for a replica claim (unverified/verified/rejected).

        Only the host/parent (operator-credentialed API) reaches this; a replica message can NEVER
        set it. It is a SEPARATE record — it does not mutate the immutable claim envelope.
        """
        record = {
            "envelope_id": envelope_id,
            "state": state,
            "verifier": verifier,
            "reason": reason,
            "evidence": evidence or {},
            "verified_at": verified_at or datetime.now(timezone.utc).isoformat(),
        }
        self._verifications[envelope_id] = record
        return record

    def verifications(self) -> dict[str, dict[str, Any]]:
        return dict(self._verifications)

    def set_import(self, envelope_id: str, record: dict[str, Any]) -> dict[str, Any]:
        """Record the flag-gated importer's outcome for an inbound work.request (#4834 comms Phase 3).

        Keyed on the inbound envelope id so a re-import is idempotent (the importer skips an envelope
        already present here) and a crash between store and import leaves no partial actuation. Never
        mutates the immutable inbox envelope; the importer never actuates — it only enqueues a gated
        proposal and records that here."""
        stored = {"envelope_id": envelope_id, **record}
        self._imports[envelope_id] = stored
        return stored

    def imports(self) -> dict[str, dict[str, Any]]:
        return dict(self._imports)

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
            cur.execute("""
                SELECT to_regclass('public.ralph_workstreams'),
                       to_regclass('public.ralph_tasks'),
                       to_regclass('public.ralph_comms'),
                       to_regclass('public.ralph_replication'),
                       to_regclass('public.ralph_merges')
            """)
            migrated = all(value is not None for value in cur.fetchone())
            if not migrated:
                cur.execute(
                    """
                CREATE TABLE IF NOT EXISTS ralph_workstreams (
                    id text PRIMARY KEY, title text NOT NULL, status text NOT NULL);
                CREATE TABLE IF NOT EXISTS ralph_tasks (
                    seq bigserial PRIMARY KEY,
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
            # Comms durable tables (#4834 Phases 0/2/3: envelope journal + parent-owned verification +
            # inbound work-request import state). In production these come from the migration
            # schema/029_comms_envelope_tables.sql — created under the OWNER role BEFORE schema/999's
            # blanket `GRANT ... ON ALL TABLES ... TO aq_loop`, so the runtime loop role gets DML.
            #
            # The runtime aq_loop role must NOT run this DDL itself: `CREATE INDEX` checks table
            # OWNERSHIP before the `IF NOT EXISTS` short-circuit, so it raises for a non-owner even when
            # the index already exists (`CREATE TABLE IF NOT EXISTS` skips cleanly, but the envelope
            # indexes do not). Before this guard, a migrated DB under aq_loop still threw here, the
            # store fell back to the in-memory stub, and every comms write 503'd. So — exactly like the
            # older ralph_* tables above — skip the DDL when the tables already exist; only a fresh /
            # dev / InMemory DB on an owner-privileged DSN materializes them here.
            # Guard each comms table on its OWN existence (not one as a proxy for three), so a rare
            # partial-state DB backfills the missing table rather than skipping it and 500ing later.
            # The envelope table is guarded together with its indexes because CREATE INDEX is the
            # ownership-hazard statement; verifications/imports have no indexes.
            cur.execute(
                """SELECT to_regclass('public.ralph_comms_envelopes'),
                          to_regclass('public.ralph_comms_verifications'),
                          to_regclass('public.ralph_comms_imports')"""
            )
            env_t, ver_t, imp_t = cur.fetchone()
            if env_t is None:
                # Versioned message envelope table (Phase 0, design §4.1). Columns pull
                # identity/routing/ordering out of the JSONB for indexing + a UNIQUE idempotency
                # constraint; the full envelope is retained in ``envelope`` jsonb. Guarded on the
                # table's existence because CREATE INDEX checks table OWNERSHIP before IF NOT EXISTS,
                # so the aq_loop role throws on the indexes even when they exist — a migrated DB
                # (schema/029, owner-created) skips this block entirely.
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ralph_comms_envelopes (
                        seq bigserial PRIMARY KEY,
                        id text NOT NULL UNIQUE,
                        origin_instance_id text NOT NULL,
                        principal_id text NOT NULL,
                        channel text NOT NULL,
                        target_instance_id text,
                        target_handle text,
                        kind text NOT NULL,
                        idempotency_key text,
                        correlation_id text,
                        in_reply_to text,
                        created_at timestamptz NOT NULL DEFAULT now(),
                        expires_at timestamptz,
                        trust text NOT NULL,
                        delivery text NOT NULL,
                        envelope jsonb NOT NULL);
                    CREATE INDEX IF NOT EXISTS ralph_comms_env_channel_idx
                        ON ralph_comms_envelopes (channel, seq);
                    CREATE INDEX IF NOT EXISTS ralph_comms_env_origin_idx
                        ON ralph_comms_envelopes (origin_instance_id, seq);
                    CREATE INDEX IF NOT EXISTS ralph_comms_env_kind_idx
                        ON ralph_comms_envelopes (kind);
                    CREATE UNIQUE INDEX IF NOT EXISTS ralph_comms_env_idem_idx
                        ON ralph_comms_envelopes (origin_instance_id, idempotency_key)
                        WHERE idempotency_key IS NOT NULL;
                    """
                )
            if ver_t is None:
                # Parent-owned verification state (Phase 2). SEPARATE from the immutable claim
                # envelopes: the parent/host records unverified/verified/rejected here WITHOUT ever
                # mutating a replica's claim. A replica has no path to write it.
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ralph_comms_verifications (
                        envelope_id text PRIMARY KEY,
                        state text NOT NULL,
                        verifier text NOT NULL,
                        reason text,
                        evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
                        verified_at timestamptz NOT NULL DEFAULT now());
                    """
                )
            if imp_t is None:
                # Parent-owned IMPORT state for inbound work.requests (Phase 3). SEPARATE from the
                # immutable inbox envelope: the flag-gated importer records imported/rejected + the
                # local work_id + gate outcome here WITHOUT mutating the stored request. Keyed on
                # envelope_id => idempotent re-import + no partial actuation on crash.
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ralph_comms_imports (
                        envelope_id text PRIMARY KEY,
                        record jsonb NOT NULL,
                        imported_at timestamptz NOT NULL DEFAULT now());
                    """
                )

    @_db_guard
    def workstreams(self) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT id, title, status FROM ralph_workstreams ORDER BY id")
            return [{"id": r[0], "title": r[1], "status": r[2]} for r in cur.fetchall()]

    @_db_guard
    def tasks(self) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            # id is DERIVED from the atomic serial seq — never a MAX(seq)+1 read.
            cur.execute(
                "SELECT seq, title, workstream_id, status FROM ralph_tasks ORDER BY seq"
            )
            return [
                {"id": f"task-{r[0]}", "title": r[1], "workstream_id": r[2], "status": r[3]}
                for r in cur.fetchall()
            ]

    @_db_guard
    def create_task(self, title: str, workstream_id: str) -> dict[str, Any]:
        with self._connect() as conn, conn.cursor() as cur:
            # Atomic: the serial seq is the id source, so concurrent inserts cannot
            # collide (the old MAX(seq)+1 raced — 151/200 UniqueViolations under load).
            cur.execute(
                "INSERT INTO ralph_tasks (title, workstream_id, status) "
                "VALUES (%s, %s, 'open') RETURNING seq",
                (title, workstream_id),
            )
            seq = cur.fetchone()[0]
            return {"id": f"task-{seq}", "title": title, "workstream_id": workstream_id,
                    "status": "open"}

    @_db_guard
    def comms(self) -> list[dict[str, Any]]:
        """Legacy read projection (design §4.1): envelopes projected to the old row shape, plus any
        legacy ``ralph_comms`` demo rows. Ordering is by monotonic ULID/seq so the React poll and a
        cursor consumer agree on order."""
        from management.api.comms_envelope import legacy_projection

        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT seq, payload FROM ralph_comms ORDER BY seq")
            legacy = [{**r[1], "id": f"comm-{r[0]}"} for r in cur.fetchall()]
            cur.execute("SELECT id, envelope FROM ralph_comms_envelopes ORDER BY seq")
            projected = [legacy_projection(r[1], row_id=r[0]) for r in cur.fetchall()]
        return legacy + projected

    @_db_guard
    def create_comm(
        self, from_handle: str, to_handle: str | None, text: str, kind: str
    ) -> dict[str, Any]:
        record = _comm_record(from_handle, to_handle, text, kind)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ralph_comms (payload) VALUES (%s) RETURNING seq",
                (json.dumps(record),),
            )
            seq = cur.fetchone()[0]
        return {**record, "id": f"comm-{seq}"}

    @_db_guard
    def envelopes(self) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT envelope FROM ralph_comms_envelopes ORDER BY seq")
            return [r[0] for r in cur.fetchall()]

    def create_envelope(self, envelope: dict[str, Any]) -> dict[str, Any]:
        """Durably insert a versioned envelope, suppressing an idempotency-key retry (design §4.1).

        A duplicate ``(origin_instance_id, idempotency_key)`` violates the partial unique index; we
        catch it and return the ORIGINAL row (no new effect). A connection-level DB outage propagates
        as ``StoreUnavailable`` (-> 503) via ``_db_guard`` — the write NEVER falls back to memory.
        """
        import psycopg2

        try:
            return self._create_envelope(envelope)
        except psycopg2.IntegrityError:
            # idempotency-key collision: return the stored original as a suppressed duplicate.
            existing = self._get_envelope_by_idem(
                envelope.get("origin_instance_id") or "", envelope.get("idempotency_key"))
            if existing is not None:
                return {"envelope": existing, "duplicate": True}
            raise

    @_db_guard
    def _create_envelope(self, envelope: dict[str, Any]) -> dict[str, Any]:
        target = envelope.get("target") or {}
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ralph_comms_envelopes
                    (id, origin_instance_id, principal_id, channel, target_instance_id,
                     target_handle, kind, idempotency_key, correlation_id, in_reply_to,
                     expires_at, trust, delivery, envelope)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id
                """,
                (
                    envelope["id"], envelope["origin_instance_id"], envelope["principal_id"],
                    envelope["channel"], target.get("instance_id"), target.get("handle"),
                    envelope["kind"], envelope.get("idempotency_key"),
                    envelope.get("correlation_id"), envelope.get("in_reply_to"),
                    envelope.get("expires_at"), envelope["trust"], envelope["delivery"],
                    json.dumps(envelope),
                ),
            )
        return {"envelope": envelope, "duplicate": False}

    @_db_guard
    def _get_envelope_by_idem(self, origin: str, idem: str | None) -> dict[str, Any] | None:
        if not idem:
            return None
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT envelope FROM ralph_comms_envelopes "
                "WHERE origin_instance_id=%s AND idempotency_key=%s",
                (origin, idem),
            )
            row = cur.fetchone()
        return row[0] if row else None

    # -- Phase 2: outbox cursor + idempotent relay + verification -------------
    @_db_guard
    def envelopes_after(self, after_seq: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT seq, envelope FROM ralph_comms_envelopes "
                "WHERE seq > %s ORDER BY seq LIMIT %s",
                (int(after_seq), limit),
            )
            return [{"seq": r[0], "envelope": r[1]} for r in cur.fetchall()]

    def relay_envelope(self, envelope: dict[str, Any]) -> dict[str, Any]:
        """Idempotent relay copy keyed on the immutable GLOBAL id (design §4.3). ``ON CONFLICT (id)
        DO NOTHING`` makes a re-relay a no-op; a StoreUnavailable (503) never falls back to memory."""
        target = envelope.get("target") or {}
        with_conflict = self._relay_insert(envelope, target)
        return with_conflict

    @_db_guard
    def _relay_insert(self, envelope: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ralph_comms_envelopes
                    (id, origin_instance_id, principal_id, channel, target_instance_id,
                     target_handle, kind, idempotency_key, correlation_id, in_reply_to,
                     expires_at, trust, delivery, envelope)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (id) DO NOTHING
                RETURNING id
                """,
                (
                    envelope["id"], envelope["origin_instance_id"], envelope["principal_id"],
                    envelope["channel"], target.get("instance_id"), target.get("handle"),
                    envelope["kind"], envelope.get("idempotency_key"),
                    envelope.get("correlation_id"), envelope.get("in_reply_to"),
                    envelope.get("expires_at"), envelope["trust"], envelope["delivery"],
                    json.dumps(envelope),
                ),
            )
            inserted = cur.fetchone() is not None
        return {"envelope": envelope, "duplicate": not inserted}

    @_db_guard
    def set_verification(self, envelope_id: str, *, state: str, verifier: str,
                         reason: str = "", evidence: dict[str, Any] | None = None,
                         verified_at: str | None = None) -> dict[str, Any]:
        record = {
            "envelope_id": envelope_id, "state": state, "verifier": verifier,
            "reason": reason, "evidence": evidence or {},
            "verified_at": verified_at or datetime.now(timezone.utc).isoformat(),
        }
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ralph_comms_verifications
                    (envelope_id, state, verifier, reason, evidence, verified_at)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (envelope_id) DO UPDATE SET
                    state=EXCLUDED.state, verifier=EXCLUDED.verifier, reason=EXCLUDED.reason,
                    evidence=EXCLUDED.evidence, verified_at=EXCLUDED.verified_at
                """,
                (envelope_id, state, verifier, reason, json.dumps(record["evidence"]),
                 record["verified_at"]),
            )
        return record

    @_db_guard
    def verifications(self) -> dict[str, dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT envelope_id, state, verifier, reason, evidence, verified_at "
                "FROM ralph_comms_verifications")
            out: dict[str, dict[str, Any]] = {}
            for r in cur.fetchall():
                out[r[0]] = {
                    "envelope_id": r[0], "state": r[1], "verifier": r[2], "reason": r[3],
                    "evidence": r[4] or {},
                    "verified_at": r[5].isoformat() if hasattr(r[5], "isoformat") else r[5],
                }
            return out

    @_db_guard
    def set_import(self, envelope_id: str, record: dict[str, Any]) -> dict[str, Any]:
        stored = {"envelope_id": envelope_id, **record}
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ralph_comms_imports (envelope_id, record)
                VALUES (%s, %s)
                ON CONFLICT (envelope_id) DO NOTHING
                """,
                (envelope_id, json.dumps(stored)),
            )
        return stored

    @_db_guard
    def imports(self) -> dict[str, dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT envelope_id, record FROM ralph_comms_imports")
            return {r[0]: r[1] for r in cur.fetchall()}

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

    @_db_guard
    def _list_jsonb(self, table: str) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT payload FROM {table} ORDER BY seq")  # noqa: S608 (fixed names)
            return [r[0] for r in cur.fetchall()]

    @_db_guard
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
        store = InMemoryStore()
        # No DB configured: in-memory IS the intended durable backing here (pytest / no-DB local),
        # so the comms write path may accept from it.
        store.durability_required = False  # type: ignore[attr-defined]
        return store
    try:
        return PgStore(dsn)
    except Exception as exc:  # pragma: no cover - defensive fallback
        # A DB WAS configured but is unreachable at startup. Reads still degrade to in-memory so the
        # API boots, but the comms WRITE path must FAIL CLOSED (design §2.2): durability_required=True
        # tells the publish endpoint to 503 rather than acknowledge from this ephemeral fallback.
        print(f"[management] Postgres store unavailable ({exc}); using in-memory backing "
              "(comms writes FAIL CLOSED — durability required but not available).")
        store = InMemoryStore()
        store.durability_required = True  # type: ignore[attr-defined]
        return store
