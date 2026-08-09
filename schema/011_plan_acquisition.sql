-- 011_plan_acquisition.sql — insufficiency triggers an ordered acquisition action.
BEGIN;
SET search_path = public, ag_catalog, "$user";
CREATE TABLE IF NOT EXISTS plan_acquisition (
  acquisition_id bigserial PRIMARY KEY,
  work_id bigint NOT NULL REFERENCES work(id) ON DELETE CASCADE,
  plan_id text NOT NULL,
  target_step_id text NOT NULL,
  rung text NOT NULL CHECK (rung IN ('recall','search','bounded_experiment','analogy_proposal','human')),
  rung_index integer NOT NULL CHECK (rung_index BETWEEN 0 AND 4),
  action_step_id text NOT NULL,
  instruction text NOT NULL,
  proposer_only boolean NOT NULL DEFAULT false,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','completed','skipped')),
  result jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  UNIQUE(work_id, target_step_id, rung),
  UNIQUE(work_id, action_step_id),
  CHECK ((rung = 'analogy_proposal') = proposer_only)
);
CREATE INDEX IF NOT EXISTS plan_acquisition_pending_idx
  ON plan_acquisition(work_id, status, rung_index);
COMMIT;
