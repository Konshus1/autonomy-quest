-- Stage-2 LIVE apply for the self-correction consultant (#4834).
--
-- BOUNDED + REVERSIBLE. Stage 1 (schema/026) only OBSERVED what the pure consultant would
-- recommend. Stage 2 lets a single ARBITER APPLY at most one bounded, reversible action per cycle
-- behind the separate flag AQ_SELF_CORRECTION_LIVE (default OFF). Nothing here can promote, demote,
-- or authorize a principle: aq_loop still cannot write any principle transition (schema/999), and
-- the two tables below are append-only queues/records, never disposition state.
--
--   * self_correction_audit_request — the sibling_audit apply. One append-only row per SUSPECT
--     same-rule sibling per correction asks the evaluator/governance to RE-EXAMINE that principle.
--     It is a review REQUEST only; it NEVER changes a disposition. A UNIQUE key makes the enqueue
--     idempotent (the same correction twice cannot open a duplicate request for the same sibling).
--     Reversibility: the row is inert — no disposition changes because it exists; the evaluator
--     consumes it later (out of scope) and may record a separate resolution, never mutating any
--     disposition. Ignoring the row entirely leaves every principle exactly as it was.
--
--   * self_correction_arbiter_action — the append-only "records why" trail for every action the
--     arbiter APPLIED this cycle (escalate / hold_dispatch / enqueue_audit). A frame_review HOLD
--     of routine dispatch is bounded to one cycle and recorded here so the hold is auditable; it
--     has no other side effect.
BEGIN;

-- The sibling_audit apply: a bounded, reversible re-examination REQUEST per suspect sibling.
CREATE TABLE IF NOT EXISTS public.self_correction_audit_request (
  id bigserial PRIMARY KEY,
  requested_at timestamptz NOT NULL DEFAULT now(),
  -- the SUSPECT same-rule sibling principle to re-examine (never the corrected item itself).
  principle_id text NOT NULL CHECK (btrim(principle_id) <> ''),
  -- the shared normalized generating rule that defines the sibling equivalence class.
  generating_rule_id text NOT NULL CHECK (btrim(generating_rule_id) <> ''),
  reason text NOT NULL CHECK (btrim(reason) <> ''),
  -- the triggering correction's stable identity (corrected principle id + when it was corrected),
  -- so a LATER re-correction of the same principle yields a fresh, distinct request.
  source_correction text NOT NULL CHECK (btrim(source_correction) <> ''),
  -- readable projections of that identity (not part of the idempotency key on their own).
  source_correction_item_id text,
  source_corrected_at timestamptz,
  work_context text,
  detail jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(detail) = 'object'),
  -- IDEMPOTENCY: one request per suspect sibling per correction. The same correction re-enqueued
  -- (e.g. the loop re-running the same cycle) cannot open a duplicate; the enqueue ON CONFLICT
  -- DO NOTHING suppresses it.
  CONSTRAINT self_correction_audit_request_one_per_suspect_per_correction
    UNIQUE (principle_id, source_correction)
);

CREATE INDEX IF NOT EXISTS self_correction_audit_request_requested_at_idx
  ON public.self_correction_audit_request (requested_at DESC);
CREATE INDEX IF NOT EXISTS self_correction_audit_request_source_idx
  ON public.self_correction_audit_request (source_correction);

-- The append-only "records why" trail for every APPLIED arbiter action.
CREATE TABLE IF NOT EXISTS public.self_correction_arbiter_action (
  id bigserial PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now(),
  work_context text,
  outcome text NOT NULL CHECK (outcome IN ('escalate','hold_dispatch','enqueue_audit')),
  -- the consultant action being applied (frame_review / sibling_audit); null for a bare escalate.
  action text CHECK (action IS NULL OR btrim(action) <> ''),
  -- true exactly when this action held routine mission dispatch for the cycle (frame_review).
  held_dispatch boolean NOT NULL DEFAULT false,
  correction_item_id text,
  generating_rule_id text,
  reason text NOT NULL CHECK (btrim(reason) <> ''),
  audit_request_count integer NOT NULL DEFAULT 0 CHECK (audit_request_count >= 0),
  detail jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(detail) = 'object')
);

CREATE INDEX IF NOT EXISTS self_correction_arbiter_action_applied_at_idx
  ON public.self_correction_arbiter_action (applied_at DESC);

-- Append-only: both tables are evidence/queues of what happened and must not be rewritten in
-- place. Reversibility is achieved WITHOUT mutating a row (an audit request is inert; a hold is
-- bounded to one cycle), so blocking UPDATE/DELETE outright costs nothing and closes a whole class
-- of "the loop quietly edited its own audit trail" bugs.
CREATE OR REPLACE FUNCTION public.reject_self_correction_arbiter_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END $$;

DROP TRIGGER IF EXISTS self_correction_audit_request_append_only ON public.self_correction_audit_request;
CREATE TRIGGER self_correction_audit_request_append_only
  BEFORE UPDATE OR DELETE ON public.self_correction_audit_request
  FOR EACH ROW EXECUTE FUNCTION public.reject_self_correction_arbiter_mutation();

DROP TRIGGER IF EXISTS self_correction_arbiter_action_append_only ON public.self_correction_arbiter_action;
CREATE TRIGGER self_correction_arbiter_action_append_only
  BEFORE UPDATE OR DELETE ON public.self_correction_arbiter_action
  FOR EACH ROW EXECUTE FUNCTION public.reject_self_correction_arbiter_mutation();

-- The loop runs as aq_loop. Like schema/026, it needs INSERT on each table AND USAGE,SELECT on the
-- bigserial sequences (a table-only INSERT grant fails at runtime with "permission denied for
-- sequence" on nextval). It gets NO UPDATE/DELETE (append-only), and — critically — this migration
-- grants it NOTHING on any principle-transition surface: aq_loop still cannot promote/demote/
-- authorize anything (schema/999 revokes all writes on causal_principle_transition from it).
-- Guarded so the migration also applies on databases without the role (tests).
DO $grants$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aq_loop') THEN
    EXECUTE 'GRANT INSERT ON public.self_correction_audit_request TO aq_loop';
    EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE public.self_correction_audit_request_id_seq TO aq_loop';
    EXECUTE 'GRANT INSERT ON public.self_correction_arbiter_action TO aq_loop';
    EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE public.self_correction_arbiter_action_id_seq TO aq_loop';
  END IF;
END $grants$;

COMMIT;
