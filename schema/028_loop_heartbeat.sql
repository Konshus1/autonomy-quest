-- DEPLOY-HYGIENE liveness (#4834): a singleton row proving a real DECIDE cycle completed recently.
--
-- The gap this closes: the live loop crash-looped on missing codex auth and sat DOWN ~27h with
-- nobody noticing, because "health" meant process-up, not "a cycle actually turned". This table is
-- upserted once per completed cycle() from a FAIL-SAFE hook, and /health reads it so "healthy" can
-- mean the loop is genuinely TURNING within a freshness threshold.
--
-- Deliberately DISTINCT from two neighbours:
--   * loop_runtime (012) — a 2s PROCESS pulse: "the process exists", not "a cycle finished".
--   * heartbeat (005)    — the turning/hibernating/rate_limited STATE gate, which by design can
--                          never count as progress. This table is neither progress nor authority;
--                          it is a pure liveness signal for operators/monitors.
BEGIN;

CREATE TABLE IF NOT EXISTS loop_heartbeat (
  singleton     boolean     PRIMARY KEY DEFAULT true CHECK (singleton),
  last_cycle_at timestamptz NOT NULL DEFAULT now(),
  cycle_count   bigint      NOT NULL DEFAULT 0
);

-- aq_loop upserts one row per completed cycle, so it needs INSERT *and* UPDATE. This is a liveness
-- signal, not an authority/evidence relation, so UPDATE is appropriate here (unlike the append-only
-- self-correction tables, which keep INSERT but lose UPDATE/DELETE). The blanket grant in
-- schema/999 already covers this table; this explicit grant makes the migration self-sufficient
-- when applied stand-alone. External installs without the container roles skip it.
DO $grant$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aq_loop') THEN
    EXECUTE 'GRANT SELECT, INSERT, UPDATE ON loop_heartbeat TO aq_loop';
  END IF;
END $grant$;

COMMIT;
