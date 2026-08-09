-- Consequence-based autonomy gate: persist the properties and exact reason used pre-act.
BEGIN;
ALTER TABLE work ADD COLUMN IF NOT EXISTS expected_cost_usd numeric(12,2) NOT NULL DEFAULT 0 CHECK (expected_cost_usd >= 0);
ALTER TABLE work ADD COLUMN IF NOT EXISTS blast_radius real NOT NULL DEFAULT 0 CHECK (blast_radius BETWEEN 0 AND 1);
ALTER TABLE work ADD COLUMN IF NOT EXISTS reversible boolean NOT NULL DEFAULT true;
ALTER TABLE work ADD COLUMN IF NOT EXISTS spends_money boolean NOT NULL DEFAULT false;
ALTER TABLE work ADD COLUMN IF NOT EXISTS touches_human boolean NOT NULL DEFAULT false;
ALTER TABLE work ADD COLUMN IF NOT EXISTS commits boolean NOT NULL DEFAULT false;
ALTER TABLE work ADD COLUMN IF NOT EXISTS gate_reason text;
COMMIT;
