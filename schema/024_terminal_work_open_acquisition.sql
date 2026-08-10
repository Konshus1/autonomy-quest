-- TERMINAL WORK MAY NEVER HAVE AN OPEN ACQUISITION. 'abandoned' IS TERMINAL TOO.
--
-- 015_acquisition_lifecycle.sql guards this pair, but both of its triggers test only `done`
-- (015:33 and 015:52). `abandoned` is equally terminal in the work schema, so this was reachable
-- and, worse, silently reachable through ORDINARY CALLERS rather than hand-written SQL:
-- reject_work_intent(), reject_work_conflict() and reject_work() all set work to 'abandoned'
-- WITHOUT closing an open acquisition.
--
-- The consequence is not cosmetic. On a RESUMED acquisition, an intent or conflict rejection
-- strands the open acquisition permanently: pending-work selection no longer sees its abandoned
-- parent, so nothing will ever pick the acquisition up, and nothing will ever close it. It is the
-- L1 wedge again, arriving by a different door.
--
-- Found by independent re-review of main@506300d (BB #2631) with a rollback-only Compose negative:
--     work pending + acquisition pending; UPDATE work SET status='abandoned'  =>  ALLOWED
--
-- scripts/prove_meta_mode_cycle.py made this worse by asserting the invariant at TEST TIME while
-- COMMENTING that the database refuses every terminal+open pair. It did not. A comment claiming a
-- guarantee the schema does not provide is how the next reader stops checking — the assertion was
-- true of that run and false of the system, which is the defect class this repository exists to
-- catch, committed in the file that catches it.
--
-- TERMINAL STATES ARE DEFINED ONCE HERE and both triggers consult that definition, so a future
-- terminal status cannot be added to one guard and forgotten in the other.

BEGIN;

CREATE OR REPLACE FUNCTION aq_terminal_work_statuses() RETURNS text[]
LANGUAGE sql IMMUTABLE AS $$ SELECT ARRAY['done','abandoned']::text[] $$;

-- REPAIR EXISTING ROWS BEFORE INSTALLING THE GUARD, or this migration cannot be applied to any
-- database that already contains a stranded pair. Closing them as 'skipped' rather than
-- 'completed' is deliberate: they were never executed to completion, and recording them as
-- completed would manufacture exactly the false receipt the governance work spent the day removing.
UPDATE plan_acquisition pa
   SET status='skipped',
       completed_at=COALESCE(pa.completed_at, now()),
       result=COALESCE(pa.result,'{}'::jsonb)
             || jsonb_build_object(
                  'closed_by_migration','024_terminal_work_open_acquisition',
                  'reason','parent work was terminal while this acquisition was still open; '
                        || 'stranded by a reject path that did not close it (BB #2631)')
  FROM work w
 WHERE w.id = pa.work_id
   AND w.status = ANY(aq_terminal_work_statuses())
   AND pa.status IN ('pending','running');

CREATE OR REPLACE FUNCTION reject_terminal_work_with_open_acquisition() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status = ANY(aq_terminal_work_statuses()) AND EXISTS (
       SELECT 1 FROM plan_acquisition pa
        WHERE pa.work_id = NEW.id AND pa.status IN ('pending','running')) THEN
    RAISE EXCEPTION 'work % cannot be %s while acquisition is open', NEW.id, NEW.status;
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION reject_open_acquisition_for_terminal_work() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE parent_status text;
BEGIN
  SELECT w.status INTO parent_status FROM work w WHERE w.id = NEW.work_id;
  IF NEW.status IN ('pending','running') AND parent_status = ANY(aq_terminal_work_statuses()) THEN
    RAISE EXCEPTION 'acquisition % cannot be open for % work %',
      NEW.acquisition_id, parent_status, NEW.work_id;
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_work_terminal_open_acquisition ON work;
CREATE TRIGGER trg_work_terminal_open_acquisition
  BEFORE UPDATE OF status ON work
  FOR EACH ROW EXECUTE FUNCTION reject_terminal_work_with_open_acquisition();

DROP TRIGGER IF EXISTS trg_acquisition_open_terminal_work ON plan_acquisition;
CREATE TRIGGER trg_acquisition_open_terminal_work
  BEFORE INSERT OR UPDATE OF status ON plan_acquisition
  FOR EACH ROW EXECUTE FUNCTION reject_open_acquisition_for_terminal_work();

COMMIT;
