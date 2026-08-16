#!/usr/bin/env python3
"""Repoint the Agentricity Stripe objects (webhook URL + Payment Link redirects) to a new base URL.

Run once per mode after the agentricity.com domain cutover, so Stripe delivers webhooks to the domain
and buyers land on the domain's thank-you page instead of the raw Azure URL.

    STRIPE_API_KEY=sk_test_... python3 scripts/stripe_repoint_agentricity.py --base-url https://agentricity.com
    STRIPE_API_KEY=sk_live_... python3 scripts/stripe_repoint_agentricity.py --base-url https://agentricity.com --live

Idempotent: matches our objects by metadata (agentricity_sku / agentricity) and only updates the URL
fields. The buy.stripe.com Payment Link URLs themselves do NOT change.
"""
from __future__ import annotations

import argparse
import os
import sys

import stripe


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()
    key = os.environ.get("STRIPE_API_KEY")
    if not key:
        print("STRIPE_API_KEY not set", file=sys.stderr)
        return 2
    if key.startswith("sk_live_") and not args.live:
        print("REFUSING: sk_live_ key without --live", file=sys.stderr)
        return 3
    stripe.api_key = key
    base = args.base_url.rstrip("/")
    thankyou, webhook = f"{base}/thank-you.html", f"{base}/api/stripe/webhook"
    mode = "LIVE" if key.startswith("sk_live_") else "TEST"
    print(f"== repointing {mode} objects to {base} ==")

    for we in stripe.WebhookEndpoint.list(limit=100).auto_paging_iter():
        if (we.metadata or {}).get("agentricity") == "purchases" or "/api/stripe/webhook" in we.url:
            if we.url != webhook:
                stripe.WebhookEndpoint.modify(we.id, url=webhook)
                print(f"  webhook {we.id}: -> {webhook}")
            else:
                print(f"  webhook {we.id}: already {webhook}")

    for pl in stripe.PaymentLink.list(active=True, limit=100).auto_paging_iter():
        if (pl.metadata or {}).get("agentricity_sku"):
            ac = pl.after_completion
            cur = (ac.get("redirect") or {}).get("url") if ac else None
            if cur != thankyou:
                stripe.PaymentLink.modify(
                    pl.id, after_completion={"type": "redirect", "redirect": {"url": thankyou}})
                print(f"  payment link {pl.id} ({pl.metadata['agentricity_sku']}): redirect -> {thankyou}")
            else:
                print(f"  payment link {pl.id}: redirect already {thankyou}")
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
