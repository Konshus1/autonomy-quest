"""Endpoint-level proof for the Agentricity signup service (no database required).

Exercises the REAL FastAPI handler in site/agentricity/server/app.py against an injected in-memory
store whose dedup semantics mirror the Postgres ``ON CONFLICT (email) DO NOTHING``. Covers:

  * records a signup, then dedups an identical re-submit (honest recorded vs already result);
  * invalid / oversized / injection-shaped emails are rejected before any store write;
  * the request body is size-capped;
  * a basic per-IP rate limit throttles abuse;
  * the static legal pages serve at their EXACT app-store paths.

These run with no live services, so they keep ``pytest -q`` green everywhere. The DB grant boundary
(writer is INSERT-only + read-blind) is proven separately in test_signups_grant_boundary.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Make site/agentricity/ importable so `server.app` resolves.
_SITE = Path(__file__).resolve().parents[1] / "site" / "agentricity"
if str(_SITE) not in sys.path:
    sys.path.insert(0, str(_SITE))

from server.app import InMemoryStore, RateLimiter, Recorded, create_app, normalize_email  # noqa: E402


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def client(store: InMemoryStore) -> TestClient:
    # Generous rate limit so functional tests aren't throttled; a dedicated test injects a tight one.
    app = create_app(store=store, rate_limiter=RateLimiter(max_hits=1000, window_sec=60))
    return TestClient(app)


# --------------------------------------------------------------------------- records + dedup
def test_signup_records_then_dedups(client: TestClient, store: InMemoryStore):
    r1 = client.post("/api/signup", json={"email": "Alice@Example.com", "source": "agentricity.com"})
    assert r1.status_code == 200
    body1 = r1.json()
    assert body1["ok"] is True and body1["status"] == Recorded.NEW

    # Re-submit the SAME address (different case + whitespace) — must dedup, not double-store.
    r2 = client.post("/api/signup", json={"email": "  alice@example.com  "})
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["ok"] is True and body2["status"] == Recorded.DUP

    # Exactly one normalized row landed.
    assert [row["email"] for row in store.rows] == ["alice@example.com"]


# --------------------------------------------------------------------------- validation
@pytest.mark.parametrize(
    "email",
    [
        "",
        "not-an-email",
        "no-at-sign.com",
        "spaces in@example.com",
        "a@b",                       # no dotted TLD
        "user@example.com\nBcc: evil@x.com",   # header/newline injection
        "user@example.com\r\nDATA",            # CRLF injection
        "'; DROP TABLE signups;--@x.com",      # sql-injection shaped (also has spaces)
        "a@" + "x" * 300 + ".com",             # over-length domain
    ],
)
def test_invalid_email_rejected(client: TestClient, store: InMemoryStore, email: str):
    r = client.post("/api/signup", json={"email": email})
    assert r.status_code == 422
    assert r.json()["ok"] is False
    assert store.rows == []  # nothing was written


def test_missing_email_rejected(client: TestClient, store: InMemoryStore):
    r = client.post("/api/signup", json={"source": "x"})
    assert r.status_code == 422
    assert store.rows == []


def test_bad_json_rejected(client: TestClient):
    r = client.post("/api/signup", content=b"{not json", headers={"content-type": "application/json"})
    assert r.status_code == 400


def test_normalize_email_unit():
    assert normalize_email("  Foo@Bar.COM ") == "foo@bar.com"
    assert normalize_email("x@y.z\n") is None
    assert normalize_email(12345) is None
    assert normalize_email("a" * 60 + "@ok.com") == ("a" * 60 + "@ok.com")


# --------------------------------------------------------------------------- body size cap
def test_oversized_body_rejected(client: TestClient, store: InMemoryStore):
    big = {"email": "a@b.com", "source": "S" * 5000}  # well over the 2048-byte cap
    r = client.post("/api/signup", json=big)
    assert r.status_code == 413
    assert store.rows == []


# --------------------------------------------------------------------------- rate limit
def test_rate_limit_per_ip(store: InMemoryStore):
    app = create_app(store=store, rate_limiter=RateLimiter(max_hits=2, window_sec=60))
    client = TestClient(app)
    assert client.post("/api/signup", json={"email": "a@ex.com"}).status_code == 200
    assert client.post("/api/signup", json={"email": "b@ex.com"}).status_code == 200
    # third request from the same client IP within the window is throttled
    r = client.post("/api/signup", json={"email": "c@ex.com"})
    assert r.status_code == 429
    assert r.json()["error"] == "rate_limited"


def test_rate_limit_not_bypassable_by_spoofed_xff(store: InMemoryStore):
    """Safe default (AQ_SIGNUP_TRUSTED_PROXY_HOPS unset/0): the rate-limit key is the unspoofable
    socket peer, so rotating a client-supplied X-Forwarded-For per request does NOT evade the limit."""
    app = create_app(store=store, rate_limiter=RateLimiter(max_hits=2, window_sec=60))
    client = TestClient(app)
    codes = [
        client.post("/api/signup", json={"email": f"x{i}@ex.com"},
                    headers={"X-Forwarded-For": f"9.9.9.{i}"}).status_code
        for i in range(5)
    ]
    # first two allowed, the rest throttled despite each carrying a different spoofed XFF
    assert codes[:2] == [200, 200]
    assert 429 in codes[2:], f"spoofed XFF evaded the limit: {codes}"


# --------------------------------------------------------------------------- static + legal paths
@pytest.mark.parametrize(
    "path",
    ["/", "/privacy.html", "/terms.html", "/refund.html", "/app-ads.txt", "/legal/privacy.html", "/js/main.js"],
)
def test_static_and_legal_paths_served(client: TestClient, path: str):
    r = client.get(path)
    assert r.status_code == 200, f"{path} did not serve (got {r.status_code})"
    assert len(r.content) > 0


def test_healthz(client: TestClient):
    assert client.get("/healthz").json() == {"ok": True}


# --------------------------------------------------------------------------- internal files not served
@pytest.mark.parametrize(
    "path",
    [
        "/server/app.py",          # service source
        "/server/requirements.txt",
        "/server/__init__.py",
        "/Dockerfile",
        "/DEPLOY.md",
        "/README.md",
    ],
)
def test_internal_files_not_public(client: TestClient, path: str):
    """The static root also holds the service source + deploy docs; they must NOT be web-reachable."""
    r = client.get(path)
    assert r.status_code == 404, f"{path} leaked over HTTP ({r.status_code})"


# --------------------------------------------------------------------------- rate-limiter memory
def test_rate_limiter_does_not_leak_keys():
    """The per-IP map must not grow without bound: keys with no live hits get swept.

    Otherwise an attacker who controls the rate-limit key (e.g. by spoofing X-Forwarded-For, which
    _client_ip trusts) can create unbounded distinct keys and exhaust process memory.
    """
    rl = RateLimiter(max_hits=1, window_sec=10)
    for i in range(5000):
        rl.allow(f"ip-{i}", now=0.0)  # 5000 distinct keys, all within one instant
    # A single call far past the window must let the limiter forget the now-stale keys.
    rl.allow("fresh", now=100000.0)
    assert len(rl._hits) < 100, f"limiter leaked keys: {len(rl._hits)} retained after window"
