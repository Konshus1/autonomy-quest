-- 010_plan_predictions.sql — per-step direction assertions before ACT (BB #2430).
-- Additive extension of Track G's 009 substrate; 009 remains unchanged.
BEGIN;
SET search_path = public, ag_catalog, "$user";

ALTER TABLE work ADD COLUMN IF NOT EXISTS plan_id text;
ALTER TABLE work ADD COLUMN IF NOT EXISTS plan jsonb;

ALTER TABLE causal_edge ADD COLUMN IF NOT EXISTS relation_direction text
  CHECK (relation_direction IN ('toward','away','neutral'));
ALTER TABLE causal_edge ADD COLUMN IF NOT EXISTS mechanism_description text;

-- 009 could only represent one covered edge per opaque plan.  A live plan must also
-- record uncovered steps, so edge_id becomes nullable and each row carries its assertion.
ALTER TABLE planning_prediction ALTER COLUMN edge_id DROP NOT NULL;
ALTER TABLE planning_prediction ADD COLUMN IF NOT EXISTS work_id bigint REFERENCES work(id) ON DELETE CASCADE;
ALTER TABLE planning_prediction ADD COLUMN IF NOT EXISTS step_id text;
ALTER TABLE planning_prediction ADD COLUMN IF NOT EXISTS step_index integer CHECK (step_index >= 0);
ALTER TABLE planning_prediction ADD COLUMN IF NOT EXISTS source_action text;
ALTER TABLE planning_prediction ADD COLUMN IF NOT EXISTS expected_direct_effect text;
ALTER TABLE planning_prediction ADD COLUMN IF NOT EXISTS expected_direction text
  CHECK (expected_direction IN ('toward','away','neutral'));
ALTER TABLE planning_prediction ADD COLUMN IF NOT EXISTS scope jsonb NOT NULL DEFAULT '{}'::jsonb
  CHECK (jsonb_typeof(scope) = 'object');
ALTER TABLE planning_prediction ADD COLUMN IF NOT EXISTS edge_state text
  CHECK (edge_state IN ('solid','fuzzy','absent'));
ALTER TABLE planning_prediction ADD COLUMN IF NOT EXISTS sufficient boolean;
ALTER TABLE planning_prediction ADD COLUMN IF NOT EXISTS assessment_annotation text;
ALTER TABLE planning_prediction ADD COLUMN IF NOT EXISTS asserted_at timestamptz NOT NULL DEFAULT now();

CREATE UNIQUE INDEX IF NOT EXISTS planning_prediction_work_step_uq
  ON planning_prediction(work_id, step_id) WHERE work_id IS NOT NULL AND step_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS planning_prediction_pre_act_idx
  ON planning_prediction(work_id, asserted_at);
COMMIT;
