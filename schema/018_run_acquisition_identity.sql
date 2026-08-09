-- Tie every run to the exact acquisition rung it executed so crash recovery cannot lose mode.
BEGIN;
SET search_path = public, ag_catalog, "$user";

ALTER TABLE runs ADD COLUMN IF NOT EXISTS acquisition_id bigint
  REFERENCES plan_acquisition(acquisition_id) ON DELETE RESTRICT;

ALTER TABLE heartbeat DROP CONSTRAINT IF EXISTS heartbeat_state_check;
ALTER TABLE heartbeat ADD CONSTRAINT heartbeat_state_check
  CHECK (state IN ('turning','acquiring','no_progress','hibernating','rate_limited'));

-- Repair the pre-018 crash shape only when history identifies exactly one candidate. Refuse an
-- ambiguous migration rather than making a guessed rung identity immutable.
DO $$
DECLARE ambiguous bigint;
BEGIN
  SELECT r.id INTO ambiguous FROM runs r
  WHERE r.acquisition_id IS NULL AND r.completed_at IS NULL AND r.outcome IS NOT NULL
    AND (SELECT count(*) FROM plan_acquisition pa WHERE pa.work_id=r.work_id
         AND pa.status IN ('pending','running') AND pa.created_at<=r.started_at) > 1
  LIMIT 1;
  IF ambiguous IS NOT NULL THEN
    RAISE EXCEPTION 'cannot backfill run %: multiple acquisition rungs match', ambiguous;
  END IF;
END $$;
UPDATE runs r SET acquisition_id = (
  SELECT pa.acquisition_id FROM plan_acquisition pa
  WHERE pa.work_id=r.work_id AND pa.status IN ('pending','running')
    AND pa.created_at <= r.started_at
  ORDER BY pa.created_at DESC, pa.acquisition_id DESC LIMIT 1
)
WHERE r.acquisition_id IS NULL AND r.completed_at IS NULL AND r.outcome IS NOT NULL
  AND 1 = (SELECT count(*) FROM plan_acquisition pa
              WHERE pa.work_id=r.work_id AND pa.status IN ('pending','running')
                AND pa.created_at<=r.started_at);

CREATE OR REPLACE FUNCTION aq_guard_run_acquisition_identity() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE acquisition_work bigint;
BEGIN
  IF TG_OP='UPDATE' AND NEW.acquisition_id IS DISTINCT FROM OLD.acquisition_id THEN
    RAISE EXCEPTION 'run acquisition identity is immutable';
  END IF;
  IF NEW.acquisition_id IS NOT NULL THEN
    SELECT work_id INTO acquisition_work FROM plan_acquisition
      WHERE acquisition_id=NEW.acquisition_id;
    IF acquisition_work IS NULL OR acquisition_work IS DISTINCT FROM NEW.work_id THEN
      RAISE EXCEPTION 'run % acquisition % does not belong to work %',
        coalesce(NEW.id,0), NEW.acquisition_id, NEW.work_id;
    END IF;
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS run_acquisition_identity_guard ON runs;
CREATE TRIGGER run_acquisition_identity_guard
BEFORE INSERT OR UPDATE OF acquisition_id,work_id ON runs
FOR EACH ROW EXECUTE FUNCTION aq_guard_run_acquisition_identity();
CREATE INDEX IF NOT EXISTS runs_acquisition_idx ON runs(acquisition_id)
  WHERE acquisition_id IS NOT NULL;

-- AGE label creation requires ownership of the graph's base tables. Create the fixed cycle shape
-- during the owner-only migration so aq_loop only needs row DML at runtime.
LOAD 'age';
DO $$
DECLARE gid oid;
BEGIN
  SELECT graphid INTO gid FROM ag_catalog.ag_graph WHERE name='autonomy_quest';
  IF gid IS NOT NULL THEN
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE graph=gid AND name='Work') THEN
      PERFORM ag_catalog.create_vlabel('autonomy_quest','Work');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE graph=gid AND name='Run') THEN
      PERFORM ag_catalog.create_vlabel('autonomy_quest','Run');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE graph=gid AND name='Learning') THEN
      PERFORM ag_catalog.create_vlabel('autonomy_quest','Learning');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE graph=gid AND name='EXECUTED') THEN
      PERFORM ag_catalog.create_elabel('autonomy_quest','EXECUTED');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE graph=gid AND name='DERIVED_FROM') THEN
      PERFORM ag_catalog.create_elabel('autonomy_quest','DERIVED_FROM');
    END IF;
  END IF;
END $$;
COMMIT;
