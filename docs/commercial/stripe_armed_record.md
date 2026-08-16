# Agentricity Stripe checkout — armed record (2026-08-16)

Provisioned via `scripts/stripe_provision_agentricity.py` in the **shared TalkingBack Stripe account**
(Kevin's call: same account). Both modes live. **No secrets in this file** — the webhook signing
secrets live only in the ACA secret `stripe-webhook-secret` (comma-separated: test,live).

## Objects (non-secret IDs)

| | Test mode | Live mode |
|---|---|---|
| Own-it product | `prod_V5IiBGXGKgtitv` | `prod_V5IkWCGQrILv5M` |
| Own-it price ($299 once) | `price_1U587LI0nyPgcOXl94lSf6z7` | `price_1U589BIm60U3T8xsT5DYQqMG` |
| Own-it Payment Link | `buy.stripe.com/test_5kQ14n22v7zK4h7d566Ri00` | **`buy.stripe.com/9B64gzdPydtk4zTfqrbQY00`** |
| Stay-current product | `prod_V5IiNlk9tgwzI6` | `prod_V5IkxGYxmSuTyA` |
| Stay-current price ($99/mo) | `price_1U587MI0nyPgcOXltL5iI1FG` | `price_1U589CIm60U3T8xsPXNtOaVR` |
| Stay-current Payment Link | `buy.stripe.com/test_fZuaEXdLd4nycND9SU6Ri01` | **`buy.stripe.com/cNicN5bHq0Gy8Q91zBbQY01`** |
| Webhook endpoint | `we_1U587NI0nyPgcOXlKlu7M3sg` | `we_1U589DIm60U3T8xsDWAoAzfw` |

The **live** Payment Links are wired into `index.html` (Own it / Stay current CTAs). Both links collect
email + a required "GitHub username" custom field and redirect to the thank-you page.

## Current URLs (pre-domain-cutover)

Both webhook endpoints + both Payment Link redirects currently point at the **Azure URL**
(`https://agentricity-site.orangebay-a6553ce1.eastus.azurecontainerapps.io/...`) so they work today,
before agentricity.com is cut over.

## TODO at the domain cutover

When agentricity.com points at the ACA app, update the LIVE objects to the friendly domain:

```python
import stripe
stripe.api_key = <sk_live>   # STRIPE_PROD_SECRET_KEY
BASE = "https://agentricity.com"
stripe.WebhookEndpoint.modify("we_1U589DIm60U3T8xsDWAoAzfw", url=f"{BASE}/api/stripe/webhook")
for pl in ("<own-it live link id>", "<stay-current live link id>"):
    stripe.PaymentLink.modify(pl, after_completion={"type":"redirect","redirect":{"url":f"{BASE}/thank-you.html"}})
```
(Payment Link *ids* — not the buy.stripe.com URLs — are needed for modify; list them with
`stripe.PaymentLink.list()`.) The buy.stripe.com URLs themselves do not change.

## Proven

Both modes verified end-to-end against the live endpoint, **bound to a specific chosen event id**
(not "a row exists"): a signed `checkout.session.completed` recorded exactly one row with correct
extraction (product / email / github_username / livemode), replay deduped, probe purged. Ledger left
pristine. The container verifies both signing secrets (comma-separated `STRIPE_WEBHOOK_SECRET`); test
rows land `livemode=false`, live rows `livemode=true`.

## Fulfillment (v1, manual/async)

On a `checkout.session.completed`, the buyer's `customer_email` + `github_username` land in
`agentricity_purchases`. Read the ledger (owner or an `aq_governance` SELECT role) and send the GitHub
collaborator invite to `Konshus1/autonomy-quest-private`. No GitHub credential is on the public box.
Automated invite + refund/cancel revocation are deferred to v2.
