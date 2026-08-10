-- Scope B M5: checked provisional mining and evaluator-owned automatic withdrawal.
BEGIN;

CREATE TABLE IF NOT EXISTS aq_control.grounding_demotion (
  demotion_transition_id bigint PRIMARY KEY REFERENCES public.causal_principle_transition(id) ON DELETE RESTRICT,
  promotion_transition_id bigint NOT NULL REFERENCES public.causal_principle_transition(id) ON DELETE RESTRICT,
  observation_id uuid NOT NULL UNIQUE REFERENCES aq_control.grounding_observation(observation_id) ON DELETE RESTRICT,
  generation_transition_id bigint NOT NULL REFERENCES public.causal_principle_transition(id) ON DELETE RESTRICT,
  reason text NOT NULL CHECK (btrim(reason)<>''),
  demoted_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS aq_control.plan_authorization_governor (
  authorization_id uuid NOT NULL REFERENCES aq_control.plan_authorization(authorization_id) ON DELETE RESTRICT,
  step_id text NOT NULL CHECK (btrim(step_id)<>''),
  promotion_transition_id bigint NOT NULL REFERENCES public.causal_principle_transition(id) ON DELETE RESTRICT,
  cause text NOT NULL, effect text NOT NULL, scope text NOT NULL,
  PRIMARY KEY(authorization_id,step_id),
  UNIQUE(authorization_id,promotion_transition_id,step_id)
);

CREATE TABLE IF NOT EXISTS public.plan_outcome_evaluation_outbox (
  run_id bigint PRIMARY KEY REFERENCES public.runs(id) ON DELETE RESTRICT,
  authorization_id uuid NOT NULL REFERENCES aq_control.plan_authorization(authorization_id) ON DELETE RESTRICT,
  queued_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS aq_control.plan_authorization_outcome (
  outcome_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  authorization_id uuid NOT NULL REFERENCES aq_control.plan_authorization(authorization_id) ON DELETE RESTRICT,
  run_id bigint NOT NULL REFERENCES public.runs(id) ON DELETE RESTRICT,
  promotion_transition_id bigint NOT NULL REFERENCES public.causal_principle_transition(id) ON DELETE RESTRICT,
  goal_reached boolean NOT NULL,
  plan_evaluation_run_id bigint NOT NULL REFERENCES public.plan_evaluation(run_id) ON DELETE RESTRICT,
  evaluator_principal text NOT NULL,
  observed_at timestamptz NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(authorization_id,promotion_transition_id),
  UNIQUE(run_id,promotion_transition_id)
);

CREATE TABLE IF NOT EXISTS aq_control.plan_outcome_application (
  run_id bigint PRIMARY KEY REFERENCES public.runs(id) ON DELETE RESTRICT,
  authorization_id uuid NOT NULL REFERENCES aq_control.plan_authorization(authorization_id) ON DELETE RESTRICT,
  result jsonb NOT NULL CHECK (jsonb_typeof(result)='object'),
  evaluator_principal text NOT NULL,
  applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS aq_control.plan_outcome_observation (
  observation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id bigint NOT NULL UNIQUE REFERENCES public.runs(id) ON DELETE RESTRICT,
  authorization_id uuid NOT NULL REFERENCES aq_control.plan_authorization(authorization_id) ON DELETE RESTRICT,
  eligible boolean NOT NULL,
  goal_reached boolean NOT NULL,
  observed_at timestamptz NOT NULL,
  evidence_payload jsonb NOT NULL CHECK (jsonb_typeof(evidence_payload)='object'),
  evidence_digest text NOT NULL CHECK (
    evidence_digest=encode(sha256(convert_to(evidence_payload::text,'UTF8')),'hex')),
  evaluator_principal text NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE OR REPLACE FUNCTION aq_control.populate_plan_authorization_governors()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_step jsonb; v_index integer:=0; v_transition bigint; v_identity record;
BEGIN
  IF NEW.disposition<>'allow' THEN RETURN NEW; END IF;
  FOR v_step IN SELECT value FROM jsonb_array_elements(NEW.request_plan->'steps') LOOP
    v_index:=v_index+1;
    v_transition:=NEW.governor_transition_ids[v_index];
    IF v_transition IS NULL THEN RAISE EXCEPTION 'allow receipt lacks per-step governor'; END IF;
    SELECT cause,effect,scope INTO v_identity FROM public.causal_principle_transition
      WHERE id=v_transition AND transition_kind='promote' AND to_status='promoted';
    IF NOT FOUND OR v_identity.cause IS DISTINCT FROM v_step->>'action'
       OR v_identity.effect IS DISTINCT FROM v_step->>'expected_effect'
       OR v_identity.scope IS DISTINCT FROM coalesce(v_step->'scope','{}'::jsonb)::text THEN
      RAISE EXCEPTION 'allow receipt governor does not match exact plan step';
    END IF;
    INSERT INTO aq_control.plan_authorization_governor(
      authorization_id,step_id,promotion_transition_id,cause,effect,scope)
    VALUES(NEW.authorization_id,v_step->>'step_id',v_transition,
           v_identity.cause,v_identity.effect,v_identity.scope);
  END LOOP;
  IF v_index<>cardinality(NEW.governor_transition_ids) THEN
    RAISE EXCEPTION 'allow receipt governor cardinality differs from complete plan';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS plan_authorization_governors ON aq_control.plan_authorization;
CREATE TRIGGER plan_authorization_governors AFTER INSERT ON aq_control.plan_authorization
  FOR EACH ROW EXECUTE FUNCTION aq_control.populate_plan_authorization_governors();

-- Safe backfill for M4 allow receipts. Arrays were persisted in plan-step order.
INSERT INTO aq_control.plan_authorization_governor(
  authorization_id,step_id,promotion_transition_id,cause,effect,scope)
SELECT a.authorization_id,step.value->>'step_id',t.id,t.cause,t.effect,t.scope
FROM aq_control.plan_authorization a
CROSS JOIN LATERAL jsonb_array_elements(a.request_plan->'steps') WITH ORDINALITY step(value,n)
JOIN public.causal_principle_transition t ON t.id=a.governor_transition_ids[step.n]
WHERE a.disposition='allow'
ON CONFLICT DO NOTHING;

CREATE OR REPLACE FUNCTION aq_control.fill_plan_outcome_outbox_authorization()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  SELECT r.plan_authorization_id INTO NEW.authorization_id
    FROM public.runs r
    JOIN aq_control.plan_authorization a ON a.authorization_id=r.plan_authorization_id
    JOIN public.work w ON w.id=r.work_id
   WHERE r.id=NEW.run_id AND r.completed_at IS NOT NULL
     AND r.work_id=a.work_id AND w.plan_id=a.global_plan_id AND w.plan=a.request_plan;
  IF NEW.authorization_id IS NULL THEN
    RAISE EXCEPTION 'outcome event requires a completed run, checked authorization, and deterministic plan evaluation';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS plan_outcome_outbox_bind ON public.plan_outcome_evaluation_outbox;
CREATE TRIGGER plan_outcome_outbox_bind BEFORE INSERT ON public.plan_outcome_evaluation_outbox
  FOR EACH ROW EXECUTE FUNCTION aq_control.fill_plan_outcome_outbox_authorization();

CREATE OR REPLACE FUNCTION aq_control.require_checked_run_outcome_event()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF NEW.completed_at IS NOT NULL AND NEW.plan_authorization_id IS NOT NULL
     AND NOT EXISTS(SELECT 1 FROM public.plan_outcome_evaluation_outbox o WHERE o.run_id=NEW.id) THEN
    RAISE EXCEPTION 'completed checked run requires a durable evaluator outcome event';
  END IF;
  RETURN NULL;
END $$;
DROP TRIGGER IF EXISTS checked_run_outcome_event ON public.runs;
CREATE CONSTRAINT TRIGGER checked_run_outcome_event
  AFTER INSERT OR UPDATE OF completed_at,plan_authorization_id ON public.runs
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
  EXECUTE FUNCTION aq_control.require_checked_run_outcome_event();

CREATE OR REPLACE FUNCTION aq_control.enqueue_checked_run_outcome_event()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
  IF NEW.completed_at IS NOT NULL AND NEW.plan_authorization_id IS NOT NULL THEN
    INSERT INTO public.plan_outcome_evaluation_outbox(run_id) VALUES(NEW.id)
      ON CONFLICT(run_id) DO NOTHING;
  END IF;
  RETURN NULL;
END $$;
DROP TRIGGER IF EXISTS checked_run_enqueue_outcome_event ON public.runs;
CREATE TRIGGER checked_run_enqueue_outcome_event
  AFTER INSERT OR UPDATE OF completed_at,plan_authorization_id ON public.runs
  FOR EACH ROW EXECUTE FUNCTION aq_control.enqueue_checked_run_outcome_event();

-- Upgrade backfill: exact M4 checked completions predate the automatic trigger.
INSERT INTO public.plan_outcome_evaluation_outbox(run_id)
SELECT r.id FROM public.runs r
JOIN aq_control.plan_authorization a ON a.authorization_id=r.plan_authorization_id
JOIN public.work w ON w.id=r.work_id
WHERE r.completed_at IS NOT NULL AND r.work_id=a.work_id
  AND w.plan_id=a.global_plan_id AND w.plan=a.request_plan
ON CONFLICT(run_id) DO NOTHING;

CREATE OR REPLACE FUNCTION aq_control.register_mined_principle(
  p_cause text,p_effect text,p_scope text,p_run_ids bigint[],p_learning_ids bigint[])
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_edge jsonb; v_index integer; v_run record; v_conf double precision:=0;
  v_insight text; v_direction text; v_measure text; v_edge_id bigint; v_transition bigint;
  v_existing record;
BEGIN
  IF btrim(coalesce(p_cause,''))='' OR p_effect NOT IN ('measure_up','measure_down')
     OR cardinality(p_run_ids)<1 OR cardinality(p_run_ids)<>cardinality(p_learning_ids)
     OR cardinality(p_run_ids)<>cardinality(ARRAY(SELECT DISTINCT x FROM unnest(p_run_ids) x)) THEN
    RAISE EXCEPTION 'mined principle requires distinct run/learning provenance';
  END IF;
  BEGIN
    IF (p_scope::jsonb)::text IS DISTINCT FROM p_scope THEN RAISE EXCEPTION 'mined scope must be canonical JSON'; END IF;
  EXCEPTION WHEN invalid_text_representation THEN RAISE EXCEPTION 'mined scope must be canonical JSON'; END;
  SELECT edge INTO v_edge FROM public.ralph_causal_edges
    WHERE cause=p_cause AND effect=p_effect AND scope=p_scope;
  IF NOT FOUND OR coalesce(v_edge->>'mined','false')<>'true' THEN
    RAISE EXCEPTION 'checked mining requires the exact provisional store candidate';
  END IF;
  FOR v_index IN 1..cardinality(p_run_ids) LOOP
    SELECT r.id,r.work_id,r.completed_at,r.succeeded,r.productive,r.measure_before,r.measure_after,
           w.kind,w.plan,l.id learning_id,l.insight,l.confidence
      INTO v_run
      FROM public.runs r JOIN public.work w ON w.id=r.work_id
      JOIN public.learnings l ON l.run_id=r.id
     WHERE r.id=p_run_ids[v_index] AND l.id=p_learning_ids[v_index];
    IF NOT FOUND OR v_run.completed_at IS NULL OR NOT v_run.succeeded
       OR v_run.measure_before IS NULL OR v_run.measure_after IS NULL
       OR v_run.measure_before=v_run.measure_after OR v_run.kind<>p_cause
       OR p_effect<>(CASE WHEN v_run.measure_after>v_run.measure_before THEN 'measure_up' ELSE 'measure_down' END)
       OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(coalesce(v_edge->'provenance','[]'::jsonb)) p
          WHERE (p->>'run_id')::bigint=v_run.id AND (p->>'work_id')::bigint=v_run.work_id
            AND (p->>'learning_id')::bigint=v_run.learning_id) THEN
      RAISE EXCEPTION 'mined provenance is not a completed measure-moving run and learning';
    END IF;
    v_conf:=greatest(v_conf,least(coalesce(v_run.confidence,0),.34));
    IF v_insight IS NULL THEN v_insight:=v_run.insight; END IF;
    IF v_measure IS NULL THEN v_measure:=coalesce(v_run.plan->'goal_predicate'->>'metric','mission_measure'); END IF;
  END LOOP;
  v_direction:=CASE WHEN p_effect='measure_up' THEN 'toward' ELSE 'away' END;
  PERFORM pg_advisory_xact_lock(hashtextextended(p_cause||chr(31)||p_effect||chr(31)||p_scope,0));
  SELECT edge_id,relation_direction,evidence_run_ids INTO v_existing FROM public.causal_edge
    WHERE source_action=p_cause AND direct_effect=p_effect AND scope_conditions=p_scope;
  IF FOUND THEN
    IF v_existing.relation_direction<>v_direction THEN RAISE EXCEPTION 'existing mined edge semantic collision'; END IF;
    SELECT id INTO v_transition FROM public.causal_principle_transition
      WHERE cause=p_cause AND effect=p_effect AND scope=p_scope ORDER BY id LIMIT 1;
    IF v_transition IS NULL THEN RAISE EXCEPTION 'existing edge lacks lifecycle registration'; END IF;
    RETURN v_transition;
  END IF;
  INSERT INTO public.causal_edge(source_action,direct_effect,mission_measure,relation_direction,
    mechanism_description,scope_conditions,predicted_certainty,evidence_run_ids)
  VALUES(p_cause,p_effect,v_measure,v_direction,v_insight,p_scope,v_conf,p_run_ids)
  RETURNING edge_id INTO v_edge_id;
  SELECT id INTO v_transition FROM public.causal_principle_transition
    WHERE cause=p_cause AND effect=p_effect AND scope=p_scope ORDER BY id LIMIT 1;
  IF v_transition IS NULL THEN RAISE EXCEPTION 'checked mined edge did not register provisionally'; END IF;
  RETURN v_transition;
END $$;

CREATE OR REPLACE FUNCTION aq_control.attest_and_apply_grounding_observation(
  p_context_id uuid,p_cause text,p_effect text,p_scope text,p_test_kind text,
  p_evidence_ref text,p_expected_direction text,p_observed_delta double precision,
  p_noise_tolerance double precision,p_evidence_payload jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_observation uuid; v_obs record; v_context record; v_latest record;
  v_promotion bigint; v_demotion bigint; v_existing bigint; v_generation bigint;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended(p_cause||chr(31)||p_effect||chr(31)||p_scope,0));
  SELECT * INTO v_obs FROM aq_control.grounding_observation
    WHERE context_id=p_context_id AND cause=p_cause AND effect=p_effect AND scope=p_scope
      AND test_kind=p_test_kind AND evidence_ref=p_evidence_ref;
  IF FOUND THEN
    IF v_obs.expected_direction IS DISTINCT FROM
         (CASE WHEN p_test_kind='negative_control' THEN NULL ELSE p_expected_direction END)
       OR v_obs.observed_delta IS DISTINCT FROM p_observed_delta
       OR v_obs.noise_tolerance IS DISTINCT FROM p_noise_tolerance
       OR v_obs.evidence_payload IS DISTINCT FROM p_evidence_payload THEN
      RAISE EXCEPTION 'grounding observation replay key has different immutable content';
    END IF;
    SELECT demotion_transition_id INTO v_existing FROM aq_control.grounding_demotion
      WHERE observation_id=v_obs.observation_id;
    IF FOUND THEN
      SELECT * INTO v_latest FROM public.causal_principle_transition
        WHERE cause=p_cause AND effect=p_effect AND scope=p_scope ORDER BY id DESC LIMIT 1;
      RETURN jsonb_build_object('observation_id',v_obs.observation_id,'result',v_obs.result,
        'automatic_demotion',false,'status',v_latest.to_status,'transition_id',v_existing,
        'historical_demotion',true,'replayed',true);
    END IF;
  END IF;
  v_observation:=aq_control.attest_grounding_observation(
    p_context_id,p_cause,p_effect,p_scope,p_test_kind,p_evidence_ref,p_expected_direction,
    p_observed_delta,p_noise_tolerance,p_evidence_payload);
  SELECT * INTO v_obs FROM aq_control.grounding_observation WHERE observation_id=v_observation;
  SELECT * INTO v_context FROM aq_control.execution_context WHERE context_id=v_obs.context_id;
  SELECT * INTO v_latest FROM public.causal_principle_transition
    WHERE cause=p_cause AND effect=p_effect AND scope=p_scope ORDER BY id DESC LIMIT 1;
  SELECT demotion_transition_id INTO v_existing FROM aq_control.grounding_demotion
    WHERE observation_id=v_observation;
  IF FOUND THEN
    RETURN jsonb_build_object('observation_id',v_observation,'result',v_obs.result,
      'automatic_demotion',false,'status',v_latest.to_status,'transition_id',v_existing,
      'historical_demotion',true,'replayed',true);
  END IF;
  SELECT id INTO v_generation FROM public.causal_principle_transition
    WHERE cause=p_cause AND effect=p_effect AND scope=p_scope
      AND transition_kind IN ('mined','demote') ORDER BY id DESC LIMIT 1;
  IF v_latest.to_status='promoted' AND v_obs.generation_transition_id=v_generation
     AND v_obs.result IN ('refutes','failed') THEN
    SELECT id INTO v_promotion FROM public.causal_principle_transition
      WHERE cause=p_cause AND effect=p_effect AND scope=p_scope AND transition_kind='promote'
      ORDER BY id DESC LIMIT 1;
    INSERT INTO public.causal_principle_transition(
      cause,effect,scope,from_status,to_status,transition_kind,environment_id,environment_domain,
      environment_fingerprint,mission_id,harness,evidence_ref,evidence_result,expected_direction,
      observed_delta,bounded_experiment,authority_after,transitioned_by,rule_version,automatic,detail)
    VALUES(p_cause,p_effect,p_scope,'promoted','demoted','demote',
      v_context.instance_id||'/'||v_context.execution_id,v_context.domain,v_context.context_fingerprint,
      v_context.mission_id,v_context.harness,'grounding-refutation:'||v_observation,'refutes',
      v_obs.expected_direction,v_obs.observed_delta,false,false,'aq-auto-demoter',
      'aq-governed-principle-v2',true,jsonb_build_object('grounding_observation_id',v_observation,
       'reason','independent evaluator refutation')) RETURNING id INTO v_demotion;
    INSERT INTO aq_control.grounding_demotion(
      demotion_transition_id,promotion_transition_id,observation_id,generation_transition_id,reason)
    VALUES(v_demotion,v_promotion,v_observation,v_obs.generation_transition_id,
      'independent evaluator refutation');
    RETURN jsonb_build_object('observation_id',v_observation,'result',v_obs.result,
      'automatic_demotion',true,'status','demoted','transition_id',v_demotion,'replayed',false);
  END IF;
  RETURN jsonb_build_object('observation_id',v_observation,'result',v_obs.result,
    'automatic_demotion',false,'status',v_latest.to_status,'transition_id',v_latest.id,'replayed',false);
END $$;

CREATE OR REPLACE FUNCTION aq_control.attest_and_apply_plan_outcome(
  p_run_id bigint,p_evidence_payload jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_event record; v_obs record; v_governor record; v_latest record; v_existing record;
  v_count bigint; v_successes bigint; v_span double precision; v_demotion bigint;
  v_demotion_ids bigint[]:='{}'::bigint[]; v_status text:='promoted'; v_current_promotion bigint;
  v_result jsonb; v_observed_at timestamptz;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended('plan-outcome:'||p_run_id,0));
  SELECT result INTO v_result FROM aq_control.plan_outcome_application WHERE run_id=p_run_id;
  IF FOUND THEN RETURN v_result||jsonb_build_object('replayed',true); END IF;
  IF jsonb_typeof(p_evidence_payload)<>'object'
     OR btrim(coalesce(p_evidence_payload->>'source',''))=''
     OR btrim(coalesce(p_evidence_payload->>'receipt',''))=''
     OR jsonb_typeof(p_evidence_payload->'eligible')<>'boolean'
     OR jsonb_typeof(p_evidence_payload->'goal_reached')<>'boolean'
     OR btrim(coalesce(p_evidence_payload->>'observed_at',''))='' THEN
    RAISE EXCEPTION 'evaluator plan outcome requires source, receipt, eligibility, goal and time';
  END IF;
  BEGIN v_observed_at:=(p_evidence_payload->>'observed_at')::timestamptz;
  EXCEPTION WHEN invalid_datetime_format THEN RAISE EXCEPTION 'evaluator outcome time is invalid'; END;
  SELECT o.authorization_id INTO v_event FROM public.plan_outcome_evaluation_outbox o
    WHERE o.run_id=p_run_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'unknown plan outcome event'; END IF;
  INSERT INTO aq_control.plan_outcome_observation(
    run_id,authorization_id,eligible,goal_reached,observed_at,evidence_payload,evidence_digest,evaluator_principal)
  VALUES(p_run_id,v_event.authorization_id,(p_evidence_payload->>'eligible')::boolean,
    (p_evidence_payload->>'goal_reached')::boolean,v_observed_at,p_evidence_payload,
    encode(sha256(convert_to(p_evidence_payload::text,'UTF8')),'hex'),session_user)
  ON CONFLICT(run_id) DO NOTHING;
  SELECT * INTO v_obs FROM aq_control.plan_outcome_observation WHERE run_id=p_run_id;
  IF v_obs.authorization_id<>v_event.authorization_id OR v_obs.evidence_payload<>p_evidence_payload THEN
    RAISE EXCEPTION 'evaluator plan outcome replay changed immutable evidence';
  END IF;
  SELECT o.run_id,o.authorization_id,r.completed_at,r.succeeded,a.global_plan_id,a.disposition,
         a.selected,a.governed,p.run_id evaluation_run_id
    INTO v_event
    FROM public.plan_outcome_evaluation_outbox o JOIN public.runs r ON r.id=o.run_id
    JOIN aq_control.plan_authorization a ON a.authorization_id=o.authorization_id
    JOIN public.work w ON w.id=r.work_id
    JOIN public.plan_evaluation p ON p.run_id=r.id
   WHERE o.run_id=p_run_id AND r.work_id=a.work_id AND p.work_id=a.work_id
     AND p.plan_id=a.global_plan_id AND w.plan_id=a.global_plan_id AND w.plan=a.request_plan;
  IF NOT FOUND OR v_event.completed_at IS NULL OR v_event.evaluation_run_id<>p_run_id THEN
    RAISE EXCEPTION 'plan outcome requires exact authorization/work/plan/evaluation binding';
  END IF;
  IF NOT v_obs.eligible THEN
    v_result:=jsonb_build_object('resolved',false,'automatic_demotion',false,
      'status','not_evaluable','reason','independent evaluator has no comparable goal observation','run_id',p_run_id);
    INSERT INTO aq_control.plan_outcome_application(run_id,authorization_id,result,evaluator_principal)
      VALUES(p_run_id,v_event.authorization_id,v_result,session_user);
    RETURN v_result;
  END IF;
  IF v_event.disposition<>'allow' OR NOT v_event.selected OR NOT v_event.governed THEN
    v_result:=jsonb_build_object('resolved',false,'automatic_demotion',false,
      'status','not_governed','reason','authorization did not govern the plan','run_id',p_run_id);
    INSERT INTO aq_control.plan_outcome_application(run_id,authorization_id,result,evaluator_principal)
      VALUES(p_run_id,v_event.authorization_id,v_result,session_user);
    RETURN v_result;
  END IF;
  FOR v_governor IN SELECT * FROM aq_control.plan_authorization_governor
      WHERE authorization_id=v_event.authorization_id ORDER BY promotion_transition_id,step_id LOOP
    PERFORM pg_advisory_xact_lock(hashtextextended(
      v_governor.cause||chr(31)||v_governor.effect||chr(31)||v_governor.scope,0));
    INSERT INTO aq_control.plan_authorization_outcome(
      authorization_id,run_id,promotion_transition_id,goal_reached,plan_evaluation_run_id,
      evaluator_principal,observed_at)
    VALUES(v_event.authorization_id,p_run_id,v_governor.promotion_transition_id,
      v_obs.goal_reached,p_run_id,session_user,v_obs.observed_at)
    ON CONFLICT(authorization_id,promotion_transition_id) DO NOTHING;
    SELECT * INTO v_existing FROM aq_control.plan_authorization_outcome
      WHERE authorization_id=v_event.authorization_id
        AND promotion_transition_id=v_governor.promotion_transition_id;
    IF v_existing.run_id<>p_run_id OR v_existing.goal_reached<>v_obs.goal_reached THEN
      RAISE EXCEPTION 'plan outcome replay changed independently attested content';
    END IF;
    SELECT * INTO v_latest FROM public.causal_principle_transition
      WHERE cause=v_governor.cause AND effect=v_governor.effect AND scope=v_governor.scope
      ORDER BY id DESC LIMIT 1;
    SELECT count(*),count(*) FILTER(WHERE goal_reached),
      extract(epoch FROM (max(observed_at)-min(observed_at)))/86400.0
      INTO v_count,v_successes,v_span
      FROM aq_control.plan_authorization_outcome
      WHERE promotion_transition_id=v_governor.promotion_transition_id
        AND observed_at>=clock_timestamp()-interval '14 days';
    SELECT id INTO v_current_promotion FROM public.causal_principle_transition
      WHERE cause=v_governor.cause AND effect=v_governor.effect AND scope=v_governor.scope
        AND transition_kind='promote' ORDER BY id DESC LIMIT 1;
    IF v_latest.to_status='promoted' AND v_current_promotion=v_governor.promotion_transition_id
       AND v_count>=5 AND v_successes=0 AND coalesce(v_span,0)>=1 THEN
      INSERT INTO public.causal_principle_transition(
        cause,effect,scope,from_status,to_status,transition_kind,environment_id,environment_domain,
        environment_fingerprint,mission_id,harness,evidence_ref,evidence_result,bounded_experiment,
        authority_after,transitioned_by,rule_version,automatic,detail)
      VALUES(v_governor.cause,v_governor.effect,v_governor.scope,'promoted','demoted','demote',
        'plan-outcome:'||p_run_id,'plan-usefulness',v_obs.evidence_digest,
        'governed-plan','independent-plan-evaluator-v1',
        'plan-unproductivity:'||v_governor.promotion_transition_id||':'||p_run_id,
        'unproductive',false,false,'aq-auto-demoter','aq-governed-principle-v2',true,
        jsonb_build_object('reason','five governed plans produced zero independently observed goal-reaching chains',
          'trigger','unproductivity','promotion_transition_id',v_governor.promotion_transition_id,
          'resolved_governed_plans',v_count,'successful_chains',v_successes,
          'span_days',round(v_span::numeric,4),'horizon_days',14)) RETURNING id INTO v_demotion;
      v_demotion_ids:=array_append(v_demotion_ids,v_demotion); v_status:='demoted';
    ELSIF v_latest.to_status='demoted' THEN v_status:='demoted';
    END IF;
  END LOOP;
  v_result:=jsonb_build_object('resolved',true,'automatic_demotion',cardinality(v_demotion_ids)>0,
    'status',v_status,'transition_ids',to_jsonb(v_demotion_ids),'run_id',p_run_id,
    'observation_id',v_obs.observation_id);
  INSERT INTO aq_control.plan_outcome_application(run_id,authorization_id,result,evaluator_principal)
    VALUES(p_run_id,v_event.authorization_id,v_result,session_user);
  RETURN v_result;
END $$;

CREATE OR REPLACE FUNCTION aq_control.pending_plan_outcome_runs(p_limit integer DEFAULT 100)
RETURNS bigint[] LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_runs bigint[];
BEGIN
  IF p_limit<1 OR p_limit>1000 THEN RAISE EXCEPTION 'consumer limit must be between 1 and 1000'; END IF;
  SELECT coalesce(array_agg(run_id ORDER BY run_id),'{}'::bigint[]) INTO v_runs FROM (
    SELECT o.run_id FROM public.plan_outcome_evaluation_outbox o
    WHERE NOT EXISTS(SELECT 1 FROM aq_control.plan_outcome_application a WHERE a.run_id=o.run_id)
    ORDER BY o.run_id LIMIT p_limit) pending;
  RETURN v_runs;
END $$;

CREATE OR REPLACE FUNCTION aq_control.quarantine_plan_outcome(p_run_id bigint,p_reason text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_auth uuid; v_result jsonb;
BEGIN
  IF p_reason<>'invalid_binding' THEN
    RAISE EXCEPTION 'unknown outcome quarantine reason';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('plan-outcome:'||p_run_id,0));
  SELECT result INTO v_result FROM aq_control.plan_outcome_application WHERE run_id=p_run_id;
  IF FOUND THEN RETURN v_result||jsonb_build_object('replayed',true); END IF;
  SELECT authorization_id INTO v_auth FROM public.plan_outcome_evaluation_outbox WHERE run_id=p_run_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'unknown plan outcome event'; END IF;
  v_result:=jsonb_build_object('resolved',false,'automatic_demotion',false,'status','quarantined',
    'reason',p_reason,'run_id',p_run_id);
  INSERT INTO aq_control.plan_outcome_application(run_id,authorization_id,result,evaluator_principal)
    VALUES(p_run_id,v_auth,v_result,session_user);
  RETURN v_result;
END $$;

-- Every new table is immutable, including TRUNCATE.
DROP TRIGGER IF EXISTS grounding_demotion_append_only ON aq_control.grounding_demotion;
CREATE TRIGGER grounding_demotion_append_only BEFORE UPDATE OR DELETE ON aq_control.grounding_demotion
  FOR EACH ROW EXECUTE FUNCTION aq_control.reject_immutable_event_mutation();
DROP TRIGGER IF EXISTS grounding_demotion_no_truncate ON aq_control.grounding_demotion;
CREATE TRIGGER grounding_demotion_no_truncate BEFORE TRUNCATE ON aq_control.grounding_demotion
  FOR EACH STATEMENT EXECUTE FUNCTION aq_control.reject_immutable_event_mutation();
DROP TRIGGER IF EXISTS plan_auth_governor_append_only ON aq_control.plan_authorization_governor;
CREATE TRIGGER plan_auth_governor_append_only BEFORE UPDATE OR DELETE ON aq_control.plan_authorization_governor
  FOR EACH ROW EXECUTE FUNCTION aq_control.reject_immutable_event_mutation();
DROP TRIGGER IF EXISTS plan_auth_governor_no_truncate ON aq_control.plan_authorization_governor;
CREATE TRIGGER plan_auth_governor_no_truncate BEFORE TRUNCATE ON aq_control.plan_authorization_governor
  FOR EACH STATEMENT EXECUTE FUNCTION aq_control.reject_immutable_event_mutation();
DROP TRIGGER IF EXISTS plan_auth_outcome_append_only ON aq_control.plan_authorization_outcome;
CREATE TRIGGER plan_auth_outcome_append_only BEFORE UPDATE OR DELETE ON aq_control.plan_authorization_outcome
  FOR EACH ROW EXECUTE FUNCTION aq_control.reject_immutable_event_mutation();
DROP TRIGGER IF EXISTS plan_auth_outcome_no_truncate ON aq_control.plan_authorization_outcome;
CREATE TRIGGER plan_auth_outcome_no_truncate BEFORE TRUNCATE ON aq_control.plan_authorization_outcome
  FOR EACH STATEMENT EXECUTE FUNCTION aq_control.reject_immutable_event_mutation();
DROP TRIGGER IF EXISTS plan_outcome_application_append_only ON aq_control.plan_outcome_application;
CREATE TRIGGER plan_outcome_application_append_only BEFORE UPDATE OR DELETE ON aq_control.plan_outcome_application
  FOR EACH ROW EXECUTE FUNCTION aq_control.reject_immutable_event_mutation();
DROP TRIGGER IF EXISTS plan_outcome_application_no_truncate ON aq_control.plan_outcome_application;
CREATE TRIGGER plan_outcome_application_no_truncate BEFORE TRUNCATE ON aq_control.plan_outcome_application
  FOR EACH STATEMENT EXECUTE FUNCTION aq_control.reject_immutable_event_mutation();
DROP TRIGGER IF EXISTS plan_outcome_observation_append_only ON aq_control.plan_outcome_observation;
CREATE TRIGGER plan_outcome_observation_append_only BEFORE UPDATE OR DELETE ON aq_control.plan_outcome_observation
  FOR EACH ROW EXECUTE FUNCTION aq_control.reject_immutable_event_mutation();
DROP TRIGGER IF EXISTS plan_outcome_observation_no_truncate ON aq_control.plan_outcome_observation;
CREATE TRIGGER plan_outcome_observation_no_truncate BEFORE TRUNCATE ON aq_control.plan_outcome_observation
  FOR EACH STATEMENT EXECUTE FUNCTION aq_control.reject_immutable_event_mutation();
DROP TRIGGER IF EXISTS plan_outcome_outbox_append_only ON public.plan_outcome_evaluation_outbox;
CREATE TRIGGER plan_outcome_outbox_append_only BEFORE UPDATE OR DELETE ON public.plan_outcome_evaluation_outbox
  FOR EACH ROW EXECUTE FUNCTION aq_control.reject_immutable_event_mutation();
DROP TRIGGER IF EXISTS plan_outcome_outbox_no_truncate ON public.plan_outcome_evaluation_outbox;
CREATE TRIGGER plan_outcome_outbox_no_truncate BEFORE TRUNCATE ON public.plan_outcome_evaluation_outbox
  FOR EACH STATEMENT EXECUTE FUNCTION aq_control.reject_immutable_event_mutation();

REVOKE ALL ON aq_control.grounding_demotion,aq_control.plan_authorization_governor,
  aq_control.plan_authorization_outcome,aq_control.plan_outcome_application,aq_control.plan_outcome_observation FROM PUBLIC;
REVOKE ALL ON public.plan_outcome_evaluation_outbox FROM PUBLIC;
REVOKE ALL ON FUNCTION aq_control.register_mined_principle(text,text,text,bigint[],bigint[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION aq_control.attest_and_apply_grounding_observation(uuid,text,text,text,text,text,text,double precision,double precision,jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION aq_control.attest_and_apply_plan_outcome(bigint,jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION aq_control.pending_plan_outcome_runs(integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION aq_control.quarantine_plan_outcome(bigint,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION aq_control.require_checked_run_outcome_event() FROM PUBLIC;
REVOKE ALL ON FUNCTION aq_control.enqueue_checked_run_outcome_event() FROM PUBLIC;

DO $roles$
BEGIN
  IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='aq_control_owner') THEN
    ALTER TABLE aq_control.grounding_demotion OWNER TO aq_control_owner;
    ALTER TABLE aq_control.plan_authorization_governor OWNER TO aq_control_owner;
    ALTER TABLE aq_control.plan_authorization_outcome OWNER TO aq_control_owner;
    ALTER TABLE aq_control.plan_outcome_application OWNER TO aq_control_owner;
    ALTER TABLE aq_control.plan_outcome_observation OWNER TO aq_control_owner;
    ALTER TABLE public.plan_outcome_evaluation_outbox OWNER TO aq_control_owner;
    ALTER FUNCTION aq_control.populate_plan_authorization_governors() OWNER TO aq_control_owner;
    ALTER FUNCTION aq_control.fill_plan_outcome_outbox_authorization() OWNER TO aq_control_owner;
    ALTER FUNCTION aq_control.require_checked_run_outcome_event() OWNER TO aq_control_owner;
    ALTER FUNCTION aq_control.enqueue_checked_run_outcome_event() OWNER TO aq_control_owner;
    ALTER FUNCTION aq_control.register_mined_principle(text,text,text,bigint[],bigint[]) OWNER TO aq_control_owner;
    ALTER FUNCTION aq_control.attest_and_apply_grounding_observation(uuid,text,text,text,text,text,text,double precision,double precision,jsonb) OWNER TO aq_control_owner;
    ALTER FUNCTION aq_control.attest_and_apply_plan_outcome(bigint,jsonb) OWNER TO aq_control_owner;
    ALTER FUNCTION aq_control.pending_plan_outcome_runs(integer) OWNER TO aq_control_owner;
    ALTER FUNCTION aq_control.quarantine_plan_outcome(bigint,text) OWNER TO aq_control_owner;
  END IF;
  IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='aq_loop') THEN
    GRANT EXECUTE ON FUNCTION aq_control.register_mined_principle(text,text,text,bigint[],bigint[]) TO aq_loop;
    GRANT INSERT,SELECT ON public.plan_outcome_evaluation_outbox TO aq_loop;
    REVOKE UPDATE,DELETE,TRUNCATE ON public.plan_outcome_evaluation_outbox FROM aq_loop;
  END IF;
  IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='aq_evaluator') THEN
    REVOKE EXECUTE ON FUNCTION aq_control.attest_grounding_observation(uuid,text,text,text,text,text,text,double precision,double precision,jsonb) FROM aq_evaluator;
    GRANT EXECUTE ON FUNCTION aq_control.attest_and_apply_grounding_observation(uuid,text,text,text,text,text,text,double precision,double precision,jsonb) TO aq_evaluator;
    GRANT EXECUTE ON FUNCTION aq_control.attest_and_apply_plan_outcome(bigint,jsonb) TO aq_evaluator;
    GRANT EXECUTE ON FUNCTION aq_control.pending_plan_outcome_runs(integer) TO aq_evaluator;
    GRANT EXECUTE ON FUNCTION aq_control.quarantine_plan_outcome(bigint,text) TO aq_evaluator;
  END IF;
END $roles$;
COMMIT;
