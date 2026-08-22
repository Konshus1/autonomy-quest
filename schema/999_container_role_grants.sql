-- Container principal grants. External installs without these roles skip this block.
DO $grants$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='aq_loop')
     OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='aq_actor')
     OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='aq_governance')
     OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='aq_control_owner')
     OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='aq_evaluator') THEN
    RAISE NOTICE 'container database roles absent; skipping container-only grants';
    RETURN;
  END IF;

  -- aq_loop operates the ordinary runtime ledger. It cannot create shadow objects or inherit
  -- execution on every future function, and it cannot write any authority/evidence relation.
  EXECUTE 'REVOKE CREATE ON SCHEMA public FROM aq_loop';
  EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO aq_loop';
  EXECUTE 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO aq_loop';
  EXECUTE 'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON causal_edge, causal_principle_transition, causal_principle_plan_usage, causal_principle_plan_outcome FROM aq_loop';
  EXECUTE 'REVOKE UPDATE, DELETE, TRUNCATE ON plan_governance_defer_outbox FROM aq_loop';
  EXECUTE 'REVOKE UPDATE, DELETE, TRUNCATE ON plan_outcome_evaluation_outbox FROM aq_loop';
  -- Append-only loop-owned telemetry/audit tables (self-correction shadow + stage-2 arbiter).
  -- aq_loop KEEPS INSERT (it writes these) but loses UPDATE/DELETE, so immutability is grant-backed
  -- (defense-in-depth) as well as trigger-enforced. Without this the blanket GRANT on line 16 hands
  -- aq_loop UPDATE/DELETE here and append-only would rest on the trigger alone.
  --
  -- REFACTOR LATER (cheap-fix for now, per operator 2026-08-11): this grant-all-then-revoke-sensitive
  -- model is the accidental complexity behind repeated per-table/sequence grant churn (every new
  -- loop table re-triggers it). The clean fix is to move governed + append-only relations into their
  -- own schema and use ONE `REVOKE USAGE ON SCHEMA governance FROM aq_loop`: new loop tables inherit
  -- write, new governed tables are off-limits by default, and this hand-maintained revoke list goes
  -- away. Tracked as a follow-up (see BB note: schema-separation refactor).
  EXECUTE 'REVOKE UPDATE, DELETE, TRUNCATE ON self_correction_shadow_log, self_correction_audit_request, self_correction_arbiter_action FROM aq_loop';
  -- loop_heartbeat (028, DEPLOY-HYGIENE #4834) is INTENTIONALLY NOT revoked here: it is a
  -- single-row upsert liveness signal, so aq_loop must keep INSERT *and* UPDATE from the blanket
  -- grant above. It is not an authority/evidence table.

  -- Comms durable tables (#4834 agent-comms, migrated in 029). The envelope journal holds IMMUTABLE
  -- untrusted claims and the import ledger is append-only: the store only ever INSERTs them (relay +
  -- import use INSERT ... ON CONFLICT DO NOTHING; the store never issues UPDATE/DELETE against
  -- either). aq_loop is the role most exposed to untrusted replica input, so — like the append-only
  -- self_correction_* tables above, and because NO immutability trigger backs these — revoke its
  -- UPDATE/DELETE so it cannot rewrite or erase a stored claim/import at the row level (the design
  -- invariant "a replica's claim is never mutated" then holds at the DB layer, not just the API).
  EXECUTE 'REVOKE UPDATE, DELETE, TRUNCATE ON ralph_comms_envelopes, ralph_comms_imports FROM aq_loop';
  -- Verification is a PARENT-OWNED upsert (INSERT ... ON CONFLICT (envelope_id) DO UPDATE), so
  -- aq_loop keeps INSERT+UPDATE there; it is never row-deleted, so drop DELETE/TRUNCATE.
  EXECUTE 'REVOKE DELETE, TRUNCATE ON ralph_comms_verifications FROM aq_loop';
  -- analytics_events (031) is the mission MEASURE the loop is GRADED on. The loop READS it to grade
  -- progress but must never WRITE it, or it could forge its own scoreboard. The blanket GRANT on
  -- line 16 handed aq_loop all four verbs; strip the writes (SELECT stays) so the boundary survives.
  EXECUTE 'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON analytics_events FROM aq_loop';

  -- aq_actor is the UNTRUSTED seeded act-phase agent's credential, surfaced by the curated aqdb
  -- skill (#4834). LEAST-PRIVILEGE / DEFAULT-DENY READS: it may read ONLY the business tables the
  -- skill curates (customers, subscriptions) and write only those; it must NOT be able to SELECT
  -- the authority/evidence tables (runs, work, learnings, causal_* and governance bookkeeping) —
  -- reading those would let an untrusted agent exfiltrate mission-internal evidence. A prior
  -- revision granted `SELECT ON ALL TABLES`, which made the skill's "cannot reach authority/
  -- evidence tables" claim false for reads; this revokes any such blanket read FIRST (so the
  -- tightening also lands on an already-provisioned database) and then re-grants only the two
  -- business tables. Scoped to schema public and to aq_actor alone: it does not touch any other
  -- principal's grants, and it does not touch a separate schema (e.g. the agent_memory schema /
  -- world_model AGE graph a later branch grants aq_actor), so a future per-schema grant is not
  -- clobbered here.
  EXECUTE 'REVOKE SELECT ON ALL TABLES IN SCHEMA public FROM aq_actor';
  EXECUTE 'GRANT SELECT ON customers, subscriptions TO aq_actor';
  EXECUTE 'GRANT INSERT, UPDATE, DELETE ON customers, subscriptions TO aq_actor';
  EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE subscriptions_id_seq TO aq_actor';
  -- analytics_events (031) is the un-forgeable MEASURE. aq_actor may READ its scoreboard (the blanket
  -- REVOKE SELECT above stripped it; re-grant the read) but must NEVER write it: the whole "the agent
  -- can't fake the number it's graded on" property is this one GRANT. Re-assert no write.
  EXECUTE 'GRANT SELECT ON analytics_events TO aq_actor';
  EXECUTE 'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON analytics_events FROM aq_actor';
  -- Defense-in-depth (now redundant with the default-deny above, but kept so intent is explicit
  -- and a re-appearing blanket grant still cannot hand aq_actor WRITE on authority/evidence).
  EXECUTE 'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON causal_edge, ralph_causal_edges, causal_principle_transition, causal_principle_plan_usage, causal_principle_plan_outcome, runs, work, learnings, planning_prediction, plan_acquisition, impasse_meta_mode_decision FROM aq_actor';

  EXECUTE 'GRANT SELECT ON ALL TABLES IN SCHEMA public TO aq_governance';
  -- Manual promotion is a checked function call, never raw transition/use/outcome evidence DML.
  EXECUTE 'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON causal_principle_transition, causal_principle_plan_usage, causal_principle_plan_outcome FROM aq_governance';
  EXECUTE 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO aq_governance';

  EXECUTE 'GRANT SELECT ON ALL TABLES IN SCHEMA public TO aq_evaluator';
  EXECUTE 'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public FROM aq_evaluator';
  EXECUTE 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO aq_evaluator';

  -- No runtime principal inherits PostgreSQL's default PUBLIC function execution. Grants below
  -- are an allowlist; trigger invocation does not require direct EXECUTE by the row writer.
  EXECUTE 'REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC, aq_loop, aq_actor, aq_governance, aq_evaluator';
  EXECUTE 'REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA aq_control FROM PUBLIC, aq_loop, aq_actor, aq_governance, aq_evaluator';

  -- The NOLOGIN owner is the trusted code/data domain. The migration owner remains offline.
  EXECUTE 'ALTER SCHEMA aq_control OWNER TO aq_control_owner';
  EXECUTE 'ALTER TABLE aq_control.evidence_event OWNER TO aq_control_owner';
  EXECUTE 'ALTER TABLE aq_control.evidence_application OWNER TO aq_control_owner';
  EXECUTE 'ALTER TABLE aq_control.execution_context OWNER TO aq_control_owner';
  EXECUTE 'ALTER TABLE aq_control.grounding_observation OWNER TO aq_control_owner';
  EXECUTE 'ALTER TABLE aq_control.grounding_promotion OWNER TO aq_control_owner';
  EXECUTE 'ALTER TABLE aq_control.grounding_promotion_support OWNER TO aq_control_owner';
  EXECUTE 'ALTER TABLE aq_control.plan_authorization OWNER TO aq_control_owner';
  EXECUTE 'ALTER TABLE aq_control.grounding_demotion OWNER TO aq_control_owner';
  EXECUTE 'ALTER TABLE aq_control.plan_authorization_governor OWNER TO aq_control_owner';
  EXECUTE 'ALTER TABLE aq_control.plan_authorization_outcome OWNER TO aq_control_owner';
  EXECUTE 'ALTER TABLE aq_control.plan_outcome_application OWNER TO aq_control_owner';
  EXECUTE 'ALTER TABLE aq_control.plan_outcome_observation OWNER TO aq_control_owner';
  EXECUTE 'ALTER TABLE plan_outcome_evaluation_outbox OWNER TO aq_control_owner';
  EXECUTE 'ALTER TABLE plan_governance_defer_outbox OWNER TO aq_control_owner';
  EXECUTE 'ALTER SEQUENCE plan_governance_defer_outbox_defer_id_seq OWNER TO aq_control_owner';
  EXECUTE 'ALTER FUNCTION aq_control.reject_immutable_event_mutation() OWNER TO aq_control_owner';
  EXECUTE 'ALTER FUNCTION aq_control.attest_acquisition_evidence(bigint,bigint,integer) OWNER TO aq_control_owner';
  EXECUTE 'ALTER FUNCTION aq_control.attest_prediction_resolution(bigint,bigint) OWNER TO aq_control_owner';
  EXECUTE 'ALTER FUNCTION aq_control.register_execution_context(text,text,text,text,text,jsonb) OWNER TO aq_control_owner';
  EXECUTE 'ALTER FUNCTION aq_control.attest_grounding_observation(uuid,text,text,text,text,text,text,double precision,double precision,jsonb) OWNER TO aq_control_owner';
  EXECUTE 'ALTER FUNCTION aq_control.promote_grounded_principle(text,text,text,uuid,text) OWNER TO aq_control_owner';
  EXECUTE 'ALTER FUNCTION aq_control.attest_and_stage_acquisition(bigint,bigint,integer) OWNER TO aq_control_owner';
  EXECUTE 'ALTER FUNCTION aq_control.attest_and_apply_prediction(bigint,bigint) OWNER TO aq_control_owner';
  EXECUTE 'ALTER FUNCTION aq_control.authorize_plan(text,bigint,jsonb) OWNER TO aq_control_owner';
  EXECUTE 'ALTER FUNCTION aq_control.populate_plan_authorization_governors() OWNER TO aq_control_owner';
  EXECUTE 'ALTER FUNCTION aq_control.fill_plan_outcome_outbox_authorization() OWNER TO aq_control_owner';
  EXECUTE 'ALTER FUNCTION aq_control.require_checked_run_outcome_event() OWNER TO aq_control_owner';
  EXECUTE 'ALTER FUNCTION aq_control.enqueue_checked_run_outcome_event() OWNER TO aq_control_owner';
  EXECUTE 'ALTER FUNCTION aq_control.register_mined_principle(text,text,text,bigint[],bigint[]) OWNER TO aq_control_owner';
  EXECUTE 'ALTER FUNCTION aq_control.attest_and_apply_grounding_observation(uuid,text,text,text,text,text,text,double precision,double precision,jsonb) OWNER TO aq_control_owner';
  EXECUTE 'ALTER FUNCTION aq_control.attest_and_apply_plan_outcome(bigint,jsonb) OWNER TO aq_control_owner';
  EXECUTE 'ALTER FUNCTION aq_control.pending_plan_outcome_runs(integer) OWNER TO aq_control_owner';
  EXECUTE 'ALTER FUNCTION aq_control.quarantine_plan_outcome(bigint,text) OWNER TO aq_control_owner';
  -- The terminal/open-acquisition triggers call this constant helper as the effective row writer.
  -- Keep it controlled by the NOLOGIN owner and expose only the harmless lookup to the loop.
  EXECUTE 'ALTER FUNCTION aq_terminal_work_statuses() OWNER TO aq_control_owner';
  EXECUTE 'GRANT EXECUTE ON FUNCTION aq_terminal_work_statuses() TO aq_loop';

  EXECUTE 'GRANT USAGE ON SCHEMA aq_control TO aq_loop, aq_governance, aq_evaluator';
  EXECUTE 'REVOKE ALL ON aq_control.evidence_event, aq_control.evidence_application, aq_control.execution_context, aq_control.grounding_observation, aq_control.grounding_promotion, aq_control.grounding_promotion_support, aq_control.plan_authorization, aq_control.grounding_demotion, aq_control.plan_authorization_governor, aq_control.plan_authorization_outcome, aq_control.plan_outcome_application, aq_control.plan_outcome_observation FROM aq_loop, aq_actor, aq_governance, aq_evaluator';
  EXECUTE 'REVOKE ALL ON FUNCTION aq_control.attest_acquisition_evidence(bigint,bigint,integer) FROM aq_governance, aq_evaluator';
  EXECUTE 'REVOKE ALL ON FUNCTION aq_control.attest_prediction_resolution(bigint,bigint) FROM aq_governance, aq_evaluator';
  EXECUTE 'GRANT EXECUTE ON FUNCTION aq_control.register_execution_context(text,text,text,text,text,jsonb) TO aq_evaluator';
  EXECUTE 'GRANT EXECUTE ON FUNCTION aq_control.attest_and_apply_grounding_observation(uuid,text,text,text,text,text,text,double precision,double precision,jsonb) TO aq_evaluator';
  EXECUTE 'GRANT EXECUTE ON FUNCTION aq_control.attest_and_apply_plan_outcome(bigint,jsonb) TO aq_evaluator';
  EXECUTE 'GRANT EXECUTE ON FUNCTION aq_control.pending_plan_outcome_runs(integer) TO aq_evaluator';
  EXECUTE 'GRANT EXECUTE ON FUNCTION aq_control.quarantine_plan_outcome(bigint,text) TO aq_evaluator';
  EXECUTE 'GRANT EXECUTE ON FUNCTION aq_control.attest_and_stage_acquisition(bigint,bigint,integer) TO aq_evaluator';
  EXECUTE 'GRANT EXECUTE ON FUNCTION aq_control.attest_and_apply_prediction(bigint,bigint) TO aq_evaluator';
  EXECUTE 'GRANT EXECUTE ON FUNCTION aq_control.promote_grounded_principle(text,text,text,uuid,text) TO aq_governance';
  EXECUTE 'GRANT EXECUTE ON FUNCTION aq_control.authorize_plan(text,bigint,jsonb) TO aq_governance';
  EXECUTE 'GRANT EXECUTE ON FUNCTION aq_control.register_mined_principle(text,text,text,bigint[],bigint[]) TO aq_loop';

  -- The control owner can verify public operational facts and apply authority consequences only
  -- through checked functions. It never receives LOGIN or membership in a runtime principal.
  EXECUTE 'GRANT SELECT ON work,runs,learnings,ralph_causal_edges,plan_evaluation,plan_acquisition,planning_prediction,principle_support_event,causal_edge,causal_principle_transition TO aq_control_owner';
  EXECUTE 'GRANT INSERT,UPDATE ON causal_edge TO aq_control_owner';
  EXECUTE 'GRANT USAGE,SELECT ON SEQUENCE causal_edge_edge_id_seq TO aq_control_owner';
  EXECUTE 'GRANT INSERT ON causal_principle_transition TO aq_control_owner';
  EXECUTE 'GRANT USAGE,SELECT ON SEQUENCE causal_principle_transition_id_seq TO aq_control_owner';

  -- Item 4 wrappers retain Python-compatible signatures but derive every consequence from the
  -- trusted event and are owned by the NOLOGIN control principal. Only aq_loop gets the checked
  -- compatibility calls; aq_governance can attest but cannot bypass their argument equality.
  EXECUTE 'ALTER FUNCTION validate_causal_principle_transition_insert() OWNER TO aq_control_owner';
  EXECUTE 'ALTER FUNCTION validate_causal_edge_run_evidence() OWNER TO aq_control_owner';
  EXECUTE 'ALTER FUNCTION register_flagship_causal_proposal() OWNER TO aq_control_owner';
  EXECUTE 'ALTER FUNCTION stage_flagship_causal_proposal(bigint,bigint,text,text,text,text,text,text,real,text) OWNER TO aq_control_owner';
  EXECUTE 'ALTER FUNCTION resolve_flagship_causal_edge(bigint,bigint,bigint,boolean) OWNER TO aq_control_owner';
  EXECUTE 'REVOKE ALL ON FUNCTION validate_causal_principle_transition_insert() FROM PUBLIC, aq_loop, aq_actor, aq_governance, aq_evaluator';
  EXECUTE 'REVOKE ALL ON FUNCTION validate_causal_edge_run_evidence() FROM PUBLIC, aq_loop, aq_actor, aq_governance, aq_evaluator';
  EXECUTE 'REVOKE ALL ON FUNCTION register_flagship_causal_proposal() FROM PUBLIC, aq_loop, aq_actor, aq_governance, aq_evaluator';
  EXECUTE 'REVOKE ALL ON FUNCTION stage_flagship_causal_proposal(bigint,bigint,text,text,text,text,text,text,real,text) FROM PUBLIC, aq_loop, aq_actor, aq_governance, aq_evaluator';
  EXECUTE 'REVOKE ALL ON FUNCTION resolve_flagship_causal_edge(bigint,bigint,bigint,boolean) FROM PUBLIC, aq_loop, aq_actor, aq_governance, aq_evaluator';
  EXECUTE 'GRANT EXECUTE ON FUNCTION stage_flagship_causal_proposal(bigint,bigint,text,text,text,text,text,text,real,text) TO aq_loop';
  EXECUTE 'GRANT EXECUTE ON FUNCTION resolve_flagship_causal_edge(bigint,bigint,bigint,boolean) TO aq_loop';

  EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE aq_owner IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC';
  EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE aq_owner IN SCHEMA aq_control REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC';
  EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE aq_control_owner IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC';
  EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE aq_control_owner IN SCHEMA aq_control REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC';
END $grants$;
