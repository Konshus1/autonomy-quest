-- 034 — Stripe purchase ledger for the Agentricity marketing site.
--
-- The site sells two SKUs (one-time "Own-it" + monthly "Stay-current") via Stripe PAYMENT LINKS. It
-- deliberately does NOT hold STRIPE_SECRET_KEY; the only Stripe credential on this public container is
-- the webhook SIGNING secret. Stripe POSTs verified events to /api/stripe/webhook and this table is the
-- durable record behind them. Shaped exactly like schema/033_signups.sql (analytics/032 discipline):
--
--   * APPEND-ONLY EVENT LEDGER. One row per verified Stripe event; never rewritten or erased from the
--     app — enforced by BOTH a trigger and withheld UPDATE/DELETE grants. Current entitlement is
--     DERIVED by reading the ledger (a one-time purchase, a later charge.refunded, a subscription
--     deleted — all are separate appended rows), not by mutating a state row.
--   * ONE dedicated writer credential (aq_purchases_writer) that is INSERT-ONLY and read-BLIND: a leak
--     of the public site's credential can append, not read the buyer list, not mutate history.
--   * Idempotent by stripe_event_id (UNIQUE): a Stripe retry / replay dedups via ON CONFLICT DO NOTHING
--     read from rowcount (no RETURNING → no SELECT needed). Bare ON CONFLICT because stripe_event_id is
--     the ONLY unique constraint.
--   * The write credential lives ONLY in the server env (PURCHASES_DB_DSN), never in the page/JS.
--
-- Guarded + idempotent (mirrors 033/032/999): the table/trigger are created unconditionally; the GRANT
-- block runs only when the writer role exists (an external/dev install without it skips, not errors).

BEGIN;

SET search_path = public, "$user";

-- ---------------------------------------------------------------------------
-- PURCHASES — append-only ledger of verified Stripe events behind the paid SKUs.
--   stripe_event_id — Stripe's event id (evt_...). UNIQUE so a webhook retry is a no-op (dedup key).
--   event_type      — checkout.session.completed | customer.subscription.deleted | charge.refunded | ...
--   mode            — 'payment' (Own-it, one-time) | 'subscription' (Stay-current, monthly).
--   product         — 'own-it' | 'stay-current' (mapped from the price/payment-link).
--   customer_email  — buyer email from the Checkout Session (for fulfillment + support).
--   github_username — from the Payment Link custom field: the private-repo invite target.
--   livemode        — true=live, false=Stripe test mode (segregate test rows from real ones).
--   payload_summary — a SMALL safe subset of the event (ids/amounts) — never card data or the full obj.
-- No UPDATE/DELETE is ever issued by any app role; it is an immutable append-only ledger.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS aq_purchases (
  id                bigserial   PRIMARY KEY,
  created_at        timestamptz NOT NULL DEFAULT now(),
  stripe_event_id   text        NOT NULL UNIQUE,
  event_type        text        NOT NULL,
  mode              text,
  product           text,
  stripe_session_id text,
  stripe_customer   text,
  subscription_id   text,
  customer_email    text,
  github_username   text,
  amount_cents      integer,
  currency          text,
  livemode          boolean,
  payload_summary   jsonb
);

CREATE INDEX IF NOT EXISTS aq_purchases_created_idx ON aq_purchases (created_at);

-- ---------------------------------------------------------------------------
-- Least-privilege grants. Only when the dedicated writer role exists.
--
-- aq_purchases_writer is the ONLY writer and it is APPEND-ONLY and BLIND: INSERT (+ the sequence it
-- needs for bigserial) and nothing else. Provisioned out-of-band by the deploy (see
-- site/agentricity/DEPLOY.md) and handed to the site process as PURCHASES_DB_DSN — never to the page.
--   * INSERT lets it append and lets ON CONFLICT DO NOTHING dedup (DO NOTHING needs no UPDATE).
--   * REVOKE SELECT so a compromised site credential cannot read back who bought.
--   * REVOKE UPDATE/DELETE/TRUNCATE so a recorded event can never be altered or erased.
-- Revoke-first (defense-in-depth against a re-appearing blanket grant), then grant the minimum.
-- ---------------------------------------------------------------------------
DO $aq_purchases_grants$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='aq_purchases_writer') THEN
    RAISE NOTICE 'aq_purchases_writer role absent; skipping aq_purchases grants (external/dev install)';
    RETURN;
  END IF;

  EXECUTE 'REVOKE ALL ON aq_purchases FROM aq_purchases_writer';
  EXECUTE 'GRANT INSERT ON aq_purchases TO aq_purchases_writer';
  -- USAGE only (lets nextval() serve the bigserial default). NOT SELECT: SELECT on the sequence is
  -- unneeded for INSERT and would let the read-blind writer read last_value, leaking the buyer count.
  EXECUTE 'REVOKE ALL ON SEQUENCE aq_purchases_id_seq FROM aq_purchases_writer';
  EXECUTE 'GRANT USAGE ON SEQUENCE aq_purchases_id_seq TO aq_purchases_writer';
  -- Belt-and-suspenders: make the withheld verbs an explicit revoke, so even if a later blanket
  -- GRANT ... ON ALL TABLES hands this role more, the read/mutate boundary is re-asserted here.
  EXECUTE 'REVOKE SELECT, UPDATE, DELETE, TRUNCATE ON aq_purchases FROM aq_purchases_writer';

  -- Trusted read-only operator principals may observe the ledger where they exist (parity with 033;
  -- harmless no-op on an isolated DB where they don't). This is how CURRENT entitlement is derived.
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='aq_governance') THEN
    EXECUTE 'GRANT SELECT ON aq_purchases TO aq_governance';
    EXECUTE 'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON aq_purchases FROM aq_governance';
  END IF;
END $aq_purchases_grants$;

-- Append-only, enforced by trigger as well as by grants (defense-in-depth, parity with 033/032/026): a
-- future accidental UPDATE/DELETE grant must still be rejected at the row level.
CREATE OR REPLACE FUNCTION public.reject_aq_purchases_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'aq_purchases is append-only';
END $$;

DROP TRIGGER IF EXISTS aq_purchases_append_only ON public.aq_purchases;
CREATE TRIGGER aq_purchases_append_only
  BEFORE UPDATE OR DELETE ON public.aq_purchases
  FOR EACH ROW EXECUTE FUNCTION public.reject_aq_purchases_mutation();

COMMIT;
