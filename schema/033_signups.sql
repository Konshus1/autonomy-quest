-- 033 — early-access agentricity_signups capture for the Agentricity marketing site.
--
-- The public marketing site (site/agentricity/) has ONE conversion goal: an email capture. This is
-- the store behind it. The untrusted public internet writes here (via the site's POST /api/signup
-- handler), so it is shaped for least-privilege and immutability, mirroring schema/032's
-- analytics_events discipline:
--
--   * APPEND-ONLY. A recorded signup is never rewritten or erased from inside the app — enforced by
--     BOTH a trigger and by withholding UPDATE/DELETE grants (defense-in-depth).
--   * ONE dedicated writer credential (agentricity_signups_writer) that is INSERT-ONLY. It cannot even SELECT
--     the list — so a leak of the site process's credential cannot read back everyone's email, only
--     append. The app dedups with ON CONFLICT DO NOTHING, which needs INSERT only (no UPDATE/SELECT).
--   * The write credential lives ONLY in the server env (SIGNUPS_DB_DSN), never in the static page
--     or client JS. The page just POSTs same-origin to /api/signup.
--
-- Guarded + idempotent, mirroring schema/032 + schema/999: the table/trigger are created
-- unconditionally; the GRANT block runs only when the writer role exists (an external/dev install
-- without it skips the grants rather than erroring).

BEGIN;

SET search_path = public, "$user";

-- ---------------------------------------------------------------------------
-- SIGNUPS — append-only early-access list written by the marketing site.
--   email      — the captured address, normalized to lowercase by the app. UNIQUE so a re-submit is
--                a no-op (ON CONFLICT DO NOTHING) instead of a duplicate row.
--   source     — optional free-text provenance the front-end sends (e.g. 'agentricity.com').
--   ip / ua    — optional coarse request metadata (abuse triage only). No PII beyond the email
--                itself is required; these stay nullable and the app may omit them.
-- No UPDATE/DELETE is ever issued against it by any app role; it is an immutable append-only list.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agentricity_signups (
  id          bigserial   PRIMARY KEY,
  email       text        NOT NULL UNIQUE,
  created_at  timestamptz NOT NULL DEFAULT now(),
  source      text,
  ip          text,
  ua          text
);

CREATE INDEX IF NOT EXISTS agentricity_signups_created_idx ON agentricity_signups (created_at);

-- ---------------------------------------------------------------------------
-- Least-privilege grants. Only when the dedicated writer role exists.
--
-- agentricity_signups_writer is the ONLY writer and it is APPEND-ONLY and BLIND: INSERT (+ the sequence it
-- needs for bigserial) and nothing else. It is provisioned out-of-band by the deploy (see
-- site/agentricity/DEPLOY.md) and handed to the site process as SIGNUPS_DB_DSN — never to the page.
--   * INSERT lets it append and lets ON CONFLICT DO NOTHING dedup (DO NOTHING needs no UPDATE).
--   * We REVOKE SELECT so a compromised site credential cannot read back the list.
--   * We REVOKE UPDATE/DELETE/TRUNCATE so a recorded signup can never be altered or erased.
-- Revoke-first (defense-in-depth against a re-appearing blanket grant), then grant the minimum.
-- ---------------------------------------------------------------------------
DO $agentricity_signups_grants$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='agentricity_signups_writer') THEN
    RAISE NOTICE 'agentricity_signups_writer role absent; skipping agentricity_signups grants (external/dev install)';
    RETURN;
  END IF;

  EXECUTE 'REVOKE ALL ON agentricity_signups FROM agentricity_signups_writer';
  EXECUTE 'GRANT INSERT ON agentricity_signups TO agentricity_signups_writer';
  -- USAGE only (lets nextval() serve the bigserial default). NOT SELECT: SELECT on the sequence is
  -- unneeded for INSERT and would let the read-blind writer read last_value, leaking the list SIZE.
  EXECUTE 'REVOKE ALL ON SEQUENCE agentricity_signups_id_seq FROM agentricity_signups_writer';
  EXECUTE 'GRANT USAGE ON SEQUENCE agentricity_signups_id_seq TO agentricity_signups_writer';
  -- Belt-and-suspenders: make the withheld verbs an explicit revoke, so even if a later blanket
  -- GRANT ... ON ALL TABLES hands this role more, the read/mutate boundary is re-asserted here.
  EXECUTE 'REVOKE SELECT, UPDATE, DELETE, TRUNCATE ON agentricity_signups FROM agentricity_signups_writer';

  -- Trusted read-only operator principals may observe the list where they exist (parity with the
  -- rest of the ledger; harmless no-op on the isolated agentricity_signups DB where they don't).
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='aq_governance') THEN
    EXECUTE 'GRANT SELECT ON agentricity_signups TO aq_governance';
    EXECUTE 'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON agentricity_signups FROM aq_governance';
  END IF;
END $agentricity_signups_grants$;

-- Append-only, enforced by trigger as well as by grants (defense-in-depth, parity with 032/026): a
-- future accidental UPDATE/DELETE grant must still be rejected at the row level.
CREATE OR REPLACE FUNCTION public.reject_agentricity_signups_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'agentricity_signups is append-only';
END $$;

DROP TRIGGER IF EXISTS agentricity_signups_append_only ON public.agentricity_signups;
CREATE TRIGGER agentricity_signups_append_only
  BEFORE UPDATE OR DELETE ON public.agentricity_signups
  FOR EACH ROW EXECUTE FUNCTION public.reject_agentricity_signups_mutation();

COMMIT;
