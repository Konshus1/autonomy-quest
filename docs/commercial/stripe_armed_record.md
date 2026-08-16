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

## URLs — DONE (domain cutover completed 2026-08-16)

agentricity.com is cut over to the ACA app (apex + www, Azure-managed TLS). Both webhook endpoints +
both Payment Link redirects were repointed from the Azure URL to **`https://agentricity.com/...`** via
`scripts/stripe_repoint_agentricity.py` (run once per mode). Live webhook re-proven end-to-end THROUGH
agentricity.com, bound to a specific event id. The buy.stripe.com Payment Link URLs did not change.

Payment Link ids (for future modifies): live own-it `plink_1U589BIm60U3T8xsup3obxFx`, live stay-current
`plink_1U589DIm60U3T8xsjxz4OTXj`; test own-it `plink_1U587LI0nyPgcOXl46FZEIxN`, test stay-current
`plink_1U587NI0nyPgcOXlhe9AFrDy`. To repoint again (e.g. a URL change): re-run
`STRIPE_API_KEY=<key> python3 scripts/stripe_repoint_agentricity.py --base-url <url> [--live]`.

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
