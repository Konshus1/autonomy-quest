-- Comms durable tables (#4834 agent-comms Phases 0/2/3). MIGRATION FIX.
--
-- These three tables back the agent-comms feature (envelope journal + parent-owned verification +
-- inbound work-request import state). They were only ever created LAZILY by
-- management/api/store.py::PgStore._ensure_schema (CREATE TABLE IF NOT EXISTS on first use) and were
-- absent from the migration schema. Because schema/999_container_role_grants.sql REVOKEs CREATE ON
-- SCHEMA public FROM aq_loop, the runtime app role could never create them, so the comms store failed
-- to initialise and EVERY comms write fell closed to 503 in a real deployment (the feature landed
-- inert and its durable store never actually ran in-container). Surfaced by the real two-stack e2e
-- (tests/test_comms_two_stack_live_e2e.py) — see COMMS_E2E.md.
--
-- Fix: create the three tables HERE, numbered < 999, so they exist BEFORE
-- schema/999 runs `GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO aq_loop`.
-- aq_loop then gets the DML it needs (INSERT + SELECT), never CREATE. 999 additionally REVOKEs the
-- privileges aq_loop must NOT hold on these: the envelope journal is IMMUTABLE and the import ledger
-- is append-only (the store only INSERTs them), so 999 revokes UPDATE/DELETE/TRUNCATE on
-- ralph_comms_envelopes + ralph_comms_imports; verification is a parent-owned upsert so it keeps
-- INSERT+UPDATE but loses DELETE/TRUNCATE. This keeps the design invariant ("a replica's claim is
-- never mutated"; "verification is parent-owned") enforced at the row level, not just the API —
-- there is no immutability trigger on these tables, so the grant is the only DB-layer guard.
--
-- Idempotent (IF NOT EXISTS) and a BYTE-FOR-BYTE mirror of the store's DDL, so a DB that already
-- materialised them lazily (via the owner role) is unchanged, and a fresh DB gets them at migrate
-- time. The lazy CREATE in _ensure_schema stays as a dev/InMemory/owner convenience; once these
-- exist it is a no-op (CREATE ... IF NOT EXISTS skips an existing relation without requiring CREATE,
-- exactly as it already does for the older migrated ralph_* tables the store also re-declares).

-- Versioned message envelope journal (Phase 0, design §4.1).
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

-- Parent-owned verification state (Phase 2). Separate from the immutable claim; a replica has no
-- path to write it (that stays an API/ACL property — here it is just a loop-writable table).
CREATE TABLE IF NOT EXISTS ralph_comms_verifications (
    envelope_id text PRIMARY KEY,
    state text NOT NULL,
    verifier text NOT NULL,
    reason text,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    verified_at timestamptz NOT NULL DEFAULT now());

-- Parent-owned import state for inbound work.requests (Phase 3). Keyed on envelope_id => idempotent
-- re-import + no partial actuation on crash. The importer records the outcome here; it never mutates
-- the stored request.
CREATE TABLE IF NOT EXISTS ralph_comms_imports (
    envelope_id text PRIMARY KEY,
    record jsonb NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT now());
