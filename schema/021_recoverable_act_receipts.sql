-- Preserve the structured ACT receipt and pre-ACT governed selections across reflection crashes.
BEGIN;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS act_result jsonb;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS governed_selections jsonb NOT NULL DEFAULT '[]'::jsonb
  CHECK (jsonb_typeof(governed_selections)='array');
ALTER TABLE causal_principle_plan_usage ADD COLUMN IF NOT EXISTS measure_before numeric;
ALTER TABLE causal_principle_plan_usage ADD COLUMN IF NOT EXISTS run_id bigint REFERENCES runs(id) ON DELETE RESTRICT;
ALTER TABLE causal_principle_plan_usage ADD COLUMN IF NOT EXISTS work_id bigint REFERENCES work(id) ON DELETE RESTRICT;
ALTER TABLE causal_principle_plan_usage DROP CONSTRAINT IF EXISTS causal_principle_plan_usage_cause_effect_scope_plan_id_key;
CREATE UNIQUE INDEX IF NOT EXISTS causal_principle_plan_usage_run_uq
  ON causal_principle_plan_usage(cause,effect,scope,plan_id,run_id) WHERE run_id IS NOT NULL;
ALTER TABLE causal_principle_plan_usage DROP CONSTRAINT IF EXISTS causal_principle_plan_usage_exact_run;
ALTER TABLE causal_principle_plan_usage ADD CONSTRAINT causal_principle_plan_usage_exact_run
  CHECK (run_id IS NOT NULL AND work_id IS NOT NULL) NOT VALID;
COMMENT ON CONSTRAINT causal_principle_plan_usage_exact_run ON causal_principle_plan_usage IS
  'NOT VALID preserves pre-021 audit rows; every new governed usage must bind an exact run/work.';
ALTER TABLE causal_principle_plan_usage ADD COLUMN IF NOT EXISTS environment_mission_id text;
ALTER TABLE causal_principle_plan_usage ADD COLUMN IF NOT EXISTS environment_harness text;
ALTER TABLE causal_principle_plan_outcome ADD COLUMN IF NOT EXISTS measure_before numeric;
ALTER TABLE causal_principle_plan_outcome ADD COLUMN IF NOT EXISTS measure_after numeric;
ALTER TABLE causal_principle_plan_outcome ADD COLUMN IF NOT EXISTS observed_delta numeric;
ALTER TABLE causal_principle_plan_outcome ADD COLUMN IF NOT EXISTS measure_source text;
COMMIT;
