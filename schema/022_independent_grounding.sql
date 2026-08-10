-- Scope B M3: an evaluator-owned immutable grounding ledger feeds explicit manual promotion.
BEGIN;

-- Management/API stores are migration-owned so restricted runtime roles never require CREATE.
CREATE TABLE IF NOT EXISTS public.ralph_workstreams (
  id text PRIMARY KEY, title text NOT NULL, status text NOT NULL);
CREATE TABLE IF NOT EXISTS public.ralph_tasks (
  seq bigserial PRIMARY KEY, title text NOT NULL, workstream_id text NOT NULL, status text NOT NULL);
CREATE TABLE IF NOT EXISTS public.ralph_comms (seq bigserial PRIMARY KEY, payload jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS public.ralph_replication (seq bigserial PRIMARY KEY, payload jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS public.ralph_merges (seq bigserial PRIMARY KEY, payload jsonb NOT NULL);

CREATE TABLE IF NOT EXISTS aq_control.execution_context (
  context_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  instance_id text NOT NULL,
  execution_id text NOT NULL,
  domain text NOT NULL CHECK (btrim(domain)<>''),
  mission_id text NOT NULL CHECK (btrim(mission_id)<>''),
  harness text NOT NULL CHECK (btrim(harness)<>''),
  context_fingerprint text NOT NULL,
  attestation jsonb NOT NULL CHECK (jsonb_typeof(attestation)='object'),
  attestation_digest text NOT NULL CHECK (
    attestation_digest=encode(sha256(convert_to(attestation::text,'UTF8')),'hex')),
  registered_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(instance_id,execution_id),
  UNIQUE(attestation_digest),
  CHECK (instance_id ~ '^urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'),
  CHECK (btrim(execution_id)<>'')
);

CREATE TABLE IF NOT EXISTS aq_control.grounding_observation (
  observation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  context_id uuid NOT NULL REFERENCES aq_control.execution_context(context_id) ON DELETE RESTRICT,
  generation_transition_id bigint NOT NULL
    REFERENCES public.causal_principle_transition(id) ON DELETE RESTRICT,
  cause text NOT NULL CHECK (btrim(cause)<>''),
  effect text NOT NULL CHECK (btrim(effect)<>''),
  scope text NOT NULL,
  test_kind text NOT NULL CHECK (test_kind IN ('support','applicability','negative_control')),
  evidence_ref text NOT NULL CHECK (btrim(evidence_ref)<>''),
  expected_direction text CHECK (expected_direction IN ('increase','decrease')),
  observed_delta double precision NOT NULL CHECK (observed_delta>'-Infinity'::double precision AND observed_delta<'Infinity'::double precision),
  noise_tolerance double precision NOT NULL CHECK (noise_tolerance>=0 AND noise_tolerance<'Infinity'::double precision),
  result text NOT NULL CHECK (result IN ('supports','refutes','noise','passed','failed')),
  evidence_payload jsonb NOT NULL CHECK (jsonb_typeof(evidence_payload)='object'),
  evidence_digest text NOT NULL CHECK (
    evidence_digest=encode(sha256(convert_to(evidence_payload::text,'UTF8')),'hex')),
  evaluator_principal text NOT NULL,
  observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(context_id,cause,effect,scope,test_kind,evidence_ref),
  CHECK ((test_kind='negative_control' AND result IN ('passed','failed')) OR
         (test_kind<>'negative_control' AND result IN ('supports','refutes','noise')))
);

CREATE TABLE IF NOT EXISTS aq_control.grounding_promotion (
  promotion_transition_id bigint PRIMARY KEY
    REFERENCES public.causal_principle_transition(id) ON DELETE RESTRICT,
  cause text NOT NULL,
  effect text NOT NULL,
  scope text NOT NULL,
  authorization_context_id uuid NOT NULL
    REFERENCES aq_control.execution_context(context_id) ON DELETE RESTRICT,
  generation_transition_id bigint NOT NULL
    REFERENCES public.causal_principle_transition(id) ON DELETE RESTRICT,
  support_observation_ids uuid[] NOT NULL CHECK (cardinality(support_observation_ids)>=2),
  applicability_observation_id uuid NOT NULL
    REFERENCES aq_control.grounding_observation(observation_id) ON DELETE RESTRICT,
  negative_control_observation_id uuid NOT NULL
    REFERENCES aq_control.grounding_observation(observation_id) ON DELETE RESTRICT,
  review_evidence_ref text NOT NULL CHECK (btrim(review_evidence_ref)<>''),
  promoted_by text NOT NULL,
  promoted_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS aq_control.grounding_promotion_support (
  promotion_transition_id bigint NOT NULL
    REFERENCES aq_control.grounding_promotion(promotion_transition_id) ON DELETE RESTRICT,
  observation_id uuid NOT NULL
    REFERENCES aq_control.grounding_observation(observation_id) ON DELETE RESTRICT,
  PRIMARY KEY(promotion_transition_id,observation_id)
);

DROP TRIGGER IF EXISTS execution_context_append_only ON aq_control.execution_context;
CREATE TRIGGER execution_context_append_only BEFORE UPDATE OR DELETE ON aq_control.execution_context
  FOR EACH ROW EXECUTE FUNCTION aq_control.reject_immutable_event_mutation();
DROP TRIGGER IF EXISTS execution_context_no_truncate ON aq_control.execution_context;
CREATE TRIGGER execution_context_no_truncate BEFORE TRUNCATE ON aq_control.execution_context
  FOR EACH STATEMENT EXECUTE FUNCTION aq_control.reject_immutable_event_mutation();
DROP TRIGGER IF EXISTS grounding_observation_append_only ON aq_control.grounding_observation;
CREATE TRIGGER grounding_observation_append_only BEFORE UPDATE OR DELETE ON aq_control.grounding_observation
  FOR EACH ROW EXECUTE FUNCTION aq_control.reject_immutable_event_mutation();
DROP TRIGGER IF EXISTS grounding_observation_no_truncate ON aq_control.grounding_observation;
CREATE TRIGGER grounding_observation_no_truncate BEFORE TRUNCATE ON aq_control.grounding_observation
  FOR EACH STATEMENT EXECUTE FUNCTION aq_control.reject_immutable_event_mutation();
DROP TRIGGER IF EXISTS grounding_promotion_append_only ON aq_control.grounding_promotion;
CREATE TRIGGER grounding_promotion_append_only BEFORE UPDATE OR DELETE ON aq_control.grounding_promotion
  FOR EACH ROW EXECUTE FUNCTION aq_control.reject_immutable_event_mutation();
DROP TRIGGER IF EXISTS grounding_promotion_no_truncate ON aq_control.grounding_promotion;
CREATE TRIGGER grounding_promotion_no_truncate BEFORE TRUNCATE ON aq_control.grounding_promotion
  FOR EACH STATEMENT EXECUTE FUNCTION aq_control.reject_immutable_event_mutation();
DROP TRIGGER IF EXISTS grounding_promotion_support_append_only ON aq_control.grounding_promotion_support;
CREATE TRIGGER grounding_promotion_support_append_only BEFORE UPDATE OR DELETE ON aq_control.grounding_promotion_support
  FOR EACH ROW EXECUTE FUNCTION aq_control.reject_immutable_event_mutation();
DROP TRIGGER IF EXISTS grounding_promotion_support_no_truncate ON aq_control.grounding_promotion_support;
CREATE TRIGGER grounding_promotion_support_no_truncate BEFORE TRUNCATE ON aq_control.grounding_promotion_support
  FOR EACH STATEMENT EXECUTE FUNCTION aq_control.reject_immutable_event_mutation();

CREATE OR REPLACE FUNCTION aq_control.register_execution_context(
  p_instance_id text,p_execution_id text,p_domain text,p_mission_id text,p_harness text,
  p_attestation jsonb)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_id uuid; v_existing record; v_fingerprint text; v_attestation_digest text;
BEGIN
  IF p_instance_id !~ '^urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
     OR btrim(coalesce(p_execution_id,''))='' OR btrim(coalesce(p_domain,''))=''
     OR btrim(coalesce(p_mission_id,''))='' OR btrim(coalesce(p_harness,''))='' THEN
    RAISE EXCEPTION 'grounding context requires a UUID namespace and complete identity';
  END IF;
  IF jsonb_typeof(p_attestation)<>'object'
     OR btrim(coalesce(p_attestation->>'source',''))=''
     OR btrim(coalesce(p_attestation->>'receipt',''))='' THEN
    RAISE EXCEPTION 'grounding context requires source and receipt attestation';
  END IF;
  v_fingerprint:=encode(sha256(convert_to(jsonb_build_object(
    'instance_id',p_instance_id,'domain',p_domain,'mission_id',p_mission_id,
    'harness',p_harness)::text,'UTF8')),'hex');
  v_attestation_digest:=encode(sha256(convert_to(p_attestation::text,'UTF8')),'hex');
  SELECT * INTO v_existing FROM aq_control.execution_context
   WHERE instance_id=p_instance_id AND execution_id=p_execution_id;
  IF FOUND AND (v_existing.domain IS DISTINCT FROM p_domain
       OR v_existing.mission_id IS DISTINCT FROM p_mission_id
       OR v_existing.harness IS DISTINCT FROM p_harness
       OR v_existing.attestation IS DISTINCT FROM p_attestation) THEN
    RAISE EXCEPTION 'execution identity is already attested with different content';
  ELSIF FOUND THEN
    RETURN v_existing.context_id;
  END IF;
  SELECT * INTO v_existing FROM aq_control.execution_context
   WHERE attestation_digest=v_attestation_digest;
  IF FOUND THEN
    RAISE EXCEPTION 'execution attestation is already bound to a different context';
  END IF;
  INSERT INTO aq_control.execution_context(
    instance_id,execution_id,domain,mission_id,harness,context_fingerprint,
    attestation,attestation_digest)
  VALUES (p_instance_id,p_execution_id,p_domain,p_mission_id,p_harness,v_fingerprint,
          p_attestation,v_attestation_digest)
  ON CONFLICT(instance_id,execution_id) DO NOTHING RETURNING context_id INTO v_id;
  IF v_id IS NULL THEN
    SELECT * INTO v_existing FROM aq_control.execution_context
     WHERE instance_id=p_instance_id AND execution_id=p_execution_id;
    IF v_existing.domain IS DISTINCT FROM p_domain
       OR v_existing.mission_id IS DISTINCT FROM p_mission_id
       OR v_existing.harness IS DISTINCT FROM p_harness
       OR v_existing.attestation IS DISTINCT FROM p_attestation THEN
      RAISE EXCEPTION 'execution identity is already attested with different content';
    END IF;
    v_id:=v_existing.context_id;
  END IF;
  RETURN v_id;
END $$;

CREATE OR REPLACE FUNCTION aq_control.attest_grounding_observation(
  p_context_id uuid,p_cause text,p_effect text,p_scope text,p_test_kind text,
  p_evidence_ref text,p_expected_direction text,p_observed_delta double precision,
  p_noise_tolerance double precision,p_evidence_payload jsonb)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_context record; v_result text; v_id uuid; v_latest record; v_kind text;
  v_generation bigint; v_existing record;
BEGIN
  SELECT * INTO v_context FROM aq_control.execution_context WHERE context_id=p_context_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'unknown attested execution context'; END IF;
  IF btrim(coalesce(p_cause,''))='' OR btrim(coalesce(p_effect,''))=''
     OR btrim(coalesce(p_evidence_ref,''))='' OR p_test_kind NOT IN
       ('support','applicability','negative_control') THEN
    RAISE EXCEPTION 'grounding observation identity is incomplete';
  END IF;
  BEGIN
    IF (p_scope::jsonb)::text IS DISTINCT FROM p_scope THEN
      RAISE EXCEPTION 'grounding scope must be canonical JSON';
    END IF;
  EXCEPTION WHEN invalid_text_representation THEN
    RAISE EXCEPTION 'grounding scope must be canonical JSON';
  END;
  IF NOT (p_observed_delta>'-Infinity'::double precision
              AND p_observed_delta<'Infinity'::double precision)
     OR NOT (p_noise_tolerance>=0 AND p_noise_tolerance<'Infinity'::double precision) THEN RAISE EXCEPTION 'grounding numeric evidence is invalid'; END IF;
  IF jsonb_typeof(p_evidence_payload)<>'object'
     OR btrim(coalesce(p_evidence_payload->>'source',''))=''
     OR btrim(coalesce(p_evidence_payload->>'receipt',''))='' THEN
    RAISE EXCEPTION 'grounding observation requires source and receipt evidence';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(p_cause||chr(31)||p_effect||chr(31)||p_scope,0));
  SELECT * INTO v_latest FROM public.causal_principle_transition
   WHERE cause=p_cause AND effect=p_effect AND scope=p_scope ORDER BY id DESC LIMIT 1;
  IF NOT FOUND THEN RAISE EXCEPTION 'unregistered mined principle'; END IF;
  SELECT id INTO v_generation FROM public.causal_principle_transition
   WHERE cause=p_cause AND effect=p_effect AND scope=p_scope
     AND transition_kind IN ('mined','demote') ORDER BY id DESC LIMIT 1;
  IF p_test_kind='negative_control' THEN
    v_result:=CASE WHEN abs(p_observed_delta)<=p_noise_tolerance THEN 'passed' ELSE 'failed' END;
  ELSE
    IF p_expected_direction NOT IN ('increase','decrease') THEN
      RAISE EXCEPTION 'grounding observation requires an expected direction';
    END IF;
    v_result:=CASE WHEN abs(p_observed_delta)<=p_noise_tolerance THEN 'noise'
                   WHEN (p_expected_direction='increase' AND p_observed_delta>p_noise_tolerance)
                     OR (p_expected_direction='decrease' AND p_observed_delta< -p_noise_tolerance)
                   THEN 'supports' ELSE 'refutes' END;
  END IF;
  INSERT INTO aq_control.grounding_observation(
    context_id,generation_transition_id,cause,effect,scope,test_kind,evidence_ref,expected_direction,
    observed_delta,noise_tolerance,result,evidence_payload,evidence_digest,evaluator_principal)
  VALUES (p_context_id,v_generation,p_cause,p_effect,p_scope,p_test_kind,p_evidence_ref,
          CASE WHEN p_test_kind='negative_control' THEN NULL ELSE p_expected_direction END,
          p_observed_delta,p_noise_tolerance,v_result,p_evidence_payload,
          encode(sha256(convert_to(p_evidence_payload::text,'UTF8')),'hex'),session_user)
  ON CONFLICT(context_id,cause,effect,scope,test_kind,evidence_ref) DO NOTHING
  RETURNING observation_id INTO v_id;
  IF v_id IS NULL THEN
    SELECT * INTO v_existing FROM aq_control.grounding_observation
     WHERE context_id=p_context_id AND cause=p_cause AND effect=p_effect AND scope=p_scope
       AND test_kind=p_test_kind AND evidence_ref=p_evidence_ref;
    IF v_existing.generation_transition_id IS DISTINCT FROM v_generation
       OR v_existing.expected_direction IS DISTINCT FROM
          (CASE WHEN p_test_kind='negative_control' THEN NULL ELSE p_expected_direction END)
       OR v_existing.observed_delta IS DISTINCT FROM p_observed_delta
       OR v_existing.noise_tolerance IS DISTINCT FROM p_noise_tolerance
       OR v_existing.evidence_payload IS DISTINCT FROM p_evidence_payload THEN
      RAISE EXCEPTION 'grounding observation replay key has different immutable content';
    END IF;
    RETURN v_existing.observation_id;
  END IF;
  IF p_test_kind='support' THEN
    v_kind:=CASE WHEN v_latest.to_status='promoted' THEN 'validation' ELSE 'shadow_test' END;
    INSERT INTO public.causal_principle_transition(
      cause,effect,scope,from_status,to_status,transition_kind,environment_id,
      environment_domain,environment_fingerprint,mission_id,harness,evidence_ref,
      evidence_result,expected_direction,observed_delta,bounded_experiment,
      authority_after,transitioned_by,rule_version,automatic,detail)
    VALUES (p_cause,p_effect,p_scope,v_latest.to_status,v_latest.to_status,v_kind,
      v_context.instance_id||'/'||v_context.execution_id,v_context.domain,
      v_context.context_fingerprint,v_context.mission_id,v_context.harness,
      'grounding-observation:'||v_id,v_result,p_expected_direction,p_observed_delta,
      v_kind='shadow_test',v_latest.to_status='promoted',session_user,
      'aq-governed-principle-v2',false,jsonb_build_object(
        'grounding_observation_id',v_id,'evidence_digest',
        encode(sha256(convert_to(p_evidence_payload::text,'UTF8')),'hex')));
  END IF;
  RETURN v_id;
END $$;

CREATE OR REPLACE FUNCTION aq_control.promote_grounded_principle(
  p_cause text,p_effect text,p_scope text,p_authorization_context_id uuid,p_review_evidence_ref text)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_latest record; v_context record; v_support_ids uuid[]; v_envs bigint;
  v_fingerprints bigint; v_domains bigint; v_app uuid; v_neg uuid; v_how text;
  v_transition bigint; v_miner text; v_generation bigint;
BEGIN
  IF btrim(coalesce(p_review_evidence_ref,''))='' THEN
    RAISE EXCEPTION 'manual promotion requires a review evidence reference';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(p_cause||chr(31)||p_effect||chr(31)||p_scope,0));
  SELECT * INTO v_latest FROM public.causal_principle_transition
   WHERE cause=p_cause AND effect=p_effect AND scope=p_scope ORDER BY id DESC LIMIT 1;
  IF NOT FOUND OR v_latest.to_status NOT IN ('provisional','demoted') THEN
    RAISE EXCEPTION 'principle is not eligible for manual grounded promotion';
  END IF;
  SELECT * INTO v_context FROM aq_control.execution_context
   WHERE context_id=p_authorization_context_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'authorization context is not attested'; END IF;
  SELECT transitioned_by INTO v_miner FROM public.causal_principle_transition
   WHERE cause=p_cause AND effect=p_effect AND scope=p_scope AND transition_kind='mined'
   ORDER BY id LIMIT 1;
  IF v_miner=session_user THEN RAISE EXCEPTION 'promoter must be independent of miner'; END IF;
  SELECT id INTO v_generation FROM public.causal_principle_transition
   WHERE cause=p_cause AND effect=p_effect AND scope=p_scope
     AND transition_kind IN ('mined','demote') ORDER BY id DESC LIMIT 1;
  SELECT array_agg(o.observation_id ORDER BY o.observation_id),count(DISTINCT o.context_id),
         count(DISTINCT c.context_fingerprint),count(DISTINCT c.domain)
    INTO v_support_ids,v_envs,v_fingerprints,v_domains
    FROM aq_control.grounding_observation o
    JOIN aq_control.execution_context c ON c.context_id=o.context_id
   WHERE o.cause=p_cause AND o.effect=p_effect AND o.scope=p_scope
     AND o.test_kind='support' AND o.result='supports' AND o.generation_transition_id=v_generation;
  IF coalesce(v_envs,0)<2 OR coalesce(v_fingerprints,0)<2 OR coalesce(v_domains,0)<2 THEN
    RAISE EXCEPTION 'grounded promotion requires evaluator supports in two independent contexts and domains';
  END IF;
  SELECT observation_id,evidence_payload->>'method' INTO v_app,v_how
    FROM aq_control.grounding_observation
   WHERE context_id=p_authorization_context_id AND cause=p_cause AND effect=p_effect
     AND scope=p_scope AND test_kind='applicability' AND result='supports'
     AND generation_transition_id=v_generation ORDER BY observed_at DESC LIMIT 1;
  IF v_app IS NULL OR btrim(coalesce(v_how,''))='' THEN
    RAISE EXCEPTION 'grounded promotion requires a passing applicability observation with method';
  END IF;
  SELECT observation_id INTO v_neg FROM aq_control.grounding_observation
   WHERE context_id=p_authorization_context_id AND cause=p_cause AND effect=p_effect
     AND scope=p_scope AND test_kind='negative_control' AND result='passed'
     AND generation_transition_id=v_generation ORDER BY observed_at DESC LIMIT 1;
  IF v_neg IS NULL THEN RAISE EXCEPTION 'grounded promotion requires a passing negative control'; END IF;
  INSERT INTO public.causal_principle_transition(
    cause,effect,scope,from_status,to_status,transition_kind,environment_id,
    environment_domain,environment_fingerprint,mission_id,harness,evidence_ref,
    evidence_result,bounded_experiment,authority_after,transitioned_by,adjudicated_by,
    negative_control,negative_control_result,rule_version,automatic,detail)
  VALUES (p_cause,p_effect,p_scope,v_latest.to_status,'promoted','promote',
    v_context.instance_id||'/'||v_context.execution_id,v_context.domain,
    v_context.context_fingerprint,v_context.mission_id,v_context.harness,
    p_review_evidence_ref,'authorized',false,true,session_user,session_user,
    'grounding-observation:'||v_neg,'passed','aq-governed-principle-v2',false,
    jsonb_build_object('applies_here',true,'applies_here_how',v_how,
      'supporting_environment_id_count',v_envs,'supporting_environment_count',v_fingerprints,
      'supporting_domain_count',v_domains,'support_observation_ids',v_support_ids,
      'applicability_observation_id',v_app,'negative_control_observation_id',v_neg))
  RETURNING id INTO v_transition;
  INSERT INTO aq_control.grounding_promotion(
    promotion_transition_id,cause,effect,scope,authorization_context_id,generation_transition_id,
    support_observation_ids,applicability_observation_id,negative_control_observation_id,
    review_evidence_ref,promoted_by)
  VALUES (v_transition,p_cause,p_effect,p_scope,p_authorization_context_id,v_generation,v_support_ids,
          v_app,v_neg,p_review_evidence_ref,session_user);
  INSERT INTO aq_control.grounding_promotion_support(promotion_transition_id,observation_id)
    SELECT v_transition,u.observation_id FROM unnest(v_support_ids) AS u(observation_id);
  RETURN v_transition;
END $$;

-- Production producer/consumer wrappers: the evaluator decides which outbox record to attest;
-- checked owner functions apply the immutable event atomically and replay safely.
CREATE OR REPLACE FUNCTION aq_control.attest_and_stage_acquisition(
  p_acquisition_id bigint,p_run_id bigint,p_proposal_index integer)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_event uuid; v jsonb; v_edge bigint;
BEGIN
  v_event:=aq_control.attest_acquisition_evidence(p_acquisition_id,p_run_id,p_proposal_index);
  SELECT payload INTO v FROM aq_control.evidence_event WHERE event_id=v_event;
  v_edge:=public.stage_flagship_causal_proposal(
    p_run_id,p_acquisition_id,v->>'source_action',v->>'direct_effect',v->>'mission_measure',
    v->>'direction',v->>'mechanism',(v->'scope')::text,(v->>'certainty')::real,v->>'evidence');
  RETURN v_edge;
END $$;

CREATE OR REPLACE FUNCTION aq_control.attest_and_apply_prediction(
  p_prediction_id bigint,p_run_id bigint)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_event uuid; v jsonb; v_edge bigint;
BEGIN
  v_event:=aq_control.attest_prediction_resolution(p_prediction_id,p_run_id);
  SELECT payload INTO v FROM aq_control.evidence_event WHERE event_id=v_event;
  v_edge:=(v->>'edge_id')::bigint;
  PERFORM public.resolve_flagship_causal_edge(
    v_edge,p_run_id,p_prediction_id,(v->>'direction_confirmed')::boolean);
  RETURN v_edge;
END $$;

REVOKE ALL ON aq_control.execution_context,aq_control.grounding_observation,
  aq_control.grounding_promotion,aq_control.grounding_promotion_support FROM PUBLIC;
REVOKE ALL ON FUNCTION aq_control.register_execution_context(text,text,text,text,text,jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION aq_control.attest_grounding_observation(uuid,text,text,text,text,text,text,double precision,double precision,jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION aq_control.promote_grounded_principle(text,text,text,uuid,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION aq_control.attest_and_stage_acquisition(bigint,bigint,integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION aq_control.attest_and_apply_prediction(bigint,bigint) FROM PUBLIC;

COMMIT;
