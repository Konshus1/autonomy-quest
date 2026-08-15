-- Slice 3 (design_authority_envelope.md): the actor may REQUEST to open a gate, never self-grant.
--
-- BOUNDED PROPOSAL, ZERO AUTHORITY. When the loop parks work because the categorical authority
-- envelope (Slice 2) GATED a capability class -- or because the substrate manifest proved a claimed
-- class structurally ABSENT -- it may emit ONE bounded, typed authority-change REQUEST for a
-- human/operator to review. The request is ADDITIVE to the existing park+notify: it does NOT change
-- the gate decision (the work still parks) and it does NOT open the envelope. Opening a gate remains
-- the operator editing instance.yaml's ``authority_envelope:`` (Slice 2) -- the sole open mechanism.
--
-- TWO append-only tables, mirroring the self-correction apply (schema/027):
--
--   * authority_change_request -- the PROPOSAL. Written by the loop (aq_loop) at gate time. A UNIQUE
--     dedupe key (instance_id, capability_class, proposed_action_digest) makes re-parking the same
--     action across cycles a no-op enqueue instead of a per-cycle spam. Append-only: a proposal is a
--     record of what was asked, never rewritten.
--
--   * authority_change_disposition -- the operator's REVIEW RECORD (approved/denied) for a request.
--     This is the DISPOSITION SURFACE the operator/host invokes out-of-band. It is NOT what opens the
--     gate (that is the envelope-config edit); it merely records the human decision. CRITICALLY, the
--     actor's process credential (aq_loop) is granted NOTHING on this table (see schema/999): the
--     self-grant boundary -- "the actor cannot approve its own request" -- is grant-backed, not just
--     convention. INSERT here is granted to aq_governance (a higher-authority host-side principal);
--     the operator may equally use the owner credential. One disposition per request (UNIQUE).
--
-- The effective status of a request is DERIVED (pending until a disposition row exists), never a
-- mutable column the actor could flip. Both tables are append-only at the row level (trigger) AND
-- grant-backed (schema/999 revokes UPDATE/DELETE from aq_loop), closing the "actor edits its own
-- request/approval" class of bug at the DB layer.
BEGIN;

-- The bounded proposal. Emitted by the loop; carries no authority.
CREATE TABLE IF NOT EXISTS public.authority_change_request (
  id bigserial PRIMARY KEY,
  requested_at timestamptz NOT NULL DEFAULT now(),
  -- which instance asked (dedupe scope). One request per (instance, class, action-digest).
  instance_id text NOT NULL CHECK (btrim(instance_id) <> ''),
  -- the capability class the actor is asking the operator to OPEN.
  capability_class text NOT NULL CHECK (btrim(capability_class) <> ''),
  -- sha256 of the proposed action (the gated plan), canonicalised, so a re-park dedupes.
  proposed_action_digest text NOT NULL CHECK (btrim(proposed_action_digest) <> ''),
  -- why the actor needs it (bounded free text).
  rationale text NOT NULL CHECK (btrim(rationale) <> ''),
  -- reversible | irreversible.
  reversibility text NOT NULL CHECK (reversibility IN ('reversible','irreversible')),
  -- how WIDE the request is (which class / surface) and how BOUNDED (finite, not a standing grant).
  requested_scope text NOT NULL CHECK (btrim(requested_scope) <> ''),
  requested_bound text NOT NULL CHECK (btrim(requested_bound) <> ''),
  -- provenance: gated_class (envelope holds it gated) | substrate_mismatch (structurally absent).
  reason_kind text NOT NULL CHECK (reason_kind IN ('gated_class','substrate_mismatch')),
  work_id bigint,
  detail jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(detail) = 'object'),
  policy_version text NOT NULL DEFAULT 'authority-request-v1',
  -- IDEMPOTENCY / DEDUPE: one open request per (instance, class, action-digest). A re-park of the
  -- same gated action re-enqueues with ON CONFLICT DO NOTHING and is suppressed.
  CONSTRAINT authority_change_request_one_per_action_class
    UNIQUE (instance_id, capability_class, proposed_action_digest)
);

CREATE INDEX IF NOT EXISTS authority_change_request_requested_at_idx
  ON public.authority_change_request (requested_at DESC);
CREATE INDEX IF NOT EXISTS authority_change_request_work_idx
  ON public.authority_change_request (work_id);

-- The operator's review record. approved/denied is a DECISION RECORD, not the gate-open (that is the
-- envelope-config edit). One disposition per request; the derived status is pending until one exists.
CREATE TABLE IF NOT EXISTS public.authority_change_disposition (
  id bigserial PRIMARY KEY,
  disposed_at timestamptz NOT NULL DEFAULT now(),
  request_id bigint NOT NULL REFERENCES public.authority_change_request(id),
  disposition text NOT NULL CHECK (disposition IN ('approved','denied')),
  -- who dispositioned it (operator / host authority identity). NEVER the actor's process.
  disposed_by text NOT NULL CHECK (btrim(disposed_by) <> ''),
  note text,
  detail jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(detail) = 'object'),
  -- one disposition per request (a request is approved OR denied once).
  CONSTRAINT authority_change_disposition_one_per_request UNIQUE (request_id)
);

CREATE INDEX IF NOT EXISTS authority_change_disposition_request_idx
  ON public.authority_change_disposition (request_id);

-- Append-only: neither the proposal nor the disposition record is ever rewritten in place. Blocking
-- UPDATE/DELETE outright closes the "actor (or anyone) quietly edits a request or flips an approval"
-- class of bug at the row level, complementing the grant-level boundary in schema/999.
CREATE OR REPLACE FUNCTION public.reject_authority_change_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END $$;

DROP TRIGGER IF EXISTS authority_change_request_append_only ON public.authority_change_request;
CREATE TRIGGER authority_change_request_append_only
  BEFORE UPDATE OR DELETE ON public.authority_change_request
  FOR EACH ROW EXECUTE FUNCTION public.reject_authority_change_mutation();

DROP TRIGGER IF EXISTS authority_change_disposition_append_only ON public.authority_change_disposition;
CREATE TRIGGER authority_change_disposition_append_only
  BEFORE UPDATE OR DELETE ON public.authority_change_disposition
  FOR EACH ROW EXECUTE FUNCTION public.reject_authority_change_mutation();

-- Least-privilege grants. Guarded so the migration also applies on databases without the roles
-- (tests). The DEFINITIVE least-privilege tightening lives in schema/999 (which runs after this and
-- re-applies aq_loop's blanket grant, then revokes): here we grant the minimum each principal needs.
--   * aq_loop  -> INSERT on the REQUEST table (+ its sequence). It EMITS requests. It gets NOTHING
--                 on the DISPOSITION table -- it can never record an approval (self-grant boundary).
--   * aq_governance -> INSERT on the DISPOSITION table (+ sequence): the higher-authority host-side
--                 reviewer that records approved/denied. It already has SELECT ON ALL (schema/999).
DO $grants$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aq_loop') THEN
    EXECUTE 'GRANT INSERT ON public.authority_change_request TO aq_loop';
    EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE public.authority_change_request_id_seq TO aq_loop';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aq_governance') THEN
    EXECUTE 'GRANT INSERT ON public.authority_change_disposition TO aq_governance';
    EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE public.authority_change_disposition_id_seq TO aq_governance';
  END IF;
END $grants$;

COMMIT;
