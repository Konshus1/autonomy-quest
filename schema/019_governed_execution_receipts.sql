-- Bind the exact promotion that governed each sufficient pre-ACT plan assertion.
BEGIN;
ALTER TABLE planning_prediction ADD COLUMN IF NOT EXISTS promotion_transition_id bigint
  REFERENCES causal_principle_transition(id) ON DELETE RESTRICT;
CREATE INDEX IF NOT EXISTS planning_prediction_promotion_idx
  ON planning_prediction(promotion_transition_id) WHERE promotion_transition_id IS NOT NULL;
COMMIT;
