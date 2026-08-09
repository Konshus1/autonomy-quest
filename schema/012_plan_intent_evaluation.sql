-- 012_plan_intent_evaluation.sql — three independent plan-success measures.
BEGIN;
SET search_path = public, ag_catalog, "$user";
CREATE TABLE IF NOT EXISTS plan_intent_lineage (
  work_id bigint PRIMARY KEY REFERENCES work(id) ON DELETE CASCADE,
  plan_id text NOT NULL,
  authoritative_concerns jsonb NOT NULL CHECK (jsonb_typeof(authoritative_concerns)='array'),
  lineage jsonb NOT NULL CHECK (jsonb_typeof(lineage)='object'),
  verifier_id text NOT NULL,
  valid boolean NOT NULL,
  reasons jsonb NOT NULL CHECK (jsonb_typeof(reasons)='array'),
  checked_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS plan_evaluation (
  run_id bigint PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
  work_id bigint NOT NULL REFERENCES work(id) ON DELETE CASCADE,
  plan_id text NOT NULL,
  goal_satisfied boolean NOT NULL,
  steps_executed integer NOT NULL CHECK (steps_executed >= 0),
  steps_confirmed integer NOT NULL CHECK (steps_confirmed >= 0 AND steps_confirmed <= steps_executed),
  intent_satisfied boolean NOT NULL,
  observed_metrics jsonb NOT NULL CHECK (jsonb_typeof(observed_metrics)='object'),
  step_results jsonb NOT NULL CHECK (jsonb_typeof(step_results)='array'),
  evaluator_id text NOT NULL DEFAULT 'deterministic-interval-v1',
  evaluated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS plan_evaluation_work_idx ON plan_evaluation(work_id,evaluated_at);
COMMIT;
