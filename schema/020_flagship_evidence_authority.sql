-- C4 hardening: durable evidence events and governance-only causal mutation.
BEGIN;
SET search_path=public,pg_temp;

-- Remove the legacy caller-verdict authority bridge from schema 017, including PUBLIC execute.
DROP FUNCTION IF EXISTS resolve_flagship_causal_edge(bigint,bigint,bigint,boolean);
DROP TABLE IF EXISTS flagship_causal_resolution_event;

CREATE TABLE IF NOT EXISTS flagship_causal_proposal_event (
  proposal_event_id bigserial PRIMARY KEY,
  edge_id bigint NOT NULL REFERENCES causal_edge(edge_id) ON DELETE RESTRICT,
  run_id bigint NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
  acquisition_id bigint NOT NULL REFERENCES plan_acquisition(acquisition_id) ON DELETE RESTRICT,
  work_id bigint NOT NULL REFERENCES work(id) ON DELETE RESTRICT,
  action text NOT NULL, effect text NOT NULL, scope jsonb NOT NULL CHECK(jsonb_typeof(scope)='object'),
  direction text NOT NULL CHECK(direction IN ('toward','away','neutral')),
  evidence text NOT NULL CHECK(btrim(evidence)<>''),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(run_id,acquisition_id,action,effect,scope)
);
ALTER TABLE causal_edge DROP CONSTRAINT IF EXISTS causal_edge_falsified_by_fkey;
ALTER TABLE causal_edge ADD CONSTRAINT causal_edge_falsified_by_fkey
  FOREIGN KEY(falsified_by) REFERENCES runs(id) ON DELETE RESTRICT;

CREATE OR REPLACE FUNCTION stage_flagship_causal_proposal(
  p_run_id bigint, p_acquisition_id bigint, p_action text, p_effect text,
  p_mission_measure text, p_direction text, p_mechanism text, p_scope text,
  p_certainty real, p_evidence text)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE target_action text; target_effect text; target_scope jsonb; plan_scope jsonb; edge bigint; wid bigint;
BEGIN
  SELECT edge_id INTO edge FROM flagship_causal_proposal_event
    WHERE run_id=p_run_id AND acquisition_id=p_acquisition_id AND action=p_action
      AND effect=p_effect AND scope=p_scope::jsonb;
  IF edge IS NOT NULL THEN RETURN edge; END IF;
  IF btrim(coalesce(p_evidence,''))='' THEN RAISE EXCEPTION 'proposal requires persisted evidence'; END IF;
  IF p_direction NOT IN ('toward','away','neutral') THEN RAISE EXCEPTION 'invalid direction'; END IF;
  IF p_certainty IS NULL OR p_certainty='NaN'::real OR p_certainty<0 OR p_certainty>0.99 THEN
    RAISE EXCEPTION 'certainty must be finite and between 0 and .99';
  END IF;
  BEGIN target_scope := p_scope::jsonb; EXCEPTION WHEN others THEN
    RAISE EXCEPTION 'proposal scope must be a JSON object'; END;
  IF jsonb_typeof(target_scope)<>'object' THEN RAISE EXCEPTION 'proposal scope must be a JSON object'; END IF;
  SELECT pa.work_id,step->>'action',step->>'expected_effect',coalesce(step->'scope','{}'::jsonb)
    INTO wid,target_action,target_effect,plan_scope
  FROM plan_acquisition pa JOIN runs r ON r.acquisition_id=pa.acquisition_id AND r.work_id=pa.work_id
  JOIN work w ON w.id=pa.work_id
  CROSS JOIN LATERAL jsonb_array_elements(w.plan->'steps') step
  WHERE pa.acquisition_id=p_acquisition_id AND pa.status IN ('running','completed') AND r.id=p_run_id
    AND r.completed_at IS NOT NULL AND btrim(coalesce(r.outcome,''))<>''
    AND btrim(coalesce(r.evidence,''))<>'' AND step->>'step_id'=pa.target_step_id;
  IF target_action IS NULL OR target_action IS DISTINCT FROM p_action
     OR target_effect IS DISTINCT FROM p_effect OR target_scope IS DISTINCT FROM plan_scope THEN
    RAISE EXCEPTION 'proposal must match its immutable acquisition target and scope';
  END IF;
  INSERT INTO causal_edge(source_action,direct_effect,mission_measure,relation_direction,
    mechanism_description,scope_conditions,predicted_certainty,evidence_run_ids,support_count)
  VALUES (p_action,p_effect,p_mission_measure,p_direction,nullif(p_mechanism,''),target_scope::text,
    p_certainty,ARRAY[p_run_id],0)
  ON CONFLICT (source_action,direct_effect,scope_conditions) DO NOTHING
  RETURNING edge_id INTO edge;
  IF edge IS NULL THEN
    RAISE EXCEPTION 'proposal identity already exists; authoritative edges are immutable here';
  END IF;
  INSERT INTO flagship_causal_proposal_event
    (edge_id,run_id,acquisition_id,work_id,action,effect,scope,direction,evidence)
  VALUES(edge,p_run_id,p_acquisition_id,wid,p_action,p_effect,target_scope,p_direction,p_evidence)
  ON CONFLICT(run_id,acquisition_id,action,effect,scope) DO NOTHING;
  RETURN edge;
END $$;

COMMIT;
