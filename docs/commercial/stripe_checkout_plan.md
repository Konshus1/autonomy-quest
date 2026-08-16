# Self-serve Stripe checkout for the Agentricity site — plan

*Research + implementation plan. Nothing here has been built, and no Stripe object was created. This
document does not modify application code or Azure infra. It mirrors the established read-blind
`aq_` writer pattern (`site/agentricity/server/app.py`, `schema/033_signups.sql`,
`site/agentricity/DEPLOY.md`) and the proven talkingback Stripe integration.*

**Offer to support (from the task brief — treated as authoritative here, but see Open Questions):**
- **PRIMARY — "Own-it", $299 one-time** → grants the private repo (`Konshus1/autonomy-quest-private`)
  / replication rights. Stripe `mode=payment`.
- **Secondary — "Stay-current", $99/mo subscription** → Stripe `mode=subscription`.
- **Free tier** = public repo, no checkout (the existing `POST /api/signup` early-access capture).

---

## 1. What already exists — the talkingback Stripe setup (reference implementation)

The talkingback prod app (Azure ACA `talkingback-api-aca`, `talkingback-rg`) has a mature Stripe
integration. Source read on this machine at `/Users/kevincthomas/src/talkingback-fastapi-deploy-main/`
(the deployed FastAPI tree). Everything Stripe lives under `app/payments/`, funnelled through one SDK
wrapper `app/payments/stripe_service.py`. Key facts with file:line citations:

**Library.** Python `stripe` package pinned `stripe==9.12.0`
(`.../requirements.txt:401`, `.../requirements.docker.txt:207`). Imported + configured once in
`app/payments/stripe_service.py:23-29` (import guarded so the app boots without it) and
`app/payments/stripe_service.py:46-49` (`stripe.api_key = STRIPE_SECRET_KEY`,
`stripe.max_network_retries = 2`). All direct Stripe calls funnel through this wrapper for mockability
(docstring `app/payments/stripe_service.py:1-8`).

**Checkout flow = hosted Stripe Checkout Sessions** (no Payment Links; PaymentIntents appear only in
admin refund/void tooling). Core creator `create_checkout_session(...)`
(`app/payments/stripe_service.py:611-624`) with the API call at
`app/payments/stripe_service.py:679` — `stripe.checkout.Session.create(**session_kwargs)`. Params
assembled at `app/payments/stripe_service.py:652-677`: `payment_method_types=["card"]`,
`mode` = `"payment"` (one-time) or `"subscription"`, `line_items=[{"price": price_id, "quantity": 1}]`,
`success_url`/`cancel_url`, `metadata={"user_id": ...}`, plus promo + trial handling. The public
endpoint is `POST /api/payments/create-checkout-session` (`app/payments/router.py:248-334`); the
request carries a `price_id` (`app/payments/router.py:48`). Also present: customer Billing Portal
(`stripe.billing_portal.Session.create` — `app/payments/stripe_service.py:875`) and
cancel/downgrade (`stripe.Subscription.delete/modify` — `app/payments/router.py:943-945`).

**Webhook + signature verification.** Endpoint `POST /api/payments/webhook`
(`stripe_webhook(...)`, `app/payments/router.py:337-446`): it reads the **raw** body, calls
`verify_webhook_signature(raw_body, sig_header)` (`app/payments/router.py:374`), then dispatches to
`handle_webhook(event, ...)`. Verification is
`stripe.Webhook.construct_event(payload=raw_body, sig_header=sig_header, secret=STRIPE_WEBHOOK_SECRET)`
(`app/payments/stripe_service.py:936-940`), catching `SignatureVerificationError`/`ValueError` →
raise (`:941-943`). A dev bypass `STRIPE_SKIP_WEBHOOK_VERIFY` exists but **fails closed in production**
(`:909-920`). Idempotency: events are claimed atomically into table `stripe_webhook_events` via
`_claim_webhook_event(...)` (`app/payments/stripe_service.py:948-1013`,
`INSERT ... ON CONFLICT (event_id) DO UPDATE ... WHERE status='failed'`); duplicate replays return
`200 duplicate_skipped` (`:1218-1237`). Handled event types (all in `handle_webhook`,
`app/payments/stripe_service.py:1159+`): `checkout.session.completed` (`:1239`, the grant path),
`customer.subscription.deleted` (`:1720`, revoke), `customer.subscription.updated` (`:1863`),
`invoice.payment_failed` (`:2051`), `invoice.payment_succeeded` (`:2124`, renewal),
`charge.refunded` (`:2230`, revoke).

**Env/secret NAMES** (values never printed). Read in `app/config.py:238-241` via
`get_stripe_keys()` (`:244-250`): `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`,
`STRIPE_WEBHOOK_SECRET`. Admin auth uses `STRIPE_PAYMENTS_ADMIN_TOKEN`
(`app/payments/admin_router.py:34`). These same names are confirmed live on the ACA app
(`az containerapp show -n talkingback-api-aca ...` → env names `STRIPE_ADMIN_TOKEN`,
`STRIPE_PAYMENTS_ADMIN_TOKEN`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`;
secrets `stripe-secret-key`, `stripe-publishable-key`, `stripe-webhook-secret`,
`stripe-payments-admin-token`, `stripe-admin-token`).

**Price/product IDs = DB-driven catalog, not hardcoded.** `Feature.stripe_product_id`
(`app/db/access_control_models.py:91`); price IDs live in `Feature.metadata` JSON under
`stripe_price_ids`, resolved by `_resolve_feature_slug_by_price_id(...)`
(`app/payments/stripe_service.py:741-777`). Provisioned via scripts + the token-gated admin API
(`stripe.Product.create` `app/payments/admin_router.py:442`, `stripe.Price.create` `:456`).

**Entitlement/purchase storage** (three tables):
- `payment` — `Payment` model `app/db/db_models.py:1358-1381` (`stripe_session_id` unique,
  `stripe_payment_intent`, `price_id`, `customer_email`, `amount`, `currency`, `status`,
  `payment_metadata`). Row created at checkout (`stripe_service.py:686-700`), flipped to `completed`
  in the webhook (`:1259-1266`).
- `user_access` — `UserAccess` `app/db/access_control_models.py:56-73` (`user_id`, `resource_id`,
  `allowed`, `start_at`, `end_at` — NULL = perpetual for one-time, datetime for subscription). This is
  the actual entitlement row; granted by `_grant_feature_user_access(...)` (`stripe_service.py:301-369`)
  and revoked by `_revoke_feature_user_access(...)` (`:372-401`).
- `subscriptions` — `Subscription` `app/db/access_control_models.py:189-258` (local mirror of Stripe
  subscription lifecycle).

**Admin token.** `app/payments/admin_router.py` (prefix `/api/payments/admin`, `include_in_schema=False`)
is guarded by `verify_admin_token` (`:39-43`) comparing an `X-Admin-Token` header against
`STRIPE_PAYMENTS_ADMIN_TOKEN`. Live-mode mutations require an extra ack when the key is `sk_live_*`
(`:658,686`).

**Takeaway for agentricity:** talkingback proves the robust pattern (hosted Checkout Sessions +
signed webhook + DB entitlement + idempotency ledger), but it is a *large* app holding
`STRIPE_SECRET_KEY` and a full access-control schema. The agentricity site must NOT inherit that
weight or that secret blast radius — see §2.

---

## 2. Recommended approach for agentricity

### Recommendation (firm): **Stripe Payment Links + one thin, signature-verified, append-only webhook recorder.**

Not pure "zero-backend Payment Links" (that leaves no durable record we own and no fulfillment
trigger), and **not** hosted Checkout Sessions (that forces `STRIPE_SECRET_KEY` onto the public
container). The middle path is strictly better under this project's constraints.

**Why this wins on "tiny isolated container, self-serve, no server-side secret sprawl":**

| Dimension | Payment Links + webhook recorder (recommended) | Hosted Checkout Sessions |
|---|---|---|
| Secret on the public container | **Only `STRIPE_WEBHOOK_SECRET`** — a *signing* secret. It can verify events; it **cannot call the Stripe API, spend, refund, or read customers.** Lowest possible blast radius. | Requires `STRIPE_SECRET_KEY` (full API access — can move money) on a public-internet container. Much larger blast radius. |
| Backend code to create checkout | **None.** The "Buy" buttons are plain `<a href>` to `buy.stripe.com/...` links (public, non-secret). | A `POST /create-checkout-session` endpoint that authenticates to Stripe with the secret key. |
| New deps in the image | **Zero** — signature verify is stdlib `hmac`/`hashlib` (§3). | The `stripe` SDK (or hand-rolled API calls). |
| Fits the read-blind-writer ethos | Yes — the container only *appends* (to `aq_purchases` and, via signature-verify, never trusts client input for money). Mirrors `aq_signups`. | Partially — but it now holds a money-moving credential. |
| Robustness (durable record, idempotency, fulfillment trigger, subscription lifecycle) | Yes — the webhook gives all of it. | Yes. |

Payment Links natively support everything the offer needs: a one-time price and a recurring price,
collecting the buyer email, a **custom field** (we'll collect the buyer's **GitHub username** for
fulfillment), post-payment redirect to a thank-you page, and they emit the exact same
`checkout.session.completed` (and subscription) webhooks as Checkout Sessions. The only thing we give
up vs. Sessions is programmatic control of the session (dynamic pricing, per-user metadata injection
from *our* server) — none of which this simple two-SKU, self-serve offer needs.

### Fulfillment — how the buyer gets private-repo access after paying

The paid deliverable is access to `Konshus1/autonomy-quest-private` (see
`docs/commercial/public_private_split_plan.md`). Design:

- **v1 (recommended launch): async manual grant, matching the brand.** The site already tells buyers
  onboarding is async/after-hours and batched (`docs/commercial/site_structure_and_offer.md:64`), and
  the honesty posture is load-bearing. On a verified `checkout.session.completed`, the webhook (a)
  **durably records** the purchase to `aq_purchases`, and (b) the record carries `customer_email` +
  the GitHub-username custom field. Kevin (or a trusted read-only role) reads new rows and sends the
  **GitHub collaborator invite** to that username. This keeps the public container **write-only and
  free of any GitHub credential** — no new spend/publish authority on an internet-facing box.
- **v2 (later, optional): automated invite** via a GitHub App/token in the webhook
  (`PUT /repos/Konshus1/autonomy-quest-private/collaborators/{username}`). Deferred because it adds a
  GitHub write credential to the public container (blast-radius increase) and needs the authority
  gate — do not ship it in v1. For the $99/mo tier, automated *revocation* on cancellation/refund is
  the mirror concern (see subscription lifecycle below).
- Either way the flow stays honest: the thank-you page says *"Payment received — we'll email your
  private-repo invite to the GitHub account you gave us (async, usually within a day)."* Never claim
  instant access the system can't deliver (P31/P43 in `site_structure_and_offer.md`).

### Recording the purchase durably — mirror the read-blind `aq_` writer

New migration `schema/034_purchases.sql`, a strict copy of `schema/033_signups.sql`'s discipline:
append-only, one dedicated **INSERT-only, read-blind** writer role `aq_purchases_writer`, dedup via
`ON CONFLICT DO NOTHING` read from `cur.rowcount` (never `RETURNING` — RETURNING needs SELECT), bare
`ON CONFLICT` (one unique constraint), append-only trigger + withheld UPDATE/DELETE/SELECT grants.
Because the table is **append-only**, it is an **event ledger**, not a mutable state table: we append
one row per verified Stripe event; *current* entitlement is derived by reading the ledger (Kevin, or
an `aq_governance` SELECT role — the same optional read principal `033` already grants at
`schema/033_signups.sql:74-77`). This is the honest, low-privilege analogue of talkingback's
`Payment`/`Subscription`/`stripe_webhook_events` tables.

Proposed `aq_purchases` shape:

```sql
CREATE TABLE IF NOT EXISTS aq_purchases (
  id                bigserial   PRIMARY KEY,
  created_at        timestamptz NOT NULL DEFAULT now(),
  stripe_event_id   text        NOT NULL UNIQUE,   -- idempotency: dedup replays (evt_...)
  event_type        text        NOT NULL,          -- checkout.session.completed, customer.subscription.deleted, ...
  mode              text,                           -- 'payment' (own-it) | 'subscription' (stay-current)
  product           text,                           -- 'own-it' | 'stay-current' (from the link/price mapping)
  stripe_session_id text,                           -- cs_...
  stripe_customer   text,                           -- cus_...
  subscription_id   text,                           -- sub_... (subscription mode only)
  customer_email    text,                           -- from the Checkout Session
  github_username   text,                           -- from the Payment Link custom field (fulfillment target)
  amount_cents      integer,
  currency          text,
  livemode          boolean,                        -- true=live, false=test (segregate test rows)
  payload_summary   jsonb                           -- small safe subset of the event (NOT the full object / no card data)
);
CREATE INDEX IF NOT EXISTS aq_purchases_created_idx ON aq_purchases (created_at);
```

The unique key is `stripe_event_id` (not session id) so **every** verified event type dedups the same
way and a Stripe retry is a no-op. Grants/trigger/writer-role block: copy `033` verbatim, renaming
`aq_signups*`→`aq_purchases*`.

### Webhook signature verification

Verify **before** trusting anything, on the **raw** request body (same as the existing signup handler
reads `await request.body()`), using the Stripe HMAC scheme. Two options:

- **Recommended (keeps the image dependency-light): hand-rolled stdlib verify.** Parse the
  `Stripe-Signature` header (`t=<unixts>,v1=<hex>,...`), compute
  `HMAC_SHA256(secret=STRIPE_WEBHOOK_SECRET, msg="{t}.{raw_body}")`, `hmac.compare_digest` against each
  `v1`, and reject if the timestamp is outside a tolerance (e.g. 5 min) to stop replay. ~20 lines,
  no new dependency — consistent with this repo's hand-rolled minimalism. Fail **closed**: no valid
  signature → `400`, append nothing.
- **Alternative (lower-risk, standard): add `stripe==9.12.0`** (mirror talkingback's pin) and call
  `stripe.Webhook.construct_event(raw_body, sig_header, STRIPE_WEBHOOK_SECRET)`. One extra dep on the
  "tiny" image; use this if you'd rather not maintain hand-rolled crypto. Either way the container
  still holds **only the signing secret**, never `STRIPE_SECRET_KEY`.

Return `200` fast on success (and on already-seen `stripe_event_id`); `400` on bad/missing signature;
`503` only if the DB append fails (so Stripe retries). Reuse the existing per-IP rate limiter and body
cap from `server/app.py`.

---

## 3. Concrete implementation checklist (under `site/agentricity/`)

### Files to add / change (code — the doable, non-secret parts)

1. **`schema/034_purchases.sql`** (NEW) — append-only `aq_purchases` + read-blind `aq_purchases_writer`
   role + append-only trigger, a direct rename-copy of `schema/033_signups.sql`. Bake into the image
   like `033` (see `site/agentricity/Dockerfile` `COPY schema/033_signups.sql ...`).
2. **`site/agentricity/server/app.py`** (EDIT) — add:
   - a `PurchaseStore` Protocol + `InMemoryPurchaseStore` + `PostgresPurchaseStore` mirroring the
     existing `SignupStore`/`PostgresStore` (INSERT-only, dedup via `rowcount`, bare `ON CONFLICT`).
   - `verify_stripe_signature(raw_body, sig_header, secret, tolerance_sec)` helper (stdlib `hmac`).
   - `POST /api/stripe/webhook` handler: rate-limit → read raw body → verify signature (fail closed)
     → parse event → map price/link → append to `aq_purchases` (dedup) → `200`. Never echo secrets or
     the full event.
   - Wire `PURCHASES_DB_DSN` env → `PostgresPurchaseStore` (fallback in-memory for dev), matching how
     `SIGNUPS_DB_DSN` is wired at `server/app.py:247-249`.
   - `_PublicStaticFiles` already 404s `.py`/`.md`/Dockerfile (`server/app.py:207-232`) — no change
     needed to keep the new code un-served.
3. **`site/agentricity/index.html`** (EDIT) — add a pricing section with two buttons:
   `<a class="btn" href="{OWN_IT_PAYMENT_LINK_URL}">Own it — $299</a>` and
   `<a class="btn" href="{STAY_CURRENT_PAYMENT_LINK_URL}">Stay current — $99/mo</a>`. These URLs are
   **public** (non-secret `buy.stripe.com/...`), so they live in the page (or as plain non-secret env
   substituted at build) — no publishable key, no JS SDK. Keep "Start free" → the existing signup
   capture for the free tier.
4. **`site/agentricity/thank-you.html`** (NEW) — honest post-payment page the Payment Links redirect
   to ("payment received; private-repo invite emailed to your GitHub account async"). Served at its
   exact path like the other static pages.
5. **`site/agentricity/server/requirements.txt`** (EDIT only if using the SDK option) — add
   `stripe==9.12.0`. If using the stdlib verify (recommended), **no change**.
6. **`site/agentricity/DEPLOY.md`** (EDIT) — document the new env/secret names, the webhook endpoint
   URL, and the `aq_purchases_writer` provisioning runbook (copy the `033`/writer section).
7. **`tests/test_stripe_webhook.py`** (NEW) — no-DB tests (mirror
   `tests/test_signup_endpoint.py`): valid signature appends + returns 200; tampered body / wrong
   secret / missing header → 400; stale timestamp → 400; duplicate `event_id` → 200 no second append;
   body-cap + rate-limit still apply. Plus a `tests/test_purchases_grant_boundary.py` mirroring
   `tests/test_signups_grant_boundary.py` (live-PG proof the writer is INSERT-only + read-blind +
   append-only), gated on an admin DSN.

### New env / secret names needed on the `agentricity-site` ACA app

Current app has env `AQ_SIGNUP_TRUSTED_PROXY_HOPS`, `SIGNUPS_DB_DSN`, `SECRET_SYNC` and secrets
`signups-db-dsn`, `talkingbackazurecrio-talkingback`. Add:

| New secret (ACA) | New env var | Purpose | Blast radius |
|---|---|---|---|
| `stripe-webhook-secret` | `STRIPE_WEBHOOK_SECRET` → `secretref:stripe-webhook-secret` | Verify webhook signatures | **Signing only — cannot call Stripe API.** |
| `purchases-db-dsn` | `PURCHASES_DB_DSN` → `secretref:purchases-db-dsn` | INSERT-only `aq_purchases_writer` DSN (same `agentricity15/postgres` server, `?sslmode=require`) | Append-only, read-blind. |

Explicitly **NOT** added: `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_PAYMENTS_ADMIN_TOKEN`.
The Payment Link URLs are non-secret (page/env). Remember the two ACA gotchas from `DEPLOY.md:101-113`:
a secret **value** change needs a **new revision** to take effect, and egress to Postgres rides the
server's "Allow Azure services" rule (don't allowlist the env `staticIp`).

### Stripe dashboard objects to create — **REQUIRES KEVIN + a real Stripe key; NOT done by me**

Do all of this in **TEST mode first**, then repeat in **live**:
1. Product **"Own-it"** with a **one-time** price **$299 USD** (→ `price_...`, `mode=payment`).
2. Product **"Stay-current"** with a **recurring** price **$99/mo USD** (→ `price_...`, `mode=subscription`).
3. A **Payment Link** for each: enable *collect customer email*, add a **required custom field
   "GitHub username"**, set the **after-payment redirect** to `https://<site>/thank-you.html`.
   Copy the two `buy.stripe.com/...` URLs into `index.html`.
4. A **webhook endpoint** → `https://agentricity-site.orangebay-a6553ce1.eastus.azurecontainerapps.io/api/stripe/webhook`,
   subscribed to at minimum `checkout.session.completed`; for the subscription tier also
   `customer.subscription.deleted`, `customer.subscription.updated`, `invoice.payment_succeeded`,
   `invoice.payment_failed`, `charge.refunded`. Copy its **signing secret** into the
   `stripe-webhook-secret` ACA secret.
5. (Optional) Enable **Stripe Tax** and configure receipts.

### Provisioning the purchases DB — **REQUIRES an admin DSN; NOT done by me**

Same runbook as signups (`DEPLOY.md:42-61`): with an **admin/owner** connection, create the
`aq_purchases_writer` LOGIN role, `GRANT CONNECT/USAGE`, then `psql "$ADMIN_DSN" -f
/app/schema/034_purchases.sql`. Never run the grant-boundary test against the live `agentricity15`
server (roles are cluster-global — same caveat as `DEPLOY.md:84-88`).

### Test plan

- **Unit (always-run, no DB, no network):** the `test_stripe_webhook.py` cases above — signature
  valid/invalid/missing/stale, dedup, append, status codes; reuse the in-memory store.
- **Grant-boundary (live PG, gated):** `test_purchases_grant_boundary.py` proves `aq_purchases_writer`
  is INSERT-only + read-blind + append-only against a disposable Postgres.
- **Stripe TEST-mode end-to-end (requires Kevin/test key):** run the container locally with a test
  `STRIPE_WEBHOOK_SECRET`; `stripe listen --forward-to localhost:8080/api/stripe/webhook`; complete a
  test Payment Link with card `4242 4242 4242 4242`; and `stripe trigger checkout.session.completed`
  / `...subscription.deleted`. Assert a correctly-parsed row lands in `aq_purchases` (read via the
  admin/`aq_governance` SELECT role), replays don't duplicate, and a tampered event is rejected `400`.
- **Fulfillment dry-run:** confirm the recorded `github_username` + `customer_email` are enough for
  Kevin to send the collaborator invite; verify the thank-you page copy matches reality.

### Steps that need Kevin's decision or a real Stripe key (flagged — NOT to be done autonomously)

- Creating the two products/prices/Payment Links and the webhook endpoint (needs Stripe login).
- Retrieving + setting `stripe-webhook-secret` and `purchases-db-dsn` on the ACA app (real secrets).
- Provisioning `aq_purchases_writer` + running `034` against `agentricity15/postgres` (admin DSN).
- Choosing v1 manual fulfillment vs. v2 automated GitHub-API invite (adds a write credential + needs
  the authority gate).
- Confirming final pricing/naming (task brief vs. the older tier doc — see Open Questions).

---

## 4. Open questions for the user (Kevin)

1. **Pricing reconciliation.** The task brief says **$299 one-time "Own-it" + $99/mo "Stay-current"**,
   but `docs/commercial/site_structure_and_offer.md:99-117` currently frames Full at **$99/mo**
   (anchor) and Managed at $249/mo. Which is canonical for v1? (This plan builds to the brief.)
2. **What exactly does "Own-it" grant, and how?** Collaborator access to
   `Konshus1/autonomy-quest-private` (my assumption), a licensed download/tarball, or both? That
   decides whether fulfillment is a GitHub invite, a signed download link, or a license key.
3. **Relationship between the two SKUs.** Is "Stay-current" ($99/mo) a standalone subscription, or
   does it require "Own-it" first (updates/support on top of the one-time purchase)? Affects whether
   the subscription tier grants anything new or just renews access to updates.
4. **Fulfillment automation timing.** Ship v1 as **manual async** GitHub invites (no new credential on
   the public box), or invest now in the automated GitHub-API invite in the webhook (v2)?
5. **Revocation policy.** On refund (`charge.refunded`) or subscription cancel
   (`customer.subscription.deleted`), do we **revoke** private-repo access — and is that manual or
   automated? (For a one-time "own it" purchase, arguably access is permanent; for $99/mo it should
   lapse.)
6. **Identity capture.** OK to make **GitHub username a required Payment Link custom field** so we can
   fulfill without a follow-up email round-trip?
7. **DB isolation.** Reuse the shared `agentricity15/postgres` server (add `aq_purchases`, mirroring
   `aq_signups`), or isolate purchases in a separate DB? (Lean: reuse, namespaced + read-blind.)
8. **Verify implementation.** Stdlib hand-rolled signature verify (zero new deps) vs. adding the
   `stripe` SDK (standard, one dep on the tiny image)?
