-- 014_consequence_gate.sql — amended BB #878 numeric spend + measured blast gate.
BEGIN;
SET search_path = public, ag_catalog, "$user";
ALTER TABLE work ADD COLUMN IF NOT EXISTS expected_expense_usd numeric(18,6)
  CHECK (expected_expense_usd >= 0);
ALTER TABLE work ADD COLUMN IF NOT EXISTS blast_radius_level smallint
  CHECK (blast_radius_level BETWEEN 0 AND 3);
ALTER TABLE work ADD COLUMN IF NOT EXISTS blast_radius_basis jsonb;
ALTER TABLE work ADD COLUMN IF NOT EXISTS gate_policy_version text;
ALTER TABLE work ADD COLUMN IF NOT EXISTS gate_reason text;
CREATE TABLE IF NOT EXISTS plan_spend_reservation (
  work_id bigint PRIMARY KEY REFERENCES work(id) ON DELETE CASCADE,
  expected_expense_usd numeric(18,6) NOT NULL CHECK (expected_expense_usd >= 0),
  status text NOT NULL DEFAULT 'reserved' CHECK (status IN ('reserved','incurred','released')),
  reserved_at timestamptz NOT NULL DEFAULT now(),
  incurred_at timestamptz
);
CREATE INDEX IF NOT EXISTS plan_spend_active_idx ON plan_spend_reservation(status);
COMMIT;
