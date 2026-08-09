-- 015_acquisition_lifecycle.sql — acquisition attempts and parent work advance atomically.
BEGIN;
SET search_path = public, ag_catalog, "$user";

-- Repair rows written by the pre-015 loop. A completed run proves the rung attempt returned;
-- preserve its durable receipt and resume the original plan. A running row without a completed
-- run was interrupted and must be retried, never claimed complete.
WITH latest AS (
  SELECT DISTINCT ON (work_id) work_id, id AS run_id, outcome, evidence, succeeded, completed_at
  FROM runs WHERE completed_at IS NOT NULL ORDER BY work_id, completed_at DESC
)
UPDATE plan_acquisition pa
SET status='completed', completed_at=latest.completed_at,
    result=jsonb_build_object('migrated_from_run', latest.run_id,
                              'outcome', latest.outcome,
                              'evidence', latest.evidence,
                              'succeeded', latest.succeeded)
FROM latest
WHERE pa.work_id=latest.work_id AND pa.status='running';

UPDATE plan_acquisition pa SET status='pending'
WHERE pa.status='running'
  AND NOT EXISTS (SELECT 1 FROM runs r WHERE r.work_id=pa.work_id AND r.completed_at IS NOT NULL);

UPDATE work w SET status='pending'
WHERE w.status='done' AND EXISTS (
  SELECT 1 FROM plan_acquisition pa WHERE pa.work_id=w.id AND pa.status='completed'
);

CREATE OR REPLACE FUNCTION aq_guard_work_acquisition_state() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status='done' AND EXISTS (
    SELECT 1 FROM plan_acquisition pa
    WHERE pa.work_id=NEW.id AND pa.status IN ('pending','running')
  ) THEN
    RAISE EXCEPTION 'work % cannot be done while acquisition is open', NEW.id;
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS work_acquisition_state_guard ON work;
CREATE TRIGGER work_acquisition_state_guard
BEFORE INSERT OR UPDATE OF status ON work
FOR EACH ROW EXECUTE FUNCTION aq_guard_work_acquisition_state();

CREATE OR REPLACE FUNCTION aq_guard_acquisition_work_state() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE parent_status text;
BEGIN
  SELECT status INTO parent_status FROM work WHERE id=NEW.work_id;
  IF NEW.status IN ('pending','running') AND parent_status='done' THEN
    RAISE EXCEPTION 'acquisition % cannot be open for done work %', NEW.acquisition_id, NEW.work_id;
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS acquisition_work_state_guard ON plan_acquisition;
CREATE TRIGGER acquisition_work_state_guard
BEFORE INSERT OR UPDATE OF status ON plan_acquisition
FOR EACH ROW EXECUTE FUNCTION aq_guard_acquisition_work_state();
COMMIT;
