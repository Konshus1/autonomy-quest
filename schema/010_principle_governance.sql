-- Governed lifecycle for AQ-owned causal principles.
-- Status is derived from the latest append-only transition; no mutable authority flag exists.
CREATE TABLE IF NOT EXISTS causal_principle_transition (
    id bigserial PRIMARY KEY,
    cause text NOT NULL CHECK (btrim(cause) <> ''),
    effect text NOT NULL CHECK (btrim(effect) <> ''),
    scope text NOT NULL,
    from_status text CHECK (from_status IN ('provisional','promoted','demoted')),
    to_status text NOT NULL CHECK (to_status IN ('provisional','promoted','demoted')),
    transition_kind text NOT NULL CHECK (transition_kind IN ('mined','shadow_test','validation','promote','demote')),
    environment_id text NOT NULL CHECK (btrim(environment_id) <> ''),
    environment_domain text NOT NULL CHECK (btrim(environment_domain) <> ''),
    environment_fingerprint text NOT NULL CHECK (btrim(environment_fingerprint) <> ''),
    mission_id text NOT NULL CHECK (btrim(mission_id) <> ''),
    harness text NOT NULL CHECK (btrim(harness) <> ''),
    evidence_ref text NOT NULL CHECK (btrim(evidence_ref) <> ''),
    evidence_result text NOT NULL CHECK (evidence_result IN ('mined','supports','refutes','noise','authorized','unproductive')),
    expected_direction text CHECK (expected_direction IN ('increase','decrease')),
    observed_delta double precision,
    bounded_experiment boolean NOT NULL DEFAULT false,
    authority_after boolean NOT NULL,
    transitioned_by text NOT NULL CHECK (btrim(transitioned_by) <> ''),
    adjudicated_by text,
    negative_control text,
    negative_control_result text,
    rule_version text NOT NULL CHECK (btrim(rule_version) <> ''),
    automatic boolean NOT NULL DEFAULT false,
    detail jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (cause,effect,scope,transition_kind,evidence_ref),
    CHECK (authority_after = (to_status = 'promoted')),
    CHECK (
      (transition_kind='mined' AND from_status IS NULL AND to_status='provisional' AND evidence_result='mined' AND NOT automatic)
      OR (transition_kind='shadow_test' AND from_status IN ('provisional','demoted') AND to_status=from_status AND bounded_experiment AND NOT automatic)
      OR (transition_kind='validation' AND from_status='promoted' AND to_status='promoted' AND NOT bounded_experiment AND NOT automatic)
      OR (transition_kind='promote' AND from_status IN ('provisional','demoted') AND to_status='promoted' AND evidence_result='authorized' AND NOT automatic
          AND btrim(coalesce(adjudicated_by,'')) <> '' AND btrim(coalesce(negative_control,'')) <> '' AND btrim(coalesce(negative_control_result,'')) <> '')
      OR (transition_kind='demote' AND from_status='promoted' AND to_status='demoted' AND evidence_result IN ('refutes','unproductive') AND automatic)
    )
);

CREATE TABLE IF NOT EXISTS causal_principle_plan_usage (
  id bigserial PRIMARY KEY,
  cause text NOT NULL CHECK (btrim(cause)<>''), effect text NOT NULL CHECK (btrim(effect)<>''), scope text NOT NULL,
  promotion_transition_id bigint NOT NULL REFERENCES causal_principle_transition(id),
  plan_id text NOT NULL CHECK (btrim(plan_id)<>''), goal_id text NOT NULL CHECK (btrim(goal_id)<>''),
  selected boolean NOT NULL CHECK(selected), governed boolean NOT NULL CHECK(governed),
  environment_id text NOT NULL CHECK (btrim(environment_id)<>''),
  environment_domain text NOT NULL CHECK (btrim(environment_domain)<>''),
  environment_fingerprint text NOT NULL CHECK (btrim(environment_fingerprint)<>''),
  selection_evidence_ref text NOT NULL CHECK (btrim(selection_evidence_ref)<>''),
  selected_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(cause,effect,scope,plan_id)
);
CREATE TABLE IF NOT EXISTS causal_principle_plan_outcome (
  id bigserial PRIMARY KEY,
  usage_id bigint NOT NULL UNIQUE REFERENCES causal_principle_plan_usage(id),
  goal_reached boolean NOT NULL,
  outcome_evidence_ref text NOT NULL CHECK (btrim(outcome_evidence_ref)<>''),
  resolved_at timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION validate_causal_principle_transition_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  latest_status text; miner text; evidence_floor bigint := 0;
  execution_ids bigint; contexts bigint; domains bigint;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended(NEW.cause || chr(31) || NEW.effect || chr(31) || NEW.scope, 0));
  SELECT to_status INTO latest_status FROM causal_principle_transition
    WHERE cause=NEW.cause AND effect=NEW.effect AND scope=NEW.scope ORDER BY id DESC LIMIT 1;
  IF NEW.transition_kind='mined' THEN
    IF latest_status IS NOT NULL THEN RAISE EXCEPTION 'mined transition requires a new principle identity'; END IF;
    RETURN NEW;
  END IF;
  IF latest_status IS NULL OR latest_status IS DISTINCT FROM NEW.from_status THEN
    RAISE EXCEPTION 'transition predecessor mismatch: latest %, supplied %', latest_status, NEW.from_status;
  END IF;
  IF NEW.transition_kind='promote' THEN
    SELECT transitioned_by INTO miner FROM causal_principle_transition
      WHERE cause=NEW.cause AND effect=NEW.effect AND scope=NEW.scope AND transition_kind='mined' ORDER BY id LIMIT 1;
    IF miner IS NULL OR miner=NEW.adjudicated_by THEN RAISE EXCEPTION 'promotion requires an independent adjudicator'; END IF;
    SELECT coalesce(max(id),0) INTO evidence_floor FROM causal_principle_transition
      WHERE cause=NEW.cause AND effect=NEW.effect AND scope=NEW.scope AND transition_kind='demote';
    SELECT count(DISTINCT environment_id), count(DISTINCT environment_fingerprint), count(DISTINCT environment_domain)
      INTO execution_ids,contexts,domains FROM causal_principle_transition
      WHERE cause=NEW.cause AND effect=NEW.effect AND scope=NEW.scope
        AND transition_kind='shadow_test' AND evidence_result='supports' AND id>evidence_floor;
    IF execution_ids<2 OR contexts<2 OR domains<2 THEN RAISE EXCEPTION 'promotion requires two cross-environment supports'; END IF;
    IF coalesce(NEW.detail->>'applies_here','false')<>'true' OR btrim(coalesce(NEW.detail->>'applies_here_how',''))=''
      THEN RAISE EXCEPTION 'promotion requires applies_here provenance'; END IF;
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS causal_principle_transition_validate_insert ON causal_principle_transition;
CREATE TRIGGER causal_principle_transition_validate_insert BEFORE INSERT ON causal_principle_transition
  FOR EACH ROW EXECUTE FUNCTION validate_causal_principle_transition_insert();

CREATE OR REPLACE FUNCTION reject_causal_principle_transition_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'causal_principle_transition is append-only';
END $$;
DROP TRIGGER IF EXISTS causal_principle_transition_append_only ON causal_principle_transition;
CREATE TRIGGER causal_principle_transition_append_only
  BEFORE UPDATE OR DELETE ON causal_principle_transition
  FOR EACH ROW EXECUTE FUNCTION reject_causal_principle_transition_mutation();
DROP TRIGGER IF EXISTS causal_principle_plan_usage_append_only ON causal_principle_plan_usage;
CREATE TRIGGER causal_principle_plan_usage_append_only BEFORE UPDATE OR DELETE ON causal_principle_plan_usage
  FOR EACH ROW EXECUTE FUNCTION reject_causal_principle_transition_mutation();
DROP TRIGGER IF EXISTS causal_principle_plan_outcome_append_only ON causal_principle_plan_outcome;
CREATE TRIGGER causal_principle_plan_outcome_append_only BEFORE UPDATE OR DELETE ON causal_principle_plan_outcome
  FOR EACH ROW EXECUTE FUNCTION reject_causal_principle_transition_mutation();
