-- 031 — measure source: the un-forgeable scoreboard (the honesty mechanism, reproducible).
--
-- The mission MEASURE is a real number the acting agent is graded on (e.g. signups, tickets
-- closed). The entire honesty claim rests on one property: the untrusted act-phase credential
-- (aq_actor) may READ the measure but must NEVER be able to WRITE it. Otherwise the loop could
-- grade itself against a scoreboard it can forge. This is enforced by a plain PostgreSQL GRANT
-- (SELECT only), not a policy and not a prompt. Only an isolated writer role (aq_measure_writer)
-- can move the number.
--
-- Reproduce it yourself: provision the container roles, apply the schema, then run
--   AQ_REQUIRE_DBSKILL_GRANTS=1 AQ_DBSKILL_ACTOR_DSN=... pytest tests/test_measure_grant_boundary.py
-- You will watch aq_actor get "permission denied" when its credential tries to write analytics_events.

CREATE TABLE IF NOT EXISTS analytics_events (
  id           bigserial PRIMARY KEY,
  event_type   text        NOT NULL,
  occurred_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS analytics_events_type_time_idx
  ON analytics_events (event_type, occurred_at);

-- Grants run only when the container roles exist; external installs skip this block.
DO $measure_grants$
BEGIN
  -- The isolated writer role: the ONLY non-owner that can move the measure. Nothing the acting
  -- agent controls has this role.
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='aq_measure_writer') THEN
    CREATE ROLE aq_measure_writer NOLOGIN;
  END IF;
  EXECUTE 'GRANT INSERT, SELECT ON analytics_events TO aq_measure_writer';
  EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE analytics_events_id_seq TO aq_measure_writer';

  -- aq_actor (untrusted act-phase) and aq_loop READ the measure to grade progress; neither may write
  -- it. Those read/no-write grants are (re-)asserted in schema/999_container_role_grants.sql, which
  -- runs LAST: 999 revokes aq_actor's blanket read and hands aq_loop a blanket write, then re-grants
  -- the SELECT and strips the writes so the boundary survives a full lexical apply. See 999 for the
  -- exact lines (search "analytics_events").
END $measure_grants$;
