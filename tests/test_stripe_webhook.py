"""Endpoint-level proof for the Stripe webhook recorder (no database required).

Exercises the REAL FastAPI handler in site/agentricity/server/app.py against an injected in-memory
purchase store. The load-bearing property is that the handler only trusts a SIGNATURE-VERIFIED event:

  * a validly-signed event is parsed + appended, and a Stripe retry (same event id) dedups;
  * a tampered body / wrong secret / missing header / stale timestamp is rejected BEFORE any store
    write (fail closed);
  * with no STRIPE_WEBHOOK_SECRET set the endpoint is inert (503, appends nothing);
  * the body is size-capped and the per-IP rate limit applies;
  * the event is mapped to the agentricity_purchases row shape (mode/product/email/github_username).

No live services, so this keeps ``pytest -q`` green everywhere. The DB grant boundary (writer is
INSERT-only + read-blind) is proven separately in test_purchases_grant_boundary.py.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_SITE = Path(__file__).resolve().parents[1] / "site" / "agentricity"
if str(_SITE) not in sys.path:
    sys.path.insert(0, str(_SITE))

from server.app import (  # noqa: E402
    InMemoryPurchaseStore,
    RateLimiter,
    Recorded,
    _extract_purchase,
    create_app,
    verify_stripe_signature,
)

_SECRET = "whsec_test_secret"


def _sign(body: bytes, secret: str = _SECRET, ts: int | None = None) -> str:
    ts = int(time.time()) if ts is None else ts
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def _event(event_id: str = "evt_1", etype: str = "checkout.session.completed", **obj) -> bytes:
    base = {
        "object": "checkout.session", "id": "cs_1", "mode": "payment", "amount_total": 29900,
        "currency": "usd", "customer": "cus_1", "customer_details": {"email": "buyer@example.com"},
        "custom_fields": [{"key": "githubusername", "text": {"value": "octocat"}}],
    }
    base.update(obj)
    return json.dumps({"id": event_id, "type": etype, "livemode": False, "data": {"object": base}}).encode()


@pytest.fixture
def store() -> InMemoryPurchaseStore:
    return InMemoryPurchaseStore()


@pytest.fixture
def client(store: InMemoryPurchaseStore, monkeypatch) -> TestClient:
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", _SECRET)
    app = create_app(purchase_store=store, rate_limiter=RateLimiter(max_hits=1000, window_sec=60))
    return TestClient(app)


def _post(client: TestClient, body: bytes, sig: str | None):
    headers = {"content-type": "application/json"}
    if sig is not None:
        headers["stripe-signature"] = sig
    return client.post("/api/stripe/webhook", content=body, headers=headers)


# --------------------------------------------------------------------------- records + dedup
def test_valid_event_recorded_then_deduped(client: TestClient, store: InMemoryPurchaseStore):
    body = _event("evt_100")
    r1 = _post(client, body, _sign(body))
    assert r1.status_code == 200 and r1.json()["status"] == Recorded.NEW
    # Stripe retry of the SAME event id must dedup (idempotent), not double-append.
    r2 = _post(client, body, _sign(body))
    assert r2.status_code == 200 and r2.json()["status"] == Recorded.DUP
    assert [row["stripe_event_id"] for row in store.rows] == ["evt_100"]
    row = store.rows[0]
    assert row["mode"] == "payment" and row["product"] == "own-it"
    assert row["customer_email"] == "buyer@example.com"
    assert row["github_username"] == "octocat"
    assert row["amount_cents"] == 29900


# --------------------------------------------------------------------------- fail closed
def test_tampered_body_rejected(client: TestClient, store: InMemoryPurchaseStore):
    body = _event("evt_tamper")
    sig = _sign(body)
    r = _post(client, body + b" ", sig)  # body changed after signing
    assert r.status_code == 400 and r.json()["error"] == "bad_signature"
    assert store.rows == []


def test_wrong_secret_rejected(client: TestClient, store: InMemoryPurchaseStore):
    body = _event("evt_wrongsecret")
    r = _post(client, body, _sign(body, secret="whsec_not_it"))
    assert r.status_code == 400
    assert store.rows == []


def test_missing_signature_rejected(client: TestClient, store: InMemoryPurchaseStore):
    body = _event("evt_nosig")
    r = _post(client, body, None)
    assert r.status_code == 400
    assert store.rows == []


def test_stale_timestamp_rejected(client: TestClient, store: InMemoryPurchaseStore):
    body = _event("evt_stale")
    r = _post(client, body, _sign(body, ts=int(time.time()) - 99999))
    assert r.status_code == 400
    assert store.rows == []


def test_bad_event_shape_rejected(client: TestClient, store: InMemoryPurchaseStore):
    body = json.dumps({"data": {"object": {}}}).encode()  # no id / type
    r = _post(client, body, _sign(body))
    assert r.status_code == 400 and r.json()["error"] == "bad_event"
    assert store.rows == []


# --------------------------------------------------------------------------- inert until armed
def test_inert_without_secret(store: InMemoryPurchaseStore, monkeypatch):
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    app = create_app(purchase_store=store, rate_limiter=RateLimiter(max_hits=1000, window_sec=60))
    client = TestClient(app)
    body = _event("evt_inert")
    r = client.post("/api/stripe/webhook", content=body,
                    headers={"content-type": "application/json", "stripe-signature": _sign(body)})
    assert r.status_code == 503 and r.json()["error"] == "not_configured"
    assert store.rows == []


# --------------------------------------------------------------------------- body cap + rate limit
def test_oversized_body_rejected(client: TestClient, store: InMemoryPurchaseStore):
    big = json.dumps({"id": "evt_big", "type": "x", "data": {"object": {"blob": "A" * 70000}}}).encode()
    r = _post(client, big, _sign(big))
    assert r.status_code == 413
    assert store.rows == []


def test_rate_limit_applies(store: InMemoryPurchaseStore, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", _SECRET)
    # The webhook has its OWN limiter (separate budget from /api/signup); inject a tight one.
    app = create_app(purchase_store=store, webhook_rate_limiter=RateLimiter(max_hits=2, window_sec=60))
    client = TestClient(app)
    codes = []
    for i in range(4):
        body = _event(f"evt_rl_{i}")
        codes.append(_post(client, body, _sign(body)).status_code)
    assert codes[:2] == [200, 200]
    assert 429 in codes[2:]


# --------------------------------------------------------------------------- arm-gate completeness
def test_armed_requires_durable_store(monkeypatch):
    """A signing secret WITHOUT a durable store must NOT accept events: recording a real paid event
    into the volatile in-memory fallback + returning 200 makes Stripe stop retrying -> silent loss."""
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", _SECRET)
    monkeypatch.delenv("PURCHASES_DB_DSN", raising=False)
    # No purchase_store injected -> built from env -> in-memory fallback -> NOT durable.
    app = create_app(webhook_rate_limiter=RateLimiter(max_hits=1000, window_sec=60))
    client = TestClient(app)
    body = _event("evt_not_durable")
    r = client.post("/api/stripe/webhook", content=body,
                    headers={"content-type": "application/json", "stripe-signature": _sign(body)})
    assert r.status_code == 503 and r.json()["error"] == "not_configured"


def test_tolerance_zero_is_strict():
    """tolerance_sec=0 means EXACT timestamp only (strict), not 'disable the replay check'."""
    body = b'{"id":"e","type":"t"}'
    assert verify_stripe_signature(body, _sign(body, ts=1000), _SECRET, tolerance_sec=0, now=1000.0) is True
    assert verify_stripe_signature(body, _sign(body, ts=999), _SECRET, tolerance_sec=0, now=1000.0) is False


# --------------------------------------------------------------------------- event mapping units
def test_extract_subscription_and_refund():
    sub = {"id": "evt_s", "type": "customer.subscription.deleted", "livemode": True,
           "data": {"object": {"object": "subscription", "id": "sub_9", "customer": "cus_9"}}}
    rec = _extract_purchase(sub)
    assert rec["mode"] == "subscription" and rec["product"] == "stay-current"
    assert rec["subscription_id"] == "sub_9" and rec["livemode"] is True

    charge = {"id": "evt_c", "type": "charge.refunded",
              "data": {"object": {"object": "charge", "amount_refunded": 29900,
                                  "billing_details": {"email": "r@example.com"}}}}
    rec = _extract_purchase(charge)
    assert rec["mode"] == "payment" and rec["amount_cents"] == 29900
    assert rec["customer_email"] == "r@example.com"


def test_verify_signature_unit():
    body = b'{"id":"evt_u","type":"t"}'
    assert verify_stripe_signature(body, _sign(body), _SECRET) is True
    assert verify_stripe_signature(body, _sign(body, secret="x"), _SECRET) is False
    assert verify_stripe_signature(body, None, _SECRET) is False
    assert verify_stripe_signature(body, "garbage", _SECRET) is False
    assert verify_stripe_signature(body, _sign(body), "") is False
