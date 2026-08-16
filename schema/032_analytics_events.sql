-- 032 — real-world analytics events (W1b measure bridge, task #6087).
--
-- The mission `measure` can now read a REAL-WORLD outcome (traffic / signups / conversions) instead
-- of only the business DB. This is the v1 source: a self-hosted, APPEND-ONLY events table the
-- marketing site writes to. It is buildable and testable with NO external service, and it is shaped
-- so a Plausible-style stats-API adapter can replace it later without touching the loop/grade code.
--
-- THE LOAD-BEARING BOUNDARY: the acting agent (aq_actor — the untrusted act-phase credential) must
-- NOT be able to WRITE this table. If it could, it could forge its own scoreboard and the loop would
-- grade the mission against a number the running system invented — defeating the entire "can't lie
-- about results" thesis. The agent may only SELECT it (advisory planning read via the `aqmeasure`
-- skill); the loop's own host-side read is the authority. Writes come from a SEPARATE site-ingest
-- credential, never from inside the loop or the agent.
--
-- Guarded + idempotent, mirroring schema/999: the table is created unconditionally (so later
-- migrations and the loop's read can rely on it), the GRANTs run only when the container roles
-- exist (external installs without them skip the grant block).

BEGIN;

SET search_path = public, ag_catalog, "$user";

-- ---------------------------------------------------------------------------
-- ANALYTICS_EVENTS — append-only real-world outcome stream written by the site.
--   event_type  — 'pageview' | 'signup' | 'conversion' | ... (what happened)
--   occurred_at — when it happened at the site (the freshness clock reads max(occurred_at))
--   ingested_at — when this row landed here (pipeline lag observability)
-- No UPDATE/DELETE is ever issued against it by any AQ role; it is an immutable ledger of what the
-- outside world actually did.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS analytics_events (
  id            bigserial   PRIMARY KEY,
  event_type    text        NOT NULL,
  occurred_at   timestamptz NOT NULL DEFAULT now(),
  ingested_at   timestamptz NOT NULL DEFAULT now(),
  visitor_id    text,
  path          text,
  source        text,                              -- which ingest wrote it (e.g. 'site', 'plausible')
  props         jsonb       NOT NULL DEFAULT '{}'::jsonb
);

-- The measure reads count(*) filtered by event_type, and freshness reads max(occurred_at). Index
-- both so a busy events table does not make every grade a seq scan.
CREATE INDEX IF NOT EXISTS analytics_events_type_time_idx
  ON analytics_events (event_type, occurred_at);
CREATE INDEX IF NOT EXISTS analytics_events_occurred_idx
  ON analytics_events (occurred_at);

-- ---------------------------------------------------------------------------
-- Least-privilege grants. Only when the container roles exist.
-- ---------------------------------------------------------------------------
DO $analytics_grants$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='aq_loop')
     OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='aq_actor') THEN
    RAISE NOTICE 'container database roles absent; skipping analytics_events grants';
    RETURN;
  END IF;

  -- aq_actor (UNTRUSTED act-phase agent): READ-ONLY. It may SELECT the events table so the curated
  -- `aqmeasure` skill can show it the current real-world number for PLANNING — but it can NEVER
  -- write it. Revoke every write first (defense-in-depth against a re-appearing blanket grant),
  -- then grant SELECT only. THIS is the "agent cannot forge its own scoreboard" boundary.
  EXECUTE 'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON analytics_events FROM aq_actor';
  EXECUTE 'GRANT SELECT ON analytics_events TO aq_actor';

  -- aq_loop (the loop's own host-side credential) performs the AUTHORITATIVE grade-time read, so it
  -- needs SELECT. But the analytics stream is EXTERNAL ground truth: nothing inside AQ — not even
  -- the trusted loop — may write it, or the number would no longer be un-forgeable. schema/999's
  -- blanket GRANT hands aq_loop all four verbs on every public table; revoke the writes here so the
  -- table stays append-only-from-outside. (999 runs after 032 and re-asserts this same revoke.)
  EXECUTE 'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON analytics_events FROM aq_loop';
  EXECUTE 'GRANT SELECT ON analytics_events TO aq_loop';

  -- The SITE-INGEST credential is the ONLY writer, and it is APPEND-ONLY (INSERT, no UPDATE/DELETE).
  -- It is provisioned out-of-band by the deploy (the site process, not the loop or the agent). Grant
  -- it only if it exists so this migration is safe on a stack that has not created it yet.
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='aq_site_ingest') THEN
    EXECUTE 'GRANT INSERT ON analytics_events TO aq_site_ingest';
    EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE analytics_events_id_seq TO aq_site_ingest';
    EXECUTE 'REVOKE UPDATE, DELETE, TRUNCATE ON analytics_events FROM aq_site_ingest';
  END IF;

  -- Trusted read-only principals may observe it too (parity with the rest of the ledger).
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='aq_governance') THEN
    EXECUTE 'GRANT SELECT ON analytics_events TO aq_governance';
    EXECUTE 'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON analytics_events FROM aq_governance';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='aq_evaluator') THEN
    EXECUTE 'GRANT SELECT ON analytics_events TO aq_evaluator';
    EXECUTE 'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON analytics_events FROM aq_evaluator';
  END IF;
END $analytics_grants$;

COMMIT;
