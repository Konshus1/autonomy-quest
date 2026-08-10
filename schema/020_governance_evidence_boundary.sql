-- Scope B M2: trusted, immutable evidence events are the only admissibility inputs.
-- The operational loop may still write its ordinary runtime ledger, but cannot write this ledger.
BEGIN;

CREATE SCHEMA IF NOT EXISTS aq_control;
REVOKE ALL ON SCHEMA aq_control FROM PUBLIC;

CREATE TABLE IF NOT EXISTS aq_control.evidence_event (
  event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  event_kind text NOT NULL CHECK (event_kind IN ('acquisition_completed','prediction_resolved')),
  subject_key text NOT NULL UNIQUE CHECK (btrim(subject_key) <> ''),
  run_id bigint NOT NULL REFERENCES public.runs(id) ON DELETE RESTRICT,
  work_id bigint NOT NULL REFERENCES public.work(id) ON DELETE RESTRICT,
  acquisition_id bigint REFERENCES public.plan_acquisition(acquisition_id) ON DELETE RESTRICT,
  prediction_id bigint REFERENCES public.planning_prediction(prediction_id) ON DELETE RESTRICT,
  edge_id bigint REFERENCES public.causal_edge(edge_id) ON DELETE RESTRICT,
  payload jsonb NOT NULL CHECK (jsonb_typeof(payload)='object'),
  payload_digest text NOT NULL CHECK (
    payload_digest = encode(sha256(convert_to(payload::text,'UTF8')),'hex')),
  attested_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CHECK (
    (event_kind='acquisition_completed' AND acquisition_id IS NOT NULL
      AND prediction_id IS NULL AND edge_id IS NULL)
    OR
    (event_kind='prediction_resolved' AND acquisition_id IS NULL
      AND prediction_id IS NOT NULL AND edge_id IS NOT NULL)
  )
);

CREATE TABLE IF NOT EXISTS aq_control.evidence_application (
  event_id uuid PRIMARY KEY REFERENCES aq_control.evidence_event(event_id) ON DELETE RESTRICT,
  application_kind text NOT NULL CHECK (application_kind IN ('stage_edge','resolve_edge')),
  edge_id bigint NOT NULL REFERENCES public.causal_edge(edge_id) ON DELETE RESTRICT,
  applied_delta smallint CHECK (applied_delta IN (-1,1)),
  applied_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CHECK ((application_kind='stage_edge' AND applied_delta IS NULL)
      OR (application_kind='resolve_edge' AND applied_delta IS NOT NULL))
);

CREATE OR REPLACE FUNCTION aq_control.reject_immutable_event_mutation()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
  RAISE EXCEPTION 'trusted governance evidence is append-only';
END $$;

DROP TRIGGER IF EXISTS evidence_event_append_only ON aq_control.evidence_event;
CREATE TRIGGER evidence_event_append_only
  BEFORE UPDATE OR DELETE ON aq_control.evidence_event
  FOR EACH ROW EXECUTE FUNCTION aq_control.reject_immutable_event_mutation();
DROP TRIGGER IF EXISTS evidence_event_no_truncate ON aq_control.evidence_event;
CREATE TRIGGER evidence_event_no_truncate
  BEFORE TRUNCATE ON aq_control.evidence_event
  FOR EACH STATEMENT EXECUTE FUNCTION aq_control.reject_immutable_event_mutation();
DROP TRIGGER IF EXISTS evidence_application_append_only ON aq_control.evidence_application;
CREATE TRIGGER evidence_application_append_only
  BEFORE UPDATE OR DELETE ON aq_control.evidence_application
  FOR EACH ROW EXECUTE FUNCTION aq_control.reject_immutable_event_mutation();
DROP TRIGGER IF EXISTS evidence_application_no_truncate ON aq_control.evidence_application;
CREATE TRIGGER evidence_application_no_truncate
  BEFORE TRUNCATE ON aq_control.evidence_application
  FOR EACH STATEMENT EXECUTE FUNCTION aq_control.reject_immutable_event_mutation();

-- Called only by the separately credentialed governance service. Candidate fields are derived
-- from the completed acquisition receipt and its durable target step, never accepted as arguments.
CREATE OR REPLACE FUNCTION aq_control.attest_acquisition_evidence(
  p_acquisition_id bigint, p_run_id bigint, p_proposal_index integer)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $$
DECLARE
  v_work_id bigint; v_plan_id text; v_target_step_id text; v_status text;
  v_result jsonb; v_plan jsonb; v_proposal jsonb; v_target jsonb; v_scope_input jsonb;
  v_scope jsonb; v_payload jsonb; v_subject text; v_event uuid; v_certainty real;
  v_completed timestamptz; v_succeeded boolean;
BEGIN
  IF p_proposal_index < 0 THEN RAISE EXCEPTION 'proposal index must be non-negative'; END IF;
  SELECT pa.work_id,pa.plan_id,pa.target_step_id,pa.status,pa.result,w.plan,
         r.completed_at,r.succeeded
    INTO v_work_id,v_plan_id,v_target_step_id,v_status,v_result,v_plan,
         v_completed,v_succeeded
    FROM public.plan_acquisition pa
    JOIN public.work w ON w.id=pa.work_id
    JOIN public.runs r ON r.id=p_run_id AND r.work_id=pa.work_id
   WHERE pa.acquisition_id=p_acquisition_id;
  IF NOT FOUND OR v_status <> 'completed' OR v_completed IS NULL OR v_succeeded IS DISTINCT FROM true
     OR v_result IS NULL
     OR v_result->>'_aq_completion_run_id' IS DISTINCT FROM p_run_id::text THEN
    RAISE EXCEPTION 'acquisition evidence requires its exact successful completion run';
  END IF;
  v_proposal := v_result->'causal_proposals'->p_proposal_index;
  IF v_proposal IS NULL OR jsonb_typeof(v_proposal) <> 'object' THEN
    RAISE EXCEPTION 'acquisition evidence proposal is missing';
  END IF;
  SELECT step INTO v_target
    FROM jsonb_array_elements(coalesce(v_plan->'steps','[]'::jsonb)) step
   WHERE step->>'step_id'=v_target_step_id;
  IF v_target IS NULL
     OR v_proposal->>'source_action' IS DISTINCT FROM v_target->>'action'
     OR v_proposal->>'direct_effect' IS DISTINCT FROM v_target->>'expected_effect' THEN
    RAISE EXCEPTION 'acquisition evidence does not match the durable target step';
  END IF;
  IF v_proposal->>'direction' NOT IN ('toward','away','neutral')
     OR btrim(coalesce(v_proposal->>'evidence',''))='' THEN
    RAISE EXCEPTION 'acquisition evidence has invalid direction or empty evidence';
  END IF;
  v_scope_input := coalesce(v_proposal->'scope','[]'::jsonb);
  IF jsonb_typeof(v_scope_input) <> 'array' OR EXISTS (
    SELECT 1 FROM jsonb_array_elements(v_scope_input) item
     WHERE jsonb_typeof(item) <> 'object' OR NOT (item ? 'key' AND item ? 'value')
       OR (SELECT count(*) FROM jsonb_object_keys(item)) <> 2
  ) THEN RAISE EXCEPTION 'acquisition evidence scope must contain exact key/value records'; END IF;
  SELECT coalesce(jsonb_object_agg(item->>'key',item->>'value'),'{}'::jsonb)
    INTO v_scope FROM jsonb_array_elements(v_scope_input) item;
  BEGIN v_certainty := coalesce((v_proposal->>'certainty')::real,0.0);
  EXCEPTION WHEN invalid_text_representation THEN
    RAISE EXCEPTION 'acquisition evidence certainty is invalid';
  END;
  IF v_certainty < 0 OR v_certainty > 1 THEN
    RAISE EXCEPTION 'acquisition evidence certainty is outside zero to one';
  END IF;
  v_payload := jsonb_build_object(
    'run_id',p_run_id,'acquisition_id',p_acquisition_id,'work_id',v_work_id,
    'plan_id',v_plan_id,'target_step_id',v_target_step_id,
    'source_action',v_target->>'action','direct_effect',v_target->>'expected_effect',
    'mission_measure',coalesce(nullif(v_proposal->>'mission_measure',''),'mission_measure'),
    'direction',v_proposal->>'direction','mechanism',coalesce(v_proposal->>'mechanism',''),
    'scope',v_scope,'certainty',least(v_certainty,.99::real),
    'evidence',v_proposal->>'evidence');
  v_subject := format('acquisition:%s:run:%s:proposal:%s',
                      p_acquisition_id,p_run_id,p_proposal_index);
  INSERT INTO aq_control.evidence_event(
    event_kind,subject_key,run_id,work_id,acquisition_id,payload,payload_digest)
  VALUES ('acquisition_completed',v_subject,p_run_id,v_work_id,p_acquisition_id,v_payload,
          encode(sha256(convert_to(v_payload::text,'UTF8')),'hex'))
  ON CONFLICT (subject_key) DO NOTHING RETURNING event_id INTO v_event;
  IF v_event IS NULL THEN
    SELECT event_id INTO v_event FROM aq_control.evidence_event WHERE subject_key=v_subject;
  END IF;
  RETURN v_event;
END $$;

-- The trusted writer snapshots the exact stored resolution/support record. Later mutation of the
-- loop ledger cannot change the attested boolean, delta, evidence, or identity.
CREATE OR REPLACE FUNCTION aq_control.attest_prediction_resolution(
  p_prediction_id bigint, p_run_id bigint)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $$
DECLARE
  v_work_id bigint; v_edge_id bigint; v_executed boolean; v_confirmed boolean;
  v_direct boolean; v_outcome text; v_evidence text; v_delta smallint;
  v_support_outcome text; v_support_evidence text;
  v_payload jsonb; v_subject text; v_event uuid; v_completed timestamptz;
BEGIN
  SELECT pp.work_id,pp.edge_id,pp.executed,pp.direction_confirmed,
         pp.direct_effect_confirmed,pp.outcome_kind,pp.outcome_evidence,
         pse.delta,pse.outcome_kind,pse.evidence,r.completed_at
    INTO v_work_id,v_edge_id,v_executed,v_confirmed,v_direct,v_outcome,v_evidence,
         v_delta,v_support_outcome,v_support_evidence,v_completed
    FROM public.planning_prediction pp
    JOIN public.runs r ON r.id=p_run_id AND r.work_id=pp.work_id
    JOIN public.principle_support_event pse
      ON pse.prediction_id=pp.prediction_id AND pse.run_id=p_run_id AND pse.edge_id=pp.edge_id
   WHERE pp.prediction_id=p_prediction_id AND pp.resolved_run_id=p_run_id;
  IF NOT FOUND OR v_completed IS NULL OR v_executed IS DISTINCT FROM true
     OR v_edge_id IS NULL OR v_confirmed IS NULL OR btrim(coalesce(v_evidence,''))=''
     OR v_support_outcome IS DISTINCT FROM v_outcome
     OR v_support_evidence IS DISTINCT FROM v_evidence
     OR v_delta IS DISTINCT FROM (CASE WHEN v_confirmed THEN 1 ELSE -1 END)::smallint THEN
    RAISE EXCEPTION 'prediction evidence requires one exact completed resolution/support event';
  END IF;
  v_payload := jsonb_build_object(
    'prediction_id',p_prediction_id,'run_id',p_run_id,'work_id',v_work_id,
    'edge_id',v_edge_id,'executed',v_executed,'direct_effect_confirmed',v_direct,
    'direction_confirmed',v_confirmed,'delta',v_delta,
    'outcome_kind',v_outcome,'evidence',v_evidence);
  v_subject := format('prediction:%s:run:%s',p_prediction_id,p_run_id);
  INSERT INTO aq_control.evidence_event(
    event_kind,subject_key,run_id,work_id,prediction_id,edge_id,payload,payload_digest)
  VALUES ('prediction_resolved',v_subject,p_run_id,v_work_id,p_prediction_id,v_edge_id,
          v_payload,encode(sha256(convert_to(v_payload::text,'UTF8')),'hex'))
  ON CONFLICT (subject_key) DO NOTHING RETURNING event_id INTO v_event;
  IF v_event IS NULL THEN
    SELECT event_id INTO v_event FROM aq_control.evidence_event WHERE subject_key=v_subject;
  END IF;
  RETURN v_event;
END $$;

REVOKE ALL ON FUNCTION aq_control.attest_acquisition_evidence(bigint,bigint,integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION aq_control.attest_prediction_resolution(bigint,bigint) FROM PUBLIC;
REVOKE ALL ON FUNCTION aq_control.reject_immutable_event_mutation() FROM PUBLIC;
REVOKE ALL ON aq_control.evidence_event,aq_control.evidence_application FROM PUBLIC;

COMMIT;
