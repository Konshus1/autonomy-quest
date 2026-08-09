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
  expected_expense_usd numeric(10,4) NOT NULL DEFAULT 0,
  blast_radius_level integer NOT NULL DEFAULT 0 CHECK (blast_radius_level BETWEEN 0 AND 3),
  reversible boolean NOT NULL DEFAULT true,
  spends_money boolean NOT NULL DEFAULT false,
  touches_human boolean NOT NULL DEFAULT false,
  commits boolean NOT NULL DEFAULT false,
  tokens_in bigint NOT NULL DEFAULT 0,
  tokens_out bigint NOT NULL DEFAULT 0,
  cost_usd numeric(10,4) NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(work_id,target_step_id,observation_index),
  CHECK ((decision='acquire') = (chosen_mode IS NOT NULL AND chosen_instruction IS NOT NULL)),
  CHECK ((decision='acquire') OR stop_reason IS NOT NULL)
);
ALTER TABLE impasse_meta_mode_decision ADD COLUMN IF NOT EXISTS expected_expense_usd numeric(10,4) NOT NULL DEFAULT 0;
ALTER TABLE impasse_meta_mode_decision ADD COLUMN IF NOT EXISTS blast_radius_level integer NOT NULL DEFAULT 0;
ALTER TABLE impasse_meta_mode_decision ADD COLUMN IF NOT EXISTS reversible boolean NOT NULL DEFAULT true;
ALTER TABLE impasse_meta_mode_decision ADD COLUMN IF NOT EXISTS spends_money boolean NOT NULL DEFAULT false;
ALTER TABLE impasse_meta_mode_decision ADD COLUMN IF NOT EXISTS touches_human boolean NOT NULL DEFAULT false;
ALTER TABLE impasse_meta_mode_decision ADD COLUMN IF NOT EXISTS commits boolean NOT NULL DEFAULT false;
ALTER TABLE impasse_meta_mode_decision ADD COLUMN IF NOT EXISTS tokens_in bigint NOT NULL DEFAULT 0;
ALTER TABLE impasse_meta_mode_decision ADD COLUMN IF NOT EXISTS tokens_out bigint NOT NULL DEFAULT 0;
ALTER TABLE impasse_meta_mode_decision ADD COLUMN IF NOT EXISTS cost_usd numeric(10,4) NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS impasse_meta_mode_forecast_attempt (
  attempt_id bigserial PRIMARY KEY,
  work_id bigint NOT NULL REFERENCES work(id) ON DELETE CASCADE,
  plan_id text NOT NULL,
  target_step_id text NOT NULL,
  raw_forecast jsonb NOT NULL,
  tokens_in bigint NOT NULL CHECK (tokens_in >= 0),
  tokens_out bigint NOT NULL CHECK (tokens_out >= 0),
  cost_usd numeric(10,4) NOT NULL CHECK (cost_usd >= 0),
  validation_error text,
  decision_id bigint REFERENCES impasse_meta_mode_decision(decision_id),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS meta_mode_spend_reservation (
  decision_id bigint PRIMARY KEY REFERENCES impasse_meta_mode_decision(decision_id) ON DELETE CASCADE,
  expected_expense_usd numeric(18,6) NOT NULL CHECK (expected_expense_usd >= 0),
  status text NOT NULL DEFAULT 'reserved' CHECK (status IN ('reserved','incurred','released')),
  reserved_at timestamptz NOT NULL DEFAULT now(),
  incurred_at timestamptz
);

ALTER TABLE impasse_meta_mode_decision DROP CONSTRAINT IF EXISTS impasse_meta_mode_decision_check;
ALTER TABLE impasse_meta_mode_decision ADD CONSTRAINT impasse_meta_mode_decision_check
  CHECK (decision <> 'acquire' OR (chosen_mode IS NOT NULL AND chosen_instruction IS NOT NULL));
ALTER TABLE impasse_meta_mode_decision DROP CONSTRAINT IF EXISTS impasse_meta_mode_expense_nonnegative;
ALTER TABLE impasse_meta_mode_decision ADD CONSTRAINT impasse_meta_mode_expense_nonnegative CHECK (expected_expense_usd BETWEEN 0 AND 999999.9999);
ALTER TABLE impasse_meta_mode_decision DROP CONSTRAINT IF EXISTS impasse_meta_mode_blast_range;
ALTER TABLE impasse_meta_mode_decision ADD CONSTRAINT impasse_meta_mode_blast_range CHECK (blast_radius_level BETWEEN 0 AND 3);
ALTER TABLE impasse_meta_mode_decision DROP CONSTRAINT IF EXISTS impasse_meta_mode_tokens_nonnegative;
ALTER TABLE impasse_meta_mode_decision ADD CONSTRAINT impasse_meta_mode_tokens_nonnegative CHECK (tokens_in >= 0 AND tokens_out >= 0);
ALTER TABLE impasse_meta_mode_decision DROP CONSTRAINT IF EXISTS impasse_meta_mode_cost_nonnegative;
ALTER TABLE impasse_meta_mode_decision ADD CONSTRAINT impasse_meta_mode_cost_nonnegative CHECK (cost_usd >= 0);

ALTER TABLE heartbeat DROP CONSTRAINT IF EXISTS heartbeat_state_check;
ALTER TABLE heartbeat ADD CONSTRAINT heartbeat_state_check
  CHECK (state IN ('turning','hibernating','rate_limited','acquiring','no_progress'));

ALTER TABLE plan_acquisition DROP CONSTRAINT IF EXISTS plan_acquisition_work_id_target_step_id_rung_key;
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
