-- 013_plan_resolution.sql — localized refutation, re-plan trigger, traceable support.
BEGIN;
SET search_path = public, ag_catalog, "$user";
ALTER TABLE causal_edge ADD COLUMN IF NOT EXISTS supplier_principle_id text;
ALTER TABLE causal_principle ADD COLUMN IF NOT EXISTS evidence_for_count integer NOT NULL DEFAULT 0 CHECK (evidence_for_count >= 0);
ALTER TABLE causal_principle ADD COLUMN IF NOT EXISTS evidence_against_count integer NOT NULL DEFAULT 0 CHECK (evidence_against_count >= 0);
ALTER TABLE planning_prediction ADD COLUMN IF NOT EXISTS supplier_principle_id text;
ALTER TABLE planning_prediction ADD COLUMN IF NOT EXISTS resolved_run_id bigint REFERENCES runs(id) ON DELETE SET NULL;
ALTER TABLE planning_prediction ADD COLUMN IF NOT EXISTS executed boolean;
ALTER TABLE planning_prediction ADD COLUMN IF NOT EXISTS direct_effect_confirmed boolean;
ALTER TABLE planning_prediction ADD COLUMN IF NOT EXISTS direction_confirmed boolean;
ALTER TABLE planning_prediction ADD COLUMN IF NOT EXISTS outcome_kind text;
ALTER TABLE planning_prediction ADD COLUMN IF NOT EXISTS outcome_evidence text;
ALTER TABLE planning_prediction ADD COLUMN IF NOT EXISTS resolved_at timestamptz;

CREATE TABLE IF NOT EXISTS principle_support_event (
  support_event_id bigserial PRIMARY KEY,
  prediction_id bigint NOT NULL REFERENCES planning_prediction(prediction_id) ON DELETE CASCADE,
  run_id bigint NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  edge_id bigint REFERENCES causal_edge(edge_id) ON DELETE SET NULL,
  principle_id text,
  delta smallint NOT NULL CHECK (delta IN (-1,1)),
  outcome_kind text NOT NULL,
  evidence text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(prediction_id,run_id)
);
CREATE INDEX IF NOT EXISTS principle_support_event_principle_idx ON principle_support_event(principle_id,created_at);

CREATE TABLE IF NOT EXISTS plan_replan (
  replan_id bigserial PRIMARY KEY,
  source_work_id bigint NOT NULL REFERENCES work(id) ON DELETE CASCADE,
  source_plan_id text NOT NULL,
  failed_step_id text NOT NULL,
  preserved_prefix_step_ids jsonb NOT NULL CHECK (jsonb_typeof(preserved_prefix_step_ids)='array'),
  reason text NOT NULL,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','planned','dismissed')),
  replacement_work_id bigint REFERENCES work(id) ON DELETE SET NULL,
  replacement_plan_id text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(source_work_id,failed_step_id)
);
CREATE INDEX IF NOT EXISTS plan_replan_pending_idx ON plan_replan(status,created_at);
COMMIT;
