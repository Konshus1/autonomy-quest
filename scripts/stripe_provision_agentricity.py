#!/usr/bin/env python3
"""Provision the Agentricity Stripe checkout for ONE mode (test or live), entirely via the API.

Creates (idempotently) the two products + prices, a Payment Link for each, and the webhook endpoint —
and CAPTURES the webhook signing secret from the create response (Stripe returns it only at creation),
so nothing needs the dashboard. Prints the two buy-links + the signing secret at the end.

    STRIPE_API_KEY=sk_test_... python3 scripts/stripe_provision_agentricity.py \
        --base-url https://agentricity-site.orangebay-a6553ce1.eastus.azurecontainerapps.io

    # live mode requires the explicit --live flag as a money-safety interlock:
    STRIPE_API_KEY=sk_live_... python3 scripts/stripe_provision_agentricity.py \
        --base-url https://agentricity.com --live

Mode is decided by the key prefix. The redirect (thank-you) + webhook URLs come from --base-url, so
run test against the Azure URL and live against https://agentricity.com (after the domain cutover).

Idempotent: products/prices/links are matched by a metadata tag (agentricity_sku) and reused. The
webhook is matched by URL; if it already exists the secret is NOT re-returnable by Stripe, so re-runs
print a note and you must roll it in the dashboard (or delete+rerun) to get a fresh secret.

This is OUT-OF-BAND ops tooling — it holds the Stripe SECRET key transiently in THIS process only and
never writes it anywhere. The public container never sees the secret key; it only ever gets the webhook
SIGNING secret (whsec_), which callers set as the `stripe-webhook-secret` ACA secret.
"""
from __future__ import annotations

import argparse
import os
import sys

import stripe

# The offer. Prices in the SMALLEST currency unit (cents). Edit here if the offer changes.
CURRENCY = "usd"
SKUS = {
    "own-it": {
        "product_name": "Agentricity — Own it",
        "description": "One-time purchase: the private Agentricity repo (replication + self-improvement).",
        "unit_amount": 29900,          # $299.00 one-time
        "recurring": None,
    },
    "stay-current": {
        "product_name": "Agentricity — Stay current",
        "description": "Monthly subscription: ongoing updates to the private Agentricity repo.",
        "unit_amount": 9900,           # $99.00 / month
        "recurring": {"interval": "month"},
    },
}
WEBHOOK_EVENTS = [
    "checkout.session.completed",
    "customer.subscription.deleted",
    "customer.subscription.updated",
    "invoice.payment_succeeded",
    "invoice.payment_failed",
    "charge.refunded",
]


def _find_product(sku: str):
    # Product search supports metadata queries; fall back to a list scan if search is disabled.
    try:
        res = stripe.Product.search(query=f"active:'true' AND metadata['agentricity_sku']:'{sku}'")
        if res.data:
            return res.data[0]
    except Exception:
        for p in stripe.Product.list(active=True, limit=100).auto_paging_iter():
            if (p.metadata or {}).get("agentricity_sku") == sku:
                return p
    return None


def _ensure_product_price(sku: str, cfg: dict):
    prod = _find_product(sku)
    if prod is None:
        prod = stripe.Product.create(
            name=cfg["product_name"], description=cfg["description"],
            metadata={"agentricity_sku": sku},
        )
        print(f"  created product {sku}: {prod.id}")
    else:
        print(f"  reuse product {sku}: {prod.id}")
    # Find a matching active price on this product, else create one.
    price = None
    for pr in stripe.Price.list(product=prod.id, active=True, limit=100).auto_paging_iter():
        if pr.unit_amount == cfg["unit_amount"] and pr.currency == CURRENCY and \
           bool(pr.recurring) == bool(cfg["recurring"]) and \
           (not cfg["recurring"] or pr.recurring.interval == cfg["recurring"]["interval"]):
            price = pr
            break
    if price is None:
        kwargs = dict(product=prod.id, unit_amount=cfg["unit_amount"], currency=CURRENCY,
                      metadata={"agentricity_sku": sku})
        if cfg["recurring"]:
            kwargs["recurring"] = cfg["recurring"]
        price = stripe.Price.create(**kwargs)
        print(f"  created price {sku}: {price.id}")
    else:
        print(f"  reuse price {sku}: {price.id}")
    return price


def _ensure_payment_link(sku: str, price_id: str, thankyou_url: str):
    for pl in stripe.PaymentLink.list(active=True, limit=100).auto_paging_iter():
        if (pl.metadata or {}).get("agentricity_sku") == sku:
            print(f"  reuse payment link {sku}: {pl.url}")
            return pl.url
    pl = stripe.PaymentLink.create(
        line_items=[{"price": price_id, "quantity": 1}],
        after_completion={"type": "redirect", "redirect": {"url": thankyou_url}},
        custom_fields=[{
            "key": "githubusername",
            "label": {"type": "custom", "custom": "GitHub username"},
            "type": "text",
            "optional": False,
        }],
        metadata={"agentricity_sku": sku},
    )
    print(f"  created payment link {sku}: {pl.url}")
    return pl.url


def _ensure_webhook(webhook_url: str):
    for we in stripe.WebhookEndpoint.list(limit=100).auto_paging_iter():
        if we.url == webhook_url:
            print(f"  webhook already exists: {we.id}")
            print("  NOTE: Stripe only returns the signing secret at creation. To get a fresh secret,")
            print("        roll it in the dashboard (or delete this endpoint and re-run).")
            return None
    we = stripe.WebhookEndpoint.create(
        url=webhook_url, enabled_events=WEBHOOK_EVENTS,
        description="Agentricity marketing-site purchase ledger",
        metadata={"agentricity": "purchases"},
    )
    print(f"  created webhook: {we.id}")
    return we.get("secret")  # whsec_... — only present on the create response


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True,
                    help="scheme+host the buyer/webhook hit, e.g. https://agentricity.com")
    ap.add_argument("--live", action="store_true", help="required to run against a sk_live_ key")
    args = ap.parse_args()

    key = os.environ.get("STRIPE_API_KEY")
    if not key:
        print("STRIPE_API_KEY not set (sk_test_... or sk_live_...)", file=sys.stderr)
        return 2
    is_live = key.startswith("sk_live_")
    if is_live and not args.live:
        print("REFUSING: key is sk_live_ but --live not given. This creates REAL money-collecting "
              "objects. Re-run with --live once you intend that.", file=sys.stderr)
        return 3
    if not is_live and args.live:
        print("Note: --live given but key is not sk_live_; proceeding in test mode.", file=sys.stderr)

    stripe.api_key = key
    base = args.base_url.rstrip("/")
    thankyou_url = f"{base}/thank-you.html"
    webhook_url = f"{base}/api/stripe/webhook"
    mode = "LIVE" if is_live else "TEST"
    print(f"== provisioning Agentricity checkout in {mode} mode ==")
    print(f"   thank-you: {thankyou_url}")
    print(f"   webhook:   {webhook_url}")

    links = {}
    for sku, cfg in SKUS.items():
        print(f"- {sku}")
        price = _ensure_product_price(sku, cfg)
        links[sku] = _ensure_payment_link(sku, price.id, thankyou_url)

    print("- webhook")
    secret = _ensure_webhook(webhook_url)

    print("\n== RESULT ==")
    print(f"OWN_IT_LINK={links['own-it']}")
    print(f"STAY_CURRENT_LINK={links['stay-current']}")
    print(f"WEBHOOK_SIGNING_SECRET={secret or '(existing endpoint — roll to retrieve)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
