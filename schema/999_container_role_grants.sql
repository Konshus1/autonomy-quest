-- Container principal grants. External installs without these roles skip this block.
DO $grants$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='aq_loop')
     OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='aq_actor')
     OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='aq_governance') THEN
    RAISE NOTICE 'container database roles absent; skipping container-only grants';
    RETURN;
  END IF;
  EXECUTE 'GRANT CREATE ON SCHEMA public TO aq_loop';
  EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO aq_loop';
  EXECUTE 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO aq_loop';
  EXECUTE 'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO aq_loop';
  EXECUTE 'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON causal_principle_transition, causal_principle_plan_usage, causal_principle_plan_outcome FROM aq_loop';

  EXECUTE 'GRANT SELECT ON ALL TABLES IN SCHEMA public TO aq_actor';
  EXECUTE 'GRANT INSERT, UPDATE, DELETE ON customers, subscriptions TO aq_actor';
  EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE subscriptions_id_seq TO aq_actor';
  EXECUTE 'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON causal_edge, ralph_causal_edges, causal_principle_transition, causal_principle_plan_usage, causal_principle_plan_outcome, runs, work, learnings, planning_prediction, plan_acquisition FROM aq_actor';

  EXECUTE 'GRANT SELECT ON ALL TABLES IN SCHEMA public TO aq_governance';
  EXECUTE 'GRANT INSERT, UPDATE ON causal_principle_transition, causal_principle_plan_usage, causal_principle_plan_outcome TO aq_governance';
  EXECUTE 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO aq_governance';
  EXECUTE 'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO aq_governance';
  EXECUTE 'REVOKE DELETE, TRUNCATE ON causal_principle_transition, causal_principle_plan_usage, causal_principle_plan_outcome FROM aq_governance';

  EXECUTE 'ALTER FUNCTION register_flagship_causal_proposal() SECURITY DEFINER';
  EXECUTE 'REVOKE ALL ON FUNCTION register_flagship_causal_proposal() FROM PUBLIC';
  EXECUTE 'GRANT EXECUTE ON FUNCTION register_flagship_causal_proposal() TO aq_loop';
END $grants$;
