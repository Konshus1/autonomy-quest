-- 018_impasse_meta_mode.sql — common-scale arbitration replaces fixed acquisition order.
BEGIN;
SET search_path = public, ag_catalog, "$user";

CREATE TABLE IF NOT EXISTS impasse_meta_mode_decision (
  decision_id bigserial PRIMARY KEY,
  work_id bigint NOT NULL REFERENCES work(id) ON DELETE CASCADE,
  plan_id text NOT NULL,
  target_step_id text NOT NULL,
  observation_index integer NOT NULL CHECK (observation_index >= 0),
  policy_version text NOT NULL,
  scorecards jsonb NOT NULL,
  decision text NOT NULL CHECK (decision IN ('acquire','abstain','stop')),
  chosen_mode text,
  chosen_score numeric,
  stop_reason text,
  chosen_instruction text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(work_id,target_step_id,observation_index),
  CHECK ((decision='acquire') = (chosen_mode IS NOT NULL AND chosen_instruction IS NOT NULL)),
  CHECK ((decision='acquire') OR stop_reason IS NOT NULL)
);

ALTER TABLE heartbeat DROP CONSTRAINT IF EXISTS heartbeat_state_check;
ALTER TABLE heartbeat ADD CONSTRAINT heartbeat_state_check
  CHECK (state IN ('turning','hibernating','rate_limited','acquiring','no_progress'));

ALTER TABLE plan_acquisition DROP CONSTRAINT IF EXISTS plan_acquisition_rung_check;
ALTER TABLE plan_acquisition ADD CONSTRAINT plan_acquisition_rung_check CHECK (rung IN (
  'recall','search','bounded_experiment','analogy_proposal','human',
  'internal_computation','environment_experiment','human_question','human_demonstration',
  'autonomous_practice'));
ALTER TABLE plan_acquisition DROP CONSTRAINT IF EXISTS plan_acquisition_rung_index_check;
ALTER TABLE plan_acquisition ADD CONSTRAINT plan_acquisition_rung_index_check CHECK (rung_index >= 0);
ALTER TABLE plan_acquisition ADD COLUMN IF NOT EXISTS meta_mode_decision_id bigint
  REFERENCES impasse_meta_mode_decision(decision_id);
CREATE UNIQUE INDEX IF NOT EXISTS plan_acquisition_meta_decision_unique
  ON plan_acquisition(meta_mode_decision_id) WHERE meta_mode_decision_id IS NOT NULL;
COMMIT;
