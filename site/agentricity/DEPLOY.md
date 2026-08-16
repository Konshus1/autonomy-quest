# Agentricity site + signup capture — deploy

ONE isolated container (Dockerfile in this directory) serves the static marketing site **and** the
early-access email capture. It is a NEW, isolated Azure ACA app — never prod talkingback, never the
big autonomy-quest image. No loop, no agent, no MCP: just the page and an append-only signup endpoint.

## What it serves

- `/` → `index.html`, plus `js/`, `assets/`.
- The legal pages at their EXACT app-store-required paths: `/privacy.html`, `/terms.html`,
  `/refund.html`, `/legal/*`, `/app-ads.txt`.
- `POST /api/signup` → records an early-access email (see below).
- `GET /healthz` → `{"ok": true}` for the ACA health probe.

## The signup endpoint

`POST /api/signup` accepts JSON `{"email": "...", "source": "..."}` (source optional). It:

- validates + normalizes the email (lowercased, length-capped, control-char/injection rejected),
- caps the request body size and rate-limits per client IP,
- appends the address to the `aq_signups` table with a **parameterized** `INSERT ... ON CONFLICT DO
  NOTHING` (dedup),
- returns an honest result the page shows verbatim: `{"ok":true,"status":"recorded"}` for a new
  address, `{"ok":true,"status":"already"}` if it was already on the list. On any failure the page
  falls back to the honest `mailto:` path — it never claims a signup it didn't record.

The DB credential is read from the server env ONLY (`SIGNUPS_DB_DSN`). It is never in the static page
or client JS; the page just POSTs same-origin.

## Environment variables the deploy MUST set

| Variable | Required | Purpose |
|----------|----------|---------|
| `SIGNUPS_DB_DSN` | **yes (prod)** | libpq DSN for the **INSERT-only** `aq_signups_writer` role, e.g. `postgresql://aq_signups_writer:PW@HOST:5432/DB`. If unset the service falls back to a non-durable in-memory store (dev only). Keep this off the page and out of the repo. |
| `PORT` | no (default `8080`) | Port uvicorn binds; ACA sets this. |
| `AQ_SIGNUP_TRUSTED_PROXY_HOPS` | **set to `1` on ACA** | Trusted reverse-proxy hops in front of the app. Default `0` = ignore client-supplied `X-Forwarded-For` and rate-limit by the unspoofable socket peer (safe but coarse — collapses to the ingress IP behind a proxy). Set `1` behind ACA's single ingress so the limiter keys on the real client (the rightmost, ingress-set XFF entry) instead of the ingress IP. Never used for auth — coarse rate-limiting only. |
| `SIGNUP_MAX_BODY_BYTES` | no (default `2048`) | Request body size cap. |
| `SIGNUP_RATE_LIMIT_MAX` | no (default `10`) | Max signup POSTs per IP per window. |
| `SIGNUP_RATE_LIMIT_WINDOW_SEC` | no (default `60`) | Rate-limit window (seconds). |

## Provisioning the signups database (once, out-of-band)

The write credential is a dedicated, least-privilege, **read-blind** role. Provision it and apply the
migration with an admin/owner connection (NOT the writer):

```sql
-- 1) dedicated INSERT-only writer role (choose a strong password; keep it in the ACA secret store)
CREATE ROLE aq_signups_writer LOGIN PASSWORD '...';
GRANT CONNECT ON DATABASE <db> TO aq_signups_writer;
GRANT USAGE  ON SCHEMA public TO aq_signups_writer;
```

```bash
# 2) apply the append-only signups table + grants (baked into the image at /app/schema/033_signups.sql)
psql "$ADMIN_DSN" -f /app/schema/033_signups.sql
```

`schema/033_signups.sql` is guarded + idempotent: it creates the table/trigger unconditionally and
grants **only** when `aq_signups_writer` exists. After it runs, the writer has `INSERT` (+ the
sequence) and nothing else — it **cannot** `SELECT` (read the list back), `UPDATE`, `DELETE`, or
`TRUNCATE`. The table is append-only, enforced by both the grants and a row-level trigger.

## Build & run

```bash
# build context = repo root so the shared migration can be baked in
docker build -f site/agentricity/Dockerfile -t agentricity-site:latest .
docker run -p 8080:8080 -e SIGNUPS_DB_DSN="postgresql://aq_signups_writer:PW@HOST:5432/DB" agentricity-site:latest
```

## Tests

- `tests/test_signup_endpoint.py` — no DB required; proves records+dedup, invalid/oversized/injection
  rejection, rate limiting, and that the legal pages serve at their exact paths. Always runs.
- `tests/test_signups_grant_boundary.py` — live-PG proof that the writer is INSERT-only + read-blind
  and the table is append-only. Gated on `SIGNUPS_ADMIN_DSN` (a superuser/owner DSN); set
  `SIGNUPS_REQUIRE_GRANTS=1` to make it a hard failure instead of a skip:

  ```bash
  SIGNUPS_ADMIN_DSN="postgresql://localhost:5432/postgres" SIGNUPS_REQUIRE_GRANTS=1 \
    pytest -q tests/test_signups_grant_boundary.py
  ```

  > **Point `SIGNUPS_ADMIN_DSN` at a DISPOSABLE Postgres — never a server that hosts the live
  > `aq_signups_writer` role.** Roles are cluster-global, so the fixture creating/dropping the role
  > would clobber the production writer credential. The fixture refuses (raises) if the role already
  > exists on the target server, and drops the role it creates on teardown.

## Applied to production (agentricity15 / `postgres`, 2026-08-16)

The live marketing app is the isolated ACA app **`agentricity-site`** (`agentricity-rg`); the DB is the
shared **`agentricity15.postgres.database.azure.com` / `postgres`** flexible server (same server the
prod `talkingback-api-aca` app uses — we only ADD the namespaced `aq_signups` table + `aq_signups_writer`
role; no existing object is touched). Proven locally first, then migrated to prod with the runbook above.
Wiring set on the ACA app:

- secret `signups-db-dsn` = the INSERT-only writer DSN (`...?sslmode=require`); env `SIGNUPS_DB_DSN`
  → `secretref:signups-db-dsn`.
- env `AQ_SIGNUP_TRUSTED_PROXY_HOPS=1` (behind ACA's single ingress).

Three things that bit us and will bite the next migrator:

1. **Egress firewall.** A Consumption ACA app's OUTBOUND IP is **not** its environment `staticIp`
   (that's inbound). Its SNAT egress reaches Postgres via the server's
   `AllowAllAzureServicesAndResourcesWithinAzureIps` rule (the same rule prod talkingback relies on).
   Do NOT try to allowlist the env `staticIp` as a `/32` — it does nothing. Keep the "Allow Azure
   services" rule enabled; that is the load-bearing grant.
2. **Secret-value changes need a NEW revision.** `az containerapp revision restart` does NOT reliably
   re-inject an updated secret VALUE. After `az containerapp secret set`, force a fresh revision (e.g.
   bump any env var) so the container picks up the new value.
3. **Never run the grant-boundary test against this server** (see the callout above) — it resets the
   cluster-global writer password. If you do it by accident, re-sync: `ALTER ROLE aq_signups_writer
   LOGIN PASSWORD '<new>'`, update the `signups-db-dsn` secret to match, and force a new revision.

## Stripe checkout (purchases webhook) — INERT until armed

The site sells two SKUs via Stripe **Payment Links** (`buy.stripe.com/...`, non-secret, in the page).
The container deliberately holds **only the webhook SIGNING secret** — never `STRIPE_SECRET_KEY` — so a
leak cannot move money. `POST /api/stripe/webhook` verifies Stripe's signature (stdlib HMAC, fail
closed) and appends each verified event to the append-only, read-blind `aq_purchases` ledger
(`schema/034_purchases.sql`, exact mirror of `033`). **It is inert until `STRIPE_WEBHOOK_SECRET` is
set** (returns `503 not_configured`, appends nothing). Current entitlement is DERIVED by reading the
ledger; fulfillment (private-repo invite) is manual/async in v1 — see
`docs/commercial/stripe_checkout_plan.md`.

Env/secrets to add on `agentricity-site` when arming (NOT the Stripe secret key):

| Secret (ACA) | Env var | Purpose | Blast radius |
|---|---|---|---|
| `stripe-webhook-secret` | `STRIPE_WEBHOOK_SECRET` → `secretref:stripe-webhook-secret` | verify webhook signatures | signing only — cannot call Stripe API |
| `purchases-db-dsn` | `PURCHASES_DB_DSN` → `secretref:purchases-db-dsn` | INSERT-only `aq_purchases_writer` DSN (`?sslmode=require`) | append-only, read-blind |

Provision the purchases DB exactly like signups (owner connection): create the `aq_purchases_writer`
LOGIN role, `GRANT CONNECT ON DATABASE ... / USAGE ON SCHEMA public`, then
`psql "$ADMIN_DSN" -f /app/schema/034_purchases.sql`. Same caveats: egress rides the "Allow Azure
services" rule; a secret-value change needs a fresh revision; **never** run
`tests/test_purchases_grant_boundary.py` against a server hosting the live role (use a disposable one).

Stripe dashboard objects (test mode first) — **needs Kevin + a real Stripe login**: two products/prices
($299 one-time, $99/mo), a Payment Link each (collect email + a required "GitHub username" custom field,
redirect to `/thank-you.html`), and a webhook endpoint → `/api/stripe/webhook` subscribed to
`checkout.session.completed` (+ subscription/refund events for the monthly tier). Copy the link URLs
into `index.html` and the endpoint's signing secret into `stripe-webhook-secret`.
