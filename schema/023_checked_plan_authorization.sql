-- Scope B M4: checked, durable plan authorization before any run or ACT.
BEGIN;

CREATE TABLE IF NOT EXISTS aq_control.plan_authorization (
  authorization_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  global_plan_id text NOT NULL UNIQUE,
  work_id bigint NOT NULL REFERENCES public.work(id) ON DELETE RESTRICT,
  request_plan jsonb NOT NULL CHECK (jsonb_typeof(request_plan)='object'),
  request_digest text NOT NULL,
  disposition text NOT NULL CHECK (disposition IN ('allow','block','abstain')),
  reason_code text NOT NULL CHECK (reason_code IN ('all_steps_exactly_governed','promoted_direction_conflict','no_applicable_promoted_governor')),
  reason text NOT NULL CHECK (btrim(reason)<>''),
  may_act boolean NOT NULL,
  selected boolean NOT NULL,
  governed boolean NOT NULL,
  governor_transition_ids bigint[] NOT NULL DEFAULT '{}'::bigint[],
  policy_version text NOT NULL DEFAULT 'aq-plan-governance-v1',
  authorized_by text NOT NULL,
  authorized_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CHECK (global_plan_id ~ '^urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/plan/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'),
  CHECK (request_digest=encode(sha256(convert_to(request_plan::text,'UTF8')),'hex')),
  CHECK ((disposition='allow' AND may_act AND selected AND governed AND cardinality(governor_transition_ids)>0)
      OR (disposition='block' AND NOT may_act AND NOT selected AND NOT governed AND cardinality(governor_transition_ids)>0)
      OR (disposition='abstain' AND may_act AND NOT selected AND NOT governed))
);

ALTER TABLE public.runs ADD COLUMN IF NOT EXISTS plan_authorization_id uuid
  REFERENCES aq_control.plan_authorization(authorization_id) ON DELETE RESTRICT;

CREATE TABLE IF NOT EXISTS public.plan_governance_defer_outbox (
  defer_id bigserial PRIMARY KEY,
  global_plan_id text NOT NULL UNIQUE,
  work_id bigint NOT NULL REFERENCES public.work(id) ON DELETE RESTRICT,
  request_digest text NOT NULL CHECK (request_digest ~ '^[0-9a-f]{64}$'),
  reason_code text NOT NULL CHECK (reason_code IN (
    'governance_not_configured','governance_unreachable','governance_unauthorized',
    'governance_http_error','governance_malformed','governance_durability_failure')),
  detail text NOT NULL CHECK (btrim(detail)<>''),
  selected boolean NOT NULL DEFAULT false CHECK (NOT selected),
  governed boolean NOT NULL DEFAULT false CHECK (NOT governed),
  deferred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CHECK (global_plan_id ~ '^urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/plan/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
);

DROP TRIGGER IF EXISTS plan_authorization_append_only ON aq_control.plan_authorization;
CREATE TRIGGER plan_authorization_append_only BEFORE UPDATE OR DELETE ON aq_control.plan_authorization
  FOR EACH ROW EXECUTE FUNCTION aq_control.reject_immutable_event_mutation();
DROP TRIGGER IF EXISTS plan_authorization_no_truncate ON aq_control.plan_authorization;
CREATE TRIGGER plan_authorization_no_truncate BEFORE TRUNCATE ON aq_control.plan_authorization
  FOR EACH STATEMENT EXECUTE FUNCTION aq_control.reject_immutable_event_mutation();
DROP TRIGGER IF EXISTS plan_governance_defer_append_only ON public.plan_governance_defer_outbox;
CREATE TRIGGER plan_governance_defer_append_only BEFORE UPDATE OR DELETE ON public.plan_governance_defer_outbox
  FOR EACH ROW EXECUTE FUNCTION aq_control.reject_immutable_event_mutation();
DROP TRIGGER IF EXISTS plan_governance_defer_no_truncate ON public.plan_governance_defer_outbox;
CREATE TRIGGER plan_governance_defer_no_truncate BEFORE TRUNCATE ON public.plan_governance_defer_outbox
  FOR EACH STATEMENT EXECUTE FUNCTION aq_control.reject_immutable_event_mutation();

CREATE OR REPLACE FUNCTION aq_control.authorize_plan(
  p_global_plan_id text, p_work_id bigint, p_plan jsonb)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $authorize$
DECLARE
  v_digest text;
  v_stored_plan jsonb;
  v_stored_plan_id text;
  v_existing aq_control.plan_authorization%ROWTYPE;
  v_step jsonb;
  v_step_id text;
  v_action text;
  v_effect text;
  v_direction text;
  v_scope text;
  v_transition_id bigint;
  v_relation_direction text;
  v_governors bigint[] := '{}'::bigint[];
  v_uncovered integer := 0;
  v_disposition text;
  v_reason_code text;
  v_reason text;
  v_id uuid;
BEGIN
  IF p_global_plan_id IS NULL OR p_global_plan_id !~ '^urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/plan/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' THEN
    RAISE EXCEPTION 'global plan identity must be instance-qualified';
  END IF;
  IF p_plan IS NULL OR jsonb_typeof(p_plan)<>'object' OR jsonb_typeof(p_plan->'steps')<>'array'
     OR jsonb_array_length(p_plan->'steps')=0 THEN
    RAISE EXCEPTION 'complete plan steps are required';
  END IF;
  SELECT plan,plan_id INTO v_stored_plan,v_stored_plan_id FROM public.work WHERE id=p_work_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'unknown work id %',p_work_id; END IF;
  IF v_stored_plan IS NULL OR v_stored_plan<>p_plan OR v_stored_plan_id IS DISTINCT FROM p_global_plan_id THEN
    RAISE EXCEPTION 'authorization request differs from durable work plan identity or content';
  END IF;
  v_digest := encode(sha256(convert_to(p_plan::text,'UTF8')),'hex');
  PERFORM pg_advisory_xact_lock(hashtextextended(p_global_plan_id,0));
  SELECT * INTO v_existing FROM aq_control.plan_authorization WHERE global_plan_id=p_global_plan_id;
  IF FOUND THEN
    IF v_existing.work_id<>p_work_id OR v_existing.request_digest<>v_digest THEN
      RAISE EXCEPTION 'global plan identity replay changed content';
    END IF;
    RETURN jsonb_build_object(
      'authorization_id',v_existing.authorization_id,'global_plan_id',v_existing.global_plan_id,
      'request_digest',v_existing.request_digest,'disposition',v_existing.disposition,
      'reason_code',v_existing.reason_code,'reason',v_existing.reason,'policy_version',v_existing.policy_version,'may_act',v_existing.may_act,'selected',v_existing.selected,
      'governed',v_existing.governed,'governor_transition_ids',to_jsonb(v_existing.governor_transition_ids));
  END IF;

  FOR v_step IN SELECT value FROM jsonb_array_elements(p_plan->'steps') LOOP
    v_step_id := btrim(coalesce(v_step->>'step_id',''));
    v_action := btrim(coalesce(v_step->>'action',''));
    v_effect := btrim(coalesce(v_step->>'expected_effect',''));
    v_direction := v_step->>'expected_direction';
    v_scope := coalesce(v_step->'scope','{}'::jsonb)::text;
    IF v_step_id='' OR v_action='' OR v_effect='' OR v_direction NOT IN ('toward','away','neutral') THEN
      RAISE EXCEPTION 'malformed plan step';
    END IF;
    SELECT t.id,e.relation_direction INTO v_transition_id,v_relation_direction
      FROM public.causal_edge e
      JOIN LATERAL (
        SELECT x.id,x.to_status FROM public.causal_principle_transition x
        WHERE x.cause=e.source_action AND x.effect=e.direct_effect AND x.scope=e.scope_conditions
        ORDER BY x.id DESC LIMIT 1
      ) t ON t.to_status='promoted'
      WHERE e.source_action=v_action AND e.direct_effect=v_effect AND e.scope_conditions=v_scope
      ORDER BY t.id DESC LIMIT 1;
    IF NOT FOUND THEN
      v_uncovered := v_uncovered+1;
    ELSE
      v_governors := array_append(v_governors,v_transition_id);
      IF v_relation_direction<>v_direction THEN
        v_disposition := 'block';
        v_reason_code := 'promoted_direction_conflict';
        v_reason := format('promoted_direction_conflict:step=%s:transition=%s',v_step_id,v_transition_id);
        EXIT;
      END IF;
    END IF;
  END LOOP;
  IF v_disposition IS NULL AND v_uncovered>0 THEN
    v_disposition := 'abstain';
    v_reason_code := 'no_applicable_promoted_governor';
    v_reason := format('no_applicable_promoted_governor:uncovered_steps=%s',v_uncovered);
  ELSIF v_disposition IS NULL THEN
    v_disposition := 'allow';
    v_reason_code := 'all_steps_exactly_governed';
    v_reason := 'all_steps_exactly_governed';
  END IF;
  INSERT INTO aq_control.plan_authorization(
    global_plan_id,work_id,request_plan,request_digest,disposition,reason_code,reason,may_act,selected,governed,
    governor_transition_ids,authorized_by)
  VALUES (p_global_plan_id,p_work_id,p_plan,v_digest,v_disposition,v_reason_code,v_reason,
    v_disposition<>'block',v_disposition='allow',v_disposition='allow',v_governors,session_user)
  RETURNING authorization_id INTO v_id;
  RETURN jsonb_build_object(
    'authorization_id',v_id,'global_plan_id',p_global_plan_id,'request_digest',v_digest,
    'disposition',v_disposition,'reason_code',v_reason_code,'reason',v_reason,'policy_version','aq-plan-governance-v1','may_act',v_disposition<>'block',
    'selected',v_disposition='allow','governed',v_disposition='allow',
    'governor_transition_ids',to_jsonb(v_governors));
END
$authorize$;

REVOKE ALL ON aq_control.plan_authorization FROM PUBLIC;
REVOKE ALL ON public.plan_governance_defer_outbox FROM PUBLIC;
REVOKE ALL ON FUNCTION aq_control.authorize_plan(text,bigint,jsonb) FROM PUBLIC;

DO $roles$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='aq_control_owner') THEN
    ALTER TABLE aq_control.plan_authorization OWNER TO aq_control_owner;
    ALTER TABLE public.plan_governance_defer_outbox OWNER TO aq_control_owner;
    ALTER SEQUENCE public.plan_governance_defer_outbox_defer_id_seq OWNER TO aq_control_owner;
    ALTER FUNCTION aq_control.authorize_plan(text,bigint,jsonb) OWNER TO aq_control_owner;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='aq_governance') THEN
    GRANT USAGE ON SCHEMA aq_control TO aq_governance;
    GRANT EXECUTE ON FUNCTION aq_control.authorize_plan(text,bigint,jsonb) TO aq_governance;
    REVOKE ALL ON aq_control.plan_authorization FROM aq_governance;
    REVOKE INSERT,UPDATE,DELETE ON public.causal_principle_plan_usage,public.causal_principle_plan_outcome FROM aq_governance;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='aq_loop') THEN
    GRANT INSERT,SELECT ON public.plan_governance_defer_outbox TO aq_loop;
    GRANT USAGE,SELECT ON SEQUENCE public.plan_governance_defer_outbox_defer_id_seq TO aq_loop;
    REVOKE UPDATE,DELETE,TRUNCATE ON public.plan_governance_defer_outbox FROM aq_loop;
    REVOKE ALL ON aq_control.plan_authorization FROM aq_loop;
  END IF;
END $roles$;

COMMIT;
