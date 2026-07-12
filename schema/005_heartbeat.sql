-- A heartbeat, so a correctly-WAITING loop is not mistaken for a DEAD one.
--
-- The conflict this resolves: the container HEALTHCHECK and the UI both gate on "a cycle
-- completed recently". A hibernating loop completes no cycles — so our own healthcheck would have
-- reported it unhealthy and RESTARTED it, and the UI would have shown STALLED. The guard would
-- have killed the thing it was guarding.
--
-- Hibernating and stalled are OPPOSITES. Stalled = something is wrong. Hibernating = the system
-- did the right thing, stopped spending, and is waiting for a human. So the heartbeat proves the
-- process is alive WITHOUT counting as progress: it can never satisfy the loop-is-turning gate,
-- because that gate requires a completed run JOINed to a learning, and a heartbeat is neither.
BEGIN;

CREATE TABLE IF NOT EXISTS heartbeat (
  id       bigserial PRIMARY KEY,
  beat_at  timestamptz NOT NULL DEFAULT now(),
  state    text        NOT NULL CHECK (state IN ('turning','hibernating','rate_limited')),
  detail   text
);
CREATE INDEX IF NOT EXISTS heartbeat_recent_idx ON heartbeat (beat_at DESC);

COMMIT;
