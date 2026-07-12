-- ARTIFACT-OR-ESCALATE: was this cycle real, or did it just narrate?
--
-- A loop that can describe work instead of doing it WILL, forever, at cost, while looking busy.
-- So every run records whether it actually produced something — and the escalation ladder reads
-- THIS, from the database, not a counter in memory. A counter that resets on restart would let a
-- stuck loop hammer the same wall forever simply by crashing.
BEGIN;

ALTER TABLE runs ADD COLUMN IF NOT EXISTS productive boolean;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS evidence   text;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS measure_before numeric;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS measure_after  numeric;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS escalation_level text;

CREATE INDEX IF NOT EXISTS runs_productive_idx ON runs (completed_at DESC)
  WHERE completed_at IS NOT NULL;

COMMIT;
