-- Stage-1 SHADOW telemetry for the self-correction consultant (#4834).
--
-- OBSERVE-ONLY. This append-only log records what the PURE self_correction.consult() WOULD
-- recommend, assembled read-only from the governed causal-principle tables. It is never read
-- back to influence a loop decision — the arbiter that may select/apply a recommendation is
-- STAGE 2 and is deliberately not built here. The only write the shadow ever makes is one row
-- into this table; the loop's decision and every other side effect are identical whether the
-- AQ_SELF_CORRECTION_SHADOW flag is on or off.
BEGIN;

CREATE TABLE IF NOT EXISTS public.self_correction_shadow_log (
  id bigserial PRIMARY KEY,
  observed_at timestamptz NOT NULL DEFAULT now(),
  -- cycle/work context in which the observation was taken (decide-step summary).
  work_context text,
  -- the corrected principle id + its generating rule (null when there was no recent correction).
  correction_item_id text,
  generating_rule_id text,
  result_kind text NOT NULL CHECK (result_kind IN ('recommendation','pass','no_snapshot','error')),
  -- the consultant action when a recommendation was produced (e.g. sibling_audit); null otherwise.
  action text CHECK (action IS NULL OR btrim(action) <> ''),
  rationale text NOT NULL CHECK (btrim(rationale) <> ''),
  -- the sibling principle ids that WOULD be reopened for audit (never actually reopened here).
  reopened_principle_ids jsonb NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(reopened_principle_ids) = 'array'),
  requires_human boolean NOT NULL DEFAULT false,
  detail jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(detail) = 'object'),
  -- an action is present exactly when a recommendation was produced.
  CHECK ((result_kind = 'recommendation') = (action IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS self_correction_shadow_log_observed_at_idx
  ON public.self_correction_shadow_log (observed_at DESC);

-- Append-only: telemetry is evidence of what was observed and must not be rewritten.
CREATE OR REPLACE FUNCTION public.reject_self_correction_shadow_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'self_correction_shadow_log is append-only';
END $$;

DROP TRIGGER IF EXISTS self_correction_shadow_log_append_only ON public.self_correction_shadow_log;
CREATE TRIGGER self_correction_shadow_log_append_only
  BEFORE UPDATE OR DELETE ON public.self_correction_shadow_log
  FOR EACH ROW EXECUTE FUNCTION public.reject_self_correction_shadow_mutation();

-- The loop runs as aq_loop. It writes exactly one append-only row per shadow observation, so it
-- needs INSERT on the table AND USAGE,SELECT on the bigserial's sequence (a table-only INSERT
-- grant fails at runtime with "permission denied for sequence" on nextval). It does NOT get
-- UPDATE/DELETE (append-only), and it reads causal_principle_transition via a grant that predates
-- this migration. Guarded so the migration also applies on databases without the role (tests).
DO $grants$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aq_loop') THEN
    EXECUTE 'GRANT INSERT ON public.self_correction_shadow_log TO aq_loop';
    EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE public.self_correction_shadow_log_id_seq TO aq_loop';
  END IF;
END $grants$;

COMMIT;
