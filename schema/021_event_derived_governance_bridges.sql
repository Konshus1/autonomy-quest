-- Scope B M2 item 4: bridge authority is derived from immutable trusted events.
BEGIN;

CREATE OR REPLACE FUNCTION public.validate_causal_principle_transition_insert()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $$
DECLARE
  latest_status text; miner text; evidence_floor bigint := 0;
  execution_ids bigint; contexts bigint; domains bigint;
BEGIN
  PERFORM pg_advisory_xact_lock(
    hashtextextended(NEW.cause || chr(31) || NEW.effect || chr(31) || NEW.scope,0));
  SELECT to_status INTO latest_status FROM public.causal_principle_transition
   WHERE cause=NEW.cause AND effect=NEW.effect AND scope=NEW.scope
   ORDER BY id DESC LIMIT 1;
  IF NEW.transition_kind='mined' THEN
    IF latest_status IS NOT NULL THEN
      RAISE EXCEPTION 'mined transition requires a new principle identity';
    END IF;
    RETURN NEW;
  END IF;
  IF latest_status IS NULL OR latest_status IS DISTINCT FROM NEW.from_status THEN
    RAISE EXCEPTION 'transition predecessor mismatch: latest %, supplied %',
                    latest_status,NEW.from_status;
  END IF;
  IF NEW.transition_kind='promote' THEN
    SELECT transitioned_by INTO miner FROM public.causal_principle_transition
     WHERE cause=NEW.cause AND effect=NEW.effect AND scope=NEW.scope
       AND transition_kind='mined' ORDER BY id LIMIT 1;
    IF miner IS NULL OR miner=NEW.adjudicated_by THEN
      RAISE EXCEPTION 'promotion requires an independent adjudicator';
    END IF;
    SELECT coalesce(max(id),0) INTO evidence_floor
      FROM public.causal_principle_transition
     WHERE cause=NEW.cause AND effect=NEW.effect AND scope=NEW.scope
       AND transition_kind='demote';
    SELECT count(DISTINCT environment_id),count(DISTINCT environment_fingerprint),
           count(DISTINCT environment_domain)
      INTO execution_ids,contexts,domains FROM public.causal_principle_transition
     WHERE cause=NEW.cause AND effect=NEW.effect AND scope=NEW.scope
       AND transition_kind='shadow_test' AND evidence_result='supports' AND id>evidence_floor;
    IF execution_ids<2 OR contexts<2 OR domains<2 THEN
      RAISE EXCEPTION 'promotion requires two cross-environment supports';
    END IF;
    IF coalesce(NEW.detail->>'applies_here','false')<>'true'
       OR btrim(coalesce(NEW.detail->>'applies_here_how',''))='' THEN
      RAISE EXCEPTION 'promotion requires applies_here provenance';
    END IF;
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.validate_causal_edge_run_evidence()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $$
DECLARE rid bigint;
BEGIN
  IF cardinality(NEW.evidence_run_ids) = 0 THEN
    RAISE EXCEPTION 'causal edge requires completed evidence_run_ids';
  END IF;
  FOREACH rid IN ARRAY NEW.evidence_run_ids LOOP
    IF rid <= 0 OR NOT EXISTS (
      SELECT 1 FROM public.runs r WHERE r.id=rid AND r.completed_at IS NOT NULL
    ) THEN RAISE EXCEPTION 'causal edge evidence run % is missing or incomplete', rid; END IF;
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

CREATE OR REPLACE FUNCTION public.register_flagship_causal_proposal()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $$
DECLARE
  v_event_text text; v_event uuid; v_payload jsonb; v_digest text;
  v_run bigint; v_work bigint;
BEGIN
  v_event_text := current_setting('aq_control.event_id',true);
  IF btrim(coalesce(v_event_text,'')) <> '' THEN
    v_event := v_event_text::uuid;
    SELECT payload,payload_digest,run_id,work_id
      INTO v_payload,v_digest,v_run,v_work
      FROM aq_control.evidence_event
     WHERE event_id=v_event AND event_kind='acquisition_completed';
    IF NOT FOUND OR v_payload->>'source_action' IS DISTINCT FROM NEW.source_action
       OR v_payload->>'direct_effect' IS DISTINCT FROM NEW.direct_effect
       OR v_payload->'scope' IS DISTINCT FROM NEW.scope_conditions::jsonb THEN
      RAISE EXCEPTION 'trusted acquisition event does not match inserted causal edge';
    END IF;
    INSERT INTO public.causal_principle_transition(
      cause,effect,scope,from_status,to_status,transition_kind,
      environment_id,environment_domain,environment_fingerprint,mission_id,harness,
      evidence_ref,evidence_result,bounded_experiment,authority_after,transitioned_by,
      rule_version,automatic,detail)
    VALUES (
      NEW.source_action,NEW.direct_effect,NEW.scope_conditions,NULL,'provisional','mined',
      'governance-event:'||v_event,'flagship-acquisition',v_digest,
      'work:'||v_work,'aq-control/m2','governance-event:'||v_event,'mined',false,false,
      'aq-control-owner','aq-governed-principle-v1',false,
      jsonb_build_object('edge_id',NEW.edge_id,'event_id',v_event,
                         'run_id',v_run,'evidence_run_ids',to_jsonb(NEW.evidence_run_ids)))
    ON CONFLICT (cause,effect,scope,transition_kind,evidence_ref) DO NOTHING;
    RETURN NEW;
  END IF;

  -- Migration/test-owner compatibility only. Runtime principals have neither raw edge DML nor
  -- direct EXECUTE on this trigger function, so this path cannot authorize an aq_loop claim.
  v_run := NEW.evidence_run_ids[1];
  SELECT work_id INTO v_work FROM public.runs WHERE id=v_run;
  INSERT INTO public.causal_principle_transition(
    cause,effect,scope,from_status,to_status,transition_kind,
    environment_id,environment_domain,environment_fingerprint,mission_id,harness,
    evidence_ref,evidence_result,bounded_experiment,authority_after,transitioned_by,
    rule_version,automatic,detail)
  VALUES (
    NEW.source_action,NEW.direct_effect,NEW.scope_conditions,NULL,'provisional','mined',
    'owner-run:'||v_run,'owner-migration',md5('owner-migration/work:'||v_work),
    'work:'||v_work,'owner-migration','owner-run:'||v_run||'/causal-edge:'||NEW.edge_id,
    'mined',false,false,'aq-owner-migration','aq-governed-principle-v1',false,
    jsonb_build_object('edge_id',NEW.edge_id,'evidence_run_ids',to_jsonb(NEW.evidence_run_ids)))
  ON CONFLICT (cause,effect,scope,transition_kind,evidence_ref) DO NOTHING;
  RETURN NEW;
END $$;

-- Compatibility signature retained for the loop adapter, but every caller field must equal the
-- immutable acquisition event. Missing or mismatched trusted evidence rejects before any edge DML.
CREATE OR REPLACE FUNCTION public.stage_flagship_causal_proposal(
  p_run_id bigint, p_acquisition_id bigint, p_action text, p_effect text,
  p_mission_measure text, p_direction text, p_mechanism text, p_scope text,
  p_certainty real, p_evidence text)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_event uuid; v_payload jsonb; v_edge bigint; v_applied bigint;
BEGIN
  SELECT event_id,payload INTO v_event,v_payload
    FROM aq_control.evidence_event
   WHERE event_kind='acquisition_completed' AND run_id=p_run_id
     AND acquisition_id=p_acquisition_id
   ORDER BY attested_at,event_id LIMIT 1 FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'trusted acquisition evidence event is missing'; END IF;
  IF v_payload->>'source_action' IS DISTINCT FROM p_action
     OR v_payload->>'direct_effect' IS DISTINCT FROM p_effect
     OR v_payload->>'mission_measure' IS DISTINCT FROM p_mission_measure
     OR v_payload->>'direction' IS DISTINCT FROM p_direction
     OR v_payload->>'mechanism' IS DISTINCT FROM coalesce(p_mechanism,'')
     OR v_payload->'scope' IS DISTINCT FROM p_scope::jsonb
     OR (v_payload->>'certainty')::real IS DISTINCT FROM p_certainty
     OR v_payload->>'evidence' IS DISTINCT FROM p_evidence THEN
    RAISE EXCEPTION 'proposal arguments do not match trusted acquisition evidence event';
  END IF;
  SELECT edge_id INTO v_applied FROM aq_control.evidence_application WHERE event_id=v_event;
  IF FOUND THEN RETURN v_applied; END IF;
  PERFORM set_config('aq_control.event_id',v_event::text,true);
  INSERT INTO public.causal_edge(
    source_action,direct_effect,mission_measure,relation_direction,
    mechanism_description,scope_conditions,predicted_certainty,evidence_run_ids,support_count)
  VALUES (
    v_payload->>'source_action',v_payload->>'direct_effect',v_payload->>'mission_measure',
    v_payload->>'direction',nullif(v_payload->>'mechanism',''),(v_payload->'scope')::text,
    (v_payload->>'certainty')::real,ARRAY[p_run_id],0)
  ON CONFLICT (source_action,direct_effect,scope_conditions) DO NOTHING
  RETURNING edge_id INTO v_edge;
  IF v_edge IS NULL THEN
    SELECT edge_id INTO v_edge FROM public.causal_edge
     WHERE source_action=v_payload->>'source_action'
       AND direct_effect=v_payload->>'direct_effect'
       AND scope_conditions=(v_payload->'scope')::text;
  END IF;
  IF v_edge IS NULL THEN RAISE EXCEPTION 'trusted acquisition event produced no exact edge'; END IF;
  INSERT INTO aq_control.evidence_application(event_id,application_kind,edge_id)
    VALUES (v_event,'stage_edge',v_edge);
  PERFORM set_config('aq_control.event_id','',true);
  RETURN v_edge;
END $$;

-- Confirmation is checked against, and mutation is derived from, the trusted event. The unique
-- application row makes the same resolution event idempotent under replay and concurrency.
CREATE OR REPLACE FUNCTION public.resolve_flagship_causal_edge(
  p_edge_id bigint, p_run_id bigint, p_prediction_id bigint, p_confirmed boolean)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_event uuid; v_payload jsonb; v_confirmed boolean; v_existing uuid;
BEGIN
  SELECT event_id,payload INTO v_event,v_payload
    FROM aq_control.evidence_event
   WHERE event_kind='prediction_resolved' AND run_id=p_run_id
     AND prediction_id=p_prediction_id AND edge_id=p_edge_id
   ORDER BY attested_at,event_id LIMIT 1 FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'trusted prediction resolution event is missing'; END IF;
  v_confirmed := (v_payload->>'direction_confirmed')::boolean;
  IF p_confirmed IS DISTINCT FROM v_confirmed THEN
    RAISE EXCEPTION 'caller confirmation does not match trusted prediction resolution event';
  END IF;
  SELECT event_id INTO v_existing FROM aq_control.evidence_application WHERE event_id=v_event;
  IF FOUND THEN RETURN; END IF;
  INSERT INTO aq_control.evidence_application(
    event_id,application_kind,edge_id,applied_delta)
  VALUES (v_event,'resolve_edge',p_edge_id,CASE WHEN v_confirmed THEN 1 ELSE -1 END);
  IF v_confirmed THEN
    UPDATE public.causal_edge
       SET support_count=support_count+1,last_validated=clock_timestamp(),updated_at=clock_timestamp()
     WHERE edge_id=p_edge_id;
  ELSE
    UPDATE public.causal_edge
       SET falsified_by=p_run_id,last_validated=clock_timestamp(),updated_at=clock_timestamp()
     WHERE edge_id=p_edge_id;
  END IF;
  IF NOT FOUND THEN RAISE EXCEPTION 'trusted prediction event edge is missing'; END IF;
END $$;

REVOKE ALL ON FUNCTION public.validate_causal_principle_transition_insert() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.validate_causal_edge_run_evidence() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.register_flagship_causal_proposal() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.stage_flagship_causal_proposal(bigint,bigint,text,text,text,text,text,text,real,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.resolve_flagship_causal_edge(bigint,bigint,bigint,boolean) FROM PUBLIC;

DO $owners$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='aq_control_owner') THEN
    ALTER FUNCTION public.validate_causal_principle_transition_insert() OWNER TO aq_control_owner;
    ALTER FUNCTION public.validate_causal_edge_run_evidence() OWNER TO aq_control_owner;
    ALTER FUNCTION public.register_flagship_causal_proposal() OWNER TO aq_control_owner;
    ALTER FUNCTION public.stage_flagship_causal_proposal(bigint,bigint,text,text,text,text,text,text,real,text) OWNER TO aq_control_owner;
    ALTER FUNCTION public.resolve_flagship_causal_edge(bigint,bigint,bigint,boolean) OWNER TO aq_control_owner;
  END IF;
END $owners$;

COMMIT;
