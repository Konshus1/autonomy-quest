-- Flagship business benchmark substrate: structure only, ZERO customer/revenue content.
-- The running-a-business mission measures active paying customers from subscriptions.
BEGIN;

CREATE TABLE IF NOT EXISTS customers (
  id          text PRIMARY KEY,
  name        text NOT NULL,
  email       text UNIQUE,
  created_at  timestamptz NOT NULL DEFAULT now(),
  metadata    jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS subscriptions (
  id                 bigserial PRIMARY KEY,
  customer_id        text NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
  status             text NOT NULL CHECK (status IN ('trialing', 'active', 'past_due', 'paused', 'canceled')),
  monthly_amount_usd numeric(12,2) NOT NULL CHECK (monthly_amount_usd >= 0),
  started_at         timestamptz NOT NULL DEFAULT now(),
  ended_at           timestamptz,
  source             text NOT NULL,
  metadata           jsonb NOT NULL DEFAULT '{}'::jsonb,
  CHECK (ended_at IS NULL OR ended_at >= started_at)
);

CREATE INDEX IF NOT EXISTS subscriptions_customer_idx ON subscriptions (customer_id);
CREATE INDEX IF NOT EXISTS subscriptions_active_customer_idx
  ON subscriptions (customer_id) WHERE status = 'active';

COMMIT;
