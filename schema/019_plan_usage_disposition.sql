-- A governance receipt must represent negative as well as positive dispositions.
-- Keep NOT NULL so every receipt is explicit, but do not force either boolean to true.
ALTER TABLE causal_principle_plan_usage
  DROP CONSTRAINT IF EXISTS causal_principle_plan_usage_selected_check,
  DROP CONSTRAINT IF EXISTS causal_principle_plan_usage_governed_check;
