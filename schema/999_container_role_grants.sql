-- Container principal grants. External installs without these roles skip this block.
DO $grants$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='aq_loop')
     OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='aq_actor')
     OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='aq_governance')
     OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='aq_control_owner') THEN
    RAISE NOTICE 'container database roles absent; skipping container-only grants';
    RETURN;
  END IF;

  -- aq_loop operates the ordinary runtime ledger. It cannot create shadow objects or inherit
  -- execution on every future function, and it cannot write any authority/evidence relation.
  EXECUTE 'REVOKE CREATE ON SCHEMA public FROM aq_loop';
  EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO aq_loop';
  EXECUTE 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO aq_loop';
  EXECUTE 'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON causal_edge, causal_principle_transition, causal_principle_plan_usage, causal_principle_plan_outcome FROM aq_loop';

  EXECUTE 'GRANT SELECT ON ALL TABLES IN SCHEMA public TO aq_actor';
  EXECUTE 'GRANT INSERT, UPDATE, DELETE ON customers, subscriptions TO aq_actor';
  EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE subscriptions_id_seq TO aq_actor';
  EXECUTE 'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON causal_edge, ralph_causal_edges, causal_principle_transition, causal_principle_plan_usage, causal_principle_plan_outcome, runs, work, learnings, planning_prediction, plan_acquisition, impasse_meta_mode_decision FROM aq_actor';

  EXECUTE 'GRANT SELECT ON ALL TABLES IN SCHEMA public TO aq_governance';
  EXECUTE 'GRANT INSERT, UPDATE ON causal_principle_transition, causal_principle_plan_usage, causal_principle_plan_outcome TO aq_governance';
  EXECUTE 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO aq_governance';
  EXECUTE 'REVOKE DELETE, TRUNCATE ON causal_principle_transition, causal_principle_plan_usage, causal_principle_plan_outcome FROM aq_governance';

  -- No runtime principal inherits PostgreSQL's default PUBLIC function execution. Grants below
  -- are an allowlist; trigger invocation does not require direct EXECUTE by the row writer.
  EXECUTE 'REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC, aq_loop, aq_actor, aq_governance';
  EXECUTE 'REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA aq_control FROM PUBLIC, aq_loop, aq_actor, aq_governance';

  -- The NOLOGIN owner is the trusted code/data domain. The migration owner remains offline.
  EXECUTE 'ALTER SCHEMA aq_control OWNER TO aq_control_owner';
  EXECUTE 'ALTER TABLE aq_control.evidence_event OWNER TO aq_control_owner';
  EXECUTE 'ALTER TABLE aq_control.evidence_application OWNER TO aq_control_owner';
  EXECUTE 'ALTER FUNCTION aq_control.reject_immutable_event_mutation() OWNER TO aq_control_owner';
  EXECUTE 'ALTER FUNCTION aq_control.attest_acquisition_evidence(bigint,bigint,integer) OWNER TO aq_control_owner';
  EXECUTE 'ALTER FUNCTION aq_control.attest_prediction_resolution(bigint,bigint) OWNER TO aq_control_owner';

  EXECUTE 'GRANT USAGE ON SCHEMA aq_control TO aq_loop, aq_governance';
  EXECUTE 'REVOKE ALL ON aq_control.evidence_event, aq_control.evidence_application FROM aq_loop, aq_actor, aq_governance';
  EXECUTE 'GRANT EXECUTE ON FUNCTION aq_control.attest_acquisition_evidence(bigint,bigint,integer) TO aq_governance';
  EXECUTE 'GRANT EXECUTE ON FUNCTION aq_control.attest_prediction_resolution(bigint,bigint) TO aq_governance';

  -- The control owner can verify public operational facts and apply authority consequences only
  -- through checked functions. It never receives LOGIN or membership in a runtime principal.
  EXECUTE 'GRANT SELECT ON work,runs,plan_acquisition,planning_prediction,principle_support_event,causal_edge,causal_principle_transition TO aq_control_owner';
  EXECUTE 'GRANT INSERT,UPDATE ON causal_edge TO aq_control_owner';
  EXECUTE 'GRANT USAGE,SELECT ON SEQUENCE causal_edge_edge_id_seq TO aq_control_owner';
  EXECUTE 'GRANT INSERT ON causal_principle_transition TO aq_control_owner';
  EXECUTE 'GRANT USAGE,SELECT ON SEQUENCE causal_principle_transition_id_seq TO aq_control_owner';

  -- Item 5 lands before item 4: the forgeable legacy bridges are not executable by the loop.
  -- Item 4 replaces them with event-derived wrappers and grants only those checked signatures.
  EXECUTE 'REVOKE ALL ON FUNCTION register_flagship_causal_proposal() FROM PUBLIC, aq_loop, aq_actor, aq_governance';
  EXECUTE 'REVOKE ALL ON FUNCTION stage_flagship_causal_proposal(bigint,bigint,text,text,text,text,text,text,real,text) FROM PUBLIC, aq_loop, aq_actor, aq_governance';
  EXECUTE 'REVOKE ALL ON FUNCTION resolve_flagship_causal_edge(bigint,bigint,bigint,boolean) FROM PUBLIC, aq_loop, aq_actor, aq_governance';

  EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE aq_owner IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC';
  EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE aq_owner IN SCHEMA aq_control REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC';
  EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE aq_control_owner IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC';
  EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE aq_control_owner IN SCHEMA aq_control REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC';
END $grants$;
