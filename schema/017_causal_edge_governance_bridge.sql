-- C4: flagship causal claims are proposals until the governed lifecycle authorizes them.
BEGIN;
SET search_path = public, ag_catalog, "$user";

CREATE TABLE IF NOT EXISTS causal_edge_quarantine (
    edge_id bigint PRIMARY KEY,
    edge jsonb NOT NULL,
    reason text NOT NULL,
    quarantined_at timestamptz NOT NULL DEFAULT now()
);

-- Empty/dangling legacy claims never acquired authority. Preserve their bytes for audit, then
-- remove them from the active substrate before making run provenance mandatory.
INSERT INTO causal_edge_quarantine(edge_id, edge, reason)
SELECT e.edge_id, to_jsonb(e), 'missing or dangling completed-run evidence'
FROM causal_edge e
WHERE cardinality(e.evidence_run_ids) = 0
   OR EXISTS (SELECT 1 FROM unnest(e.evidence_run_ids) rid
              WHERE NOT EXISTS (SELECT 1 FROM runs r WHERE r.id=rid AND r.completed_at IS NOT NULL))
ON CONFLICT (edge_id) DO NOTHING;
DELETE FROM causal_edge e
WHERE cardinality(e.evidence_run_ids) = 0
   OR EXISTS (SELECT 1 FROM unnest(e.evidence_run_ids) rid
              WHERE NOT EXISTS (SELECT 1 FROM runs r WHERE r.id=rid AND r.completed_at IS NOT NULL));

ALTER TABLE causal_edge DROP CONSTRAINT IF EXISTS causal_edge_has_run_evidence;
ALTER TABLE causal_edge ADD CONSTRAINT causal_edge_has_run_evidence
  CHECK (cardinality(evidence_run_ids) > 0);

CREATE OR REPLACE FUNCTION validate_causal_edge_run_evidence()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE rid bigint;
BEGIN
  IF cardinality(NEW.evidence_run_ids) = 0 THEN
    RAISE EXCEPTION 'causal edge requires completed evidence_run_ids';
  END IF;
  FOREACH rid IN ARRAY NEW.evidence_run_ids LOOP
    IF rid <= 0 OR NOT EXISTS (
      SELECT 1 FROM runs r WHERE r.id=rid AND r.completed_at IS NOT NULL
    ) THEN
      RAISE EXCEPTION 'causal edge evidence run % is missing or incomplete', rid;
    END IF;
  END LOOP;
  BEGIN
    IF NEW.scope_conditions IS NULL OR jsonb_typeof(NEW.scope_conditions::jsonb) <> 'object' THEN
      RAISE EXCEPTION 'causal edge scope_conditions must be a canonical JSON object';
    END IF;
  EXCEPTION WHEN invalid_text_representation THEN
    RAISE EXCEPTION 'causal edge scope_conditions must be a canonical JSON object';
  END;
  NEW.scope_conditions := (NEW.scope_conditions::jsonb)::text;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS causal_edge_validate_run_evidence ON causal_edge;
CREATE TRIGGER causal_edge_validate_run_evidence
  BEFORE INSERT OR UPDATE OF evidence_run_ids, scope_conditions ON causal_edge
  FOR EACH ROW EXECUTE FUNCTION validate_causal_edge_run_evidence();

CREATE UNIQUE INDEX IF NOT EXISTS causal_edge_governed_identity_uq
  ON causal_edge(source_action, direct_effect, scope_conditions);

-- Any normalized flagship edge starts the reviewed lifecycle at provisional. The triggering
-- insert and transition share one transaction, so neither can survive alone.
CREATE OR REPLACE FUNCTION register_flagship_causal_proposal()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE rid bigint := NEW.evidence_run_ids[1]; wid bigint;
BEGIN
  SELECT work_id INTO wid FROM runs WHERE id=rid;
  INSERT INTO causal_principle_transition(
    cause,effect,scope,from_status,to_status,transition_kind,
    environment_id,environment_domain,environment_fingerprint,mission_id,harness,
    evidence_ref,evidence_result,bounded_experiment,authority_after,transitioned_by,
    rule_version,automatic,detail)
  VALUES (
    NEW.source_action,NEW.direct_effect,NEW.scope_conditions,NULL,'provisional','mined',
    'mission-run:'||rid,'flagship-acquisition',md5('flagship-acquisition/work:'||wid||'/default/v1'),
    'work:'||wid,'default/v1','run:'||rid||'/causal-edge:'||NEW.edge_id,'mined',false,false,
    'aq-acquisition-actor','aq-governed-principle-v1',false,
    jsonb_build_object('edge_id',NEW.edge_id,'evidence_run_ids',to_jsonb(NEW.evidence_run_ids)))
  ON CONFLICT (cause,effect,scope,transition_kind,evidence_ref) DO NOTHING;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS causal_edge_register_proposal ON causal_edge;
CREATE TRIGGER causal_edge_register_proposal
  AFTER INSERT ON causal_edge FOR EACH ROW EXECUTE FUNCTION register_flagship_causal_proposal();

-- Valid pre-migration rows also enter as inert proposals; migration never grants authority.
INSERT INTO causal_principle_transition(
  cause,effect,scope,from_status,to_status,transition_kind,
  environment_id,environment_domain,environment_fingerprint,mission_id,harness,
  evidence_ref,evidence_result,bounded_experiment,authority_after,transitioned_by,
  rule_version,automatic,detail)
SELECT e.source_action,e.direct_effect,e.scope_conditions,NULL,'provisional','mined',
  'mission-run:'||e.evidence_run_ids[1],'flagship-migration',
  md5('flagship-migration/work:'||r.work_id||'/default/v1'),'work:'||r.work_id,'default/v1',
  'run:'||e.evidence_run_ids[1]||'/causal-edge:'||e.edge_id,'mined',false,false,
  'aq-c4-migration','aq-governed-principle-v1',false,
  jsonb_build_object('edge_id',e.edge_id,'evidence_run_ids',to_jsonb(e.evidence_run_ids))
FROM causal_edge e JOIN runs r ON r.id=e.evidence_run_ids[1]
WHERE NOT EXISTS (
  SELECT 1 FROM causal_principle_transition t
  WHERE t.cause=e.source_action AND t.effect=e.direct_effect AND t.scope=e.scope_conditions
);

COMMIT;
