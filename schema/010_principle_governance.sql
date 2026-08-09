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
    evidence_result text NOT NULL CHECK (evidence_result IN ('mined','supports','refutes','noise','authorized')),
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
      OR (transition_kind='demote' AND from_status='promoted' AND to_status='demoted' AND evidence_result='refutes' AND automatic)
    )
);

CREATE OR REPLACE FUNCTION reject_causal_principle_transition_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'causal_principle_transition is append-only';
END $$;
DROP TRIGGER IF EXISTS causal_principle_transition_append_only ON causal_principle_transition;
CREATE TRIGGER causal_principle_transition_append_only
  BEFORE UPDATE OR DELETE ON causal_principle_transition
  FOR EACH ROW EXECUTE FUNCTION reject_causal_principle_transition_mutation();
