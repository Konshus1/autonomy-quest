"""Minimal, safe signup-capture service for the Agentricity marketing site.

ONE container serves the static one-pager (site/agentricity/) AND accepts the early-access email
capture at ``POST /api/signup``. It is deliberately tiny and self-contained so it can deploy as a
single isolated Azure ACA app (never prod talkingback).

Security posture (the untrusted public internet hits /api/signup):
  * The DB credential lives ONLY in the server env (``SIGNUPS_DB_DSN``); it is never in the page or
    client JS. The static page POSTs same-origin to /api/signup.
  * That credential is a dedicated, INSERT-ONLY, read-BLIND role (``agentricity_signups_writer``, see
    schema/033_signups.sql). A leak of it can append, not read the list, not mutate it.
  * Email is validated + normalized; the request body is size-capped; writes are parameterized
    (no SQL injection); a basic per-IP rate limit throttles abuse; secrets are never echoed.
  * Dedup is ``INSERT ... ON CONFLICT (email) DO NOTHING`` so the JSON result can honestly say
    whether the address was newly *recorded* or was *already* on the list.

The app is built via ``create_app(store=...)`` so tests can inject an in-memory store and exercise
the real endpoint logic (records + dedup + validation + rate limit + static paths) with no database,
while production wires the Postgres-backed writer.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional, Protocol

logger = logging.getLogger("agentricity.signup")

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

# ---------------------------------------------------------------------------
# Configuration (all overridable by env; safe defaults).
# ---------------------------------------------------------------------------
SITE_ROOT = Path(__file__).resolve().parents[1]          # site/agentricity/
MAX_BODY_BYTES = int(os.environ.get("SIGNUP_MAX_BODY_BYTES", "2048"))
MAX_EMAIL_LEN = 254                                       # RFC 5321 upper bound
RATE_LIMIT_MAX = int(os.environ.get("SIGNUP_RATE_LIMIT_MAX", "10"))
RATE_LIMIT_WINDOW_SEC = float(os.environ.get("SIGNUP_RATE_LIMIT_WINDOW_SEC", "60"))
# Stripe webhook events are larger than a signup POST (a Checkout Session is several KB), so the
# webhook gets its own body cap. Signature tolerance bounds replay of a captured-but-old event.
STRIPE_WEBHOOK_MAX_BODY_BYTES = int(os.environ.get("STRIPE_WEBHOOK_MAX_BODY_BYTES", "65536"))
STRIPE_WEBHOOK_TOLERANCE_SEC = int(os.environ.get("STRIPE_WEBHOOK_TOLERANCE_SEC", "300"))
# The webhook gets its OWN rate budget: legitimate Stripe bursts (renewal waves, post-outage retry
# storms) share one source IP and would trip the signup-sized limiter, 429'ing real paid events.
STRIPE_WEBHOOK_RATE_LIMIT_MAX = int(os.environ.get("STRIPE_WEBHOOK_RATE_LIMIT_MAX", "120"))
STRIPE_WEBHOOK_RATE_LIMIT_WINDOW_SEC = float(os.environ.get("STRIPE_WEBHOOK_RATE_LIMIT_WINDOW_SEC", "60"))
# Cap on any single string field written to the purchase ledger (defense-in-depth; the columns are
# unbounded text, and even a signature-holder shouldn't be able to bloat a row).
PURCHASE_FIELD_MAX = int(os.environ.get("PURCHASE_FIELD_MAX", "400"))

# Conservative single-line email check. We are not trying to fully parse RFC 5322; we reject
# anything with whitespace/control chars, enforce a single '@', a dotted domain, and length caps.
_EMAIL_RE = re.compile(r"^[^\s@]{1,64}@[^\s@]+\.[^\s@]{2,}$")


class Recorded:
    """Sentinel-ish string constants for the honest result the front-end shows."""

    NEW = "recorded"
    DUP = "already"


def normalize_email(raw: object) -> Optional[str]:
    """Return a normalized email, or None if it is not a plausibly-valid address.

    Rejects non-strings, control chars/newlines (header-injection guard), over-length input, and
    anything failing the shape check. Lowercases + strips so dedup is case-insensitive.
    """
    if not isinstance(raw, str):
        return None
    email = raw.strip().lower()
    if not email or len(email) > MAX_EMAIL_LEN:
        return None
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in email):  # control chars incl. \r \n \t
        return None
    if not _EMAIL_RE.match(email):
        return None
    return email


# ---------------------------------------------------------------------------
# Store interface + implementations.
# ---------------------------------------------------------------------------
class SignupStore(Protocol):
    def record(self, email: str, source: Optional[str], ip: Optional[str], ua: Optional[str]) -> str:
        """Append a signup; return Recorded.NEW if newly stored, Recorded.DUP if already present."""
        ...


class InMemoryStore:
    """Thread-safe in-memory store with the SAME dedup semantics as the Postgres one.

    Used by tests (and as a no-DB local fallback) so the endpoint's records/dedup behaviour is
    provable without a database. Not durable — process-local only.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._emails: set[str] = set()
        self.rows: list[dict] = []

    def record(self, email: str, source: Optional[str], ip: Optional[str], ua: Optional[str]) -> str:
        with self._lock:
            if email in self._emails:
                return Recorded.DUP
            self._emails.add(email)
            self.rows.append({"email": email, "source": source, "ip": ip, "ua": ua})
            return Recorded.NEW


class PostgresStore:
    """Writes via the dedicated INSERT-only ``agentricity_signups_writer`` credential (SIGNUPS_DB_DSN).

    A fresh short-lived autocommit connection per write keeps the service stateless and avoids a
    pool that could pin a leaked credential open. Two choices keep the writer INSERT-ONLY and fully
    read-blind (no SELECT grant at all):
      * dedup is read from ``cur.rowcount`` (the INSERT command tag: 1 on insert, 0 on conflict),
        NOT ``RETURNING`` — RETURNING would require SELECT on the returned columns;
      * the conflict clause is the BARE ``ON CONFLICT DO NOTHING`` (no ``(email)`` target) —
        naming the arbiter column would require SELECT on it. ``agentricity_signups`` has exactly one unique
        constraint (email), so the bare form dedups on it.
    """

    def __init__(self, dsn: str) -> None:
        import psycopg2  # imported lazily so the no-DB test path needs no live driver config

        self._psycopg2 = psycopg2
        self._dsn = dsn

    def record(self, email: str, source: Optional[str], ip: Optional[str], ua: Optional[str]) -> str:
        conn = self._psycopg2.connect(self._dsn)
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO agentricity_signups (email, source, ip, ua) VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT DO NOTHING",
                    (email, source, ip, ua),
                )
                inserted = cur.rowcount == 1
            return Recorded.NEW if inserted else Recorded.DUP
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Purchase ledger store (Stripe webhook -> append-only agentricity_purchases). Same read-blind discipline as the
# signup store: INSERT-only, dedup via rowcount (bare ON CONFLICT DO NOTHING, no RETURNING).
# ---------------------------------------------------------------------------
_PURCHASE_COLUMNS = (
    "stripe_event_id", "event_type", "mode", "product", "stripe_session_id", "stripe_customer",
    "subscription_id", "customer_email", "github_username", "amount_cents", "currency", "livemode",
    "payload_summary",
)


class PurchaseStore(Protocol):
    def record(self, purchase: dict) -> str:
        """Append one verified Stripe event; NEW if newly stored, DUP if event_id already present."""
        ...


class InMemoryPurchaseStore:
    """Thread-safe in-memory purchase ledger with the SAME dedup semantics as the Postgres one
    (dedup on ``stripe_event_id``). Used by tests and as a no-DB dev fallback. Not durable."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._event_ids: set[str] = set()
        self.rows: list[dict] = []

    def record(self, purchase: dict) -> str:
        eid = purchase.get("stripe_event_id")
        with self._lock:
            if eid in self._event_ids:
                return Recorded.DUP
            self._event_ids.add(eid)
            self.rows.append(dict(purchase))
            return Recorded.NEW


class PostgresPurchaseStore:
    """Writes via the dedicated INSERT-only ``agentricity_purchases_writer`` credential (PURCHASES_DB_DSN).

    Mirrors ``PostgresStore``: a fresh autocommit connection per write, dedup read from ``rowcount``
    (bare ``ON CONFLICT DO NOTHING`` on the sole unique constraint ``stripe_event_id`` — no RETURNING,
    which would need SELECT the read-blind writer is denied)."""

    def __init__(self, dsn: str) -> None:
        import psycopg2  # imported lazily so the no-DB test path needs no live driver config

        self._psycopg2 = psycopg2
        self._dsn = dsn

    def record(self, purchase: dict) -> str:
        import json

        cols = ", ".join(_PURCHASE_COLUMNS)
        # payload_summary is the only jsonb column; cast its text param. The rest are plain params.
        placeholders = ", ".join("%s::jsonb" if c == "payload_summary" else "%s" for c in _PURCHASE_COLUMNS)
        values = []
        for c in _PURCHASE_COLUMNS:
            v = purchase.get(c)
            values.append(json.dumps(v) if c == "payload_summary" else v)
        conn = self._psycopg2.connect(self._dsn)
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO agentricity_purchases ({cols}) VALUES ({placeholders}) ON CONFLICT DO NOTHING",
                    values,
                )
                inserted = cur.rowcount == 1
            return Recorded.NEW if inserted else Recorded.DUP
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Basic per-IP rate limiter (sliding window, in-memory).
# ---------------------------------------------------------------------------
class RateLimiter:
    def __init__(self, max_hits: int, window_sec: float) -> None:
        self._max = max_hits
        self._window = window_sec
        self._lock = threading.Lock()
        self._hits: dict[str, list[float]] = {}
        self._last_sweep = 0.0

    def _sweep(self, now: float) -> None:
        """Drop keys with no hit inside the current window so the map can't grow without bound.

        The rate-limit key is client-influenced (X-Forwarded-For; see _client_ip), so an attacker
        can mint unbounded distinct keys. Without eviction that is a memory-exhaustion DoS. We sweep
        at most once per window (O(n) but amortized cheap) under the same lock as allow().
        """
        window_start = now - self._window
        stale = [k for k, v in self._hits.items() if not v or v[-1] < window_start]
        for k in stale:
            del self._hits[k]
        self._last_sweep = now

    def allow(self, key: str, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            if now - self._last_sweep >= self._window:
                self._sweep(now)
            window_start = now - self._window
            hits = [t for t in self._hits.get(key, ()) if t >= window_start]
            if len(hits) >= self._max:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True


# Number of TRUSTED reverse-proxy hops in front of this app (env, default 0). X-Forwarded-For is
# client-appendable, so the LEFTMOST entry is attacker-controlled and must never be trusted. With N
# trusted hops the real client is the Nth entry FROM THE RIGHT (the address the trusted proxy set),
# which the attacker cannot shift by prepending spoofed entries. Default 0 => ignore XFF entirely and
# use the unspoofable socket peer (coarser but safe). For Azure Container Apps set this to 1.
_TRUSTED_PROXY_HOPS = max(0, int(os.environ.get("AQ_SIGNUP_TRUSTED_PROXY_HOPS", "0") or "0"))


def _client_ip(request: Request) -> str:
    """A rate-limit key that a public client cannot spoof (coarse limiting only — never auth)."""
    peer = request.client.host if request.client else "unknown"
    if _TRUSTED_PROXY_HOPS <= 0:
        return peer  # safe default: trust nothing client-supplied
    xff = request.headers.get("x-forwarded-for")
    if not xff:
        return peer
    parts = [p.strip() for p in xff.split(",") if p.strip()]
    # The rightmost trusted-proxy-set address; fail safe to the peer if there are fewer hops than expected.
    return parts[-_TRUSTED_PROXY_HOPS] if len(parts) >= _TRUSTED_PROXY_HOPS else peer


def _truncate(value: Optional[str], limit: int) -> Optional[str]:
    if value is None:
        return None
    return value[:limit]


# ---------------------------------------------------------------------------
# Stripe webhook: signature verify (stdlib, no SDK dependency) + safe event extraction.
# ---------------------------------------------------------------------------
def verify_stripe_signature(
    raw_body: bytes,
    sig_header: Optional[str],
    secret: str,
    tolerance_sec: int = STRIPE_WEBHOOK_TOLERANCE_SEC,
    now: Optional[float] = None,
) -> bool:
    """Verify Stripe's ``Stripe-Signature`` header against the raw body. Fails CLOSED.

    Stripe signs ``"{t}.{raw_body}"`` with HMAC-SHA256 keyed by the endpoint's signing secret and sends
    ``t=<unixts>,v1=<hex>[,v1=<hex>...]``. We recompute and constant-time compare against every ``v1``,
    and reject a timestamp outside ``tolerance_sec`` (replay bound). Any missing/garbled input -> False.
    """
    if not secret or not sig_header:
        return False
    now = time.time() if now is None else now
    ts: Optional[int] = None
    v1s: list[str] = []
    for part in sig_header.split(","):
        key, sep, val = part.strip().partition("=")
        if not sep:
            continue
        if key == "t":
            try:
                ts = int(val)
            except ValueError:
                return False
        elif key == "v1":
            v1s.append(val)
    if ts is None or not v1s:
        return False
    # Always enforce the replay window. Note 0 means "exact timestamp only" (strict), NOT "disabled" —
    # a falsy-guard here would silently turn the intuitive stricter setting into a no-op.
    if abs(now - ts) > max(0, tolerance_sec):
        return False
    signed = f"{ts}.".encode("utf-8") + raw_body
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, v) for v in v1s)


def _stripe_custom_field(custom_fields: object, wanted: tuple) -> Optional[str]:
    """Pull a value (e.g. GitHub username) out of a Checkout Session's ``custom_fields`` list."""
    if not isinstance(custom_fields, list):
        return None
    for cf in custom_fields:
        if not isinstance(cf, dict):
            continue
        if (cf.get("key") or "").lower() in wanted:
            for tkey in ("text", "dropdown", "numeric"):
                sub = cf.get(tkey)
                if isinstance(sub, dict) and sub.get("value"):
                    return str(sub["value"])[:100]
    return None


def _extract_purchase(event: dict) -> dict:
    """Map a verified Stripe event to the ``agentricity_purchases`` row shape, defensively (all ``.get``).

    Records EVERY verified event type as a ledger row — current entitlement is derived downstream by
    reading the ledger, not by mutating state here. Never stores card data or the full event object.
    """
    obj = (event.get("data") or {}).get("object") or {}
    obj_type = obj.get("object")  # 'checkout.session' | 'subscription' | 'charge' | 'invoice'
    rec: dict = {c: None for c in _PURCHASE_COLUMNS}
    rec["stripe_event_id"] = event.get("id")
    rec["event_type"] = event.get("type")
    rec["livemode"] = event.get("livemode")
    rec["stripe_customer"] = obj.get("customer")
    rec["currency"] = obj.get("currency")

    if obj_type == "checkout.session":
        rec["mode"] = obj.get("mode")
        rec["stripe_session_id"] = obj.get("id")
        rec["subscription_id"] = obj.get("subscription")
        rec["amount_cents"] = obj.get("amount_total")
        details = obj.get("customer_details") if isinstance(obj.get("customer_details"), dict) else {}
        rec["customer_email"] = obj.get("customer_email") or details.get("email")
        rec["github_username"] = _stripe_custom_field(
            obj.get("custom_fields"), ("github", "githubusername", "github_username")
        )
    elif obj_type == "subscription":
        rec["mode"] = "subscription"
        rec["subscription_id"] = obj.get("id")
    elif obj_type == "charge":
        rec["mode"] = "payment"
        rec["amount_cents"] = obj.get("amount_refunded") or obj.get("amount")
        billing = obj.get("billing_details") if isinstance(obj.get("billing_details"), dict) else {}
        rec["customer_email"] = billing.get("email")
    elif obj_type == "invoice":
        rec["subscription_id"] = obj.get("subscription")
        rec["amount_cents"] = obj.get("amount_paid")
        rec["customer_email"] = obj.get("customer_email")

    # Coarse product label from the payment mode (refine later with real price ids if needed).
    if rec["mode"] == "payment":
        rec["product"] = "own-it"
    elif rec["mode"] == "subscription":
        rec["product"] = "stay-current"

    rec["payload_summary"] = {
        "object": obj_type,
        "id": obj.get("id"),
        "status": obj.get("status"),
        "payment_status": obj.get("payment_status"),
    }

    # Defense-in-depth: cap every string field (github_username is already capped in the helper) so a
    # signature-holder can't bloat a ledger row past the "small safe subset" the columns are meant for.
    for key, val in rec.items():
        if isinstance(val, str):
            rec[key] = val[:PURCHASE_FIELD_MAX]
    rec["payload_summary"] = {
        k: (v[:PURCHASE_FIELD_MAX] if isinstance(v, str) else v)
        for k, v in rec["payload_summary"].items()
    }
    return rec


class _PublicStaticFiles(StaticFiles):
    """StaticFiles that serves ONLY public site assets.

    The static root (site/agentricity/) also contains the service's own source (server/), the
    Dockerfile, and DEPLOY.md — which get COPY'd into the served directory in the image. Without this
    guard they are reachable over the public internet (e.g. GET /server/app.py, GET /DEPLOY.md),
    disclosing implementation + the deploy's env-var/role names. No credential lives in those files
    (the DSN is env-only), but the disclosure is needless attacker reconnaissance. We 404 the internal
    paths while leaving every public path — index.html, js/, assets/, the EXACT legal .html paths,
    /legal/*, /app-ads.txt — untouched.
    """

    def _is_internal(self, path: str) -> bool:
        p = path.replace("\\", "/").lstrip("/").lower()
        first = p.split("/", 1)[0]
        if first == "server":
            return True
        base = p.rsplit("/", 1)[-1]
        return base == "dockerfile" or p.endswith(".py") or p.endswith(".md")

    async def get_response(self, path: str, scope):
        if self._is_internal(path):
            from starlette.exceptions import HTTPException

            raise HTTPException(status_code=404)
        return await super().get_response(path, scope)


# ---------------------------------------------------------------------------
# App factory.
# ---------------------------------------------------------------------------
def create_app(
    store: Optional[SignupStore] = None,
    *,
    site_root: Optional[Path] = None,
    rate_limiter: Optional[RateLimiter] = None,
    purchase_store: Optional[PurchaseStore] = None,
    webhook_rate_limiter: Optional[RateLimiter] = None,
) -> FastAPI:
    root = site_root or SITE_ROOT
    limiter = rate_limiter or RateLimiter(RATE_LIMIT_MAX, RATE_LIMIT_WINDOW_SEC)
    webhook_limiter = webhook_rate_limiter or RateLimiter(
        STRIPE_WEBHOOK_RATE_LIMIT_MAX, STRIPE_WEBHOOK_RATE_LIMIT_WINDOW_SEC
    )

    if store is None:
        dsn = os.environ.get("SIGNUPS_DB_DSN")
        store = PostgresStore(dsn) if dsn else InMemoryStore()

    if purchase_store is None:
        pdsn = os.environ.get("PURCHASES_DB_DSN")
        purchase_store = PostgresPurchaseStore(pdsn) if pdsn else InMemoryPurchaseStore()
        # Durable only when a real DSN is wired. If a caller injects a store (tests), they own its
        # durability. This flag closes the silent-loss gap: armed REQUIRES a durable store (below).
        purchase_store_durable = bool(pdsn)
    else:
        purchase_store_durable = True

    app = FastAPI(title="Agentricity signup service", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.state.purchase_store = purchase_store
    app.state.purchase_store_durable = purchase_store_durable

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"ok": True})

    @app.post("/api/signup")
    async def signup(request: Request) -> JSONResponse:
        # 1) Rate-limit per IP before doing any work.
        if not limiter.allow(_client_ip(request)):
            return JSONResponse(
                {"ok": False, "error": "rate_limited", "message": "Too many requests — try again shortly."},
                status_code=429,
            )

        # 2) Cap the body size (both the declared length and the actual bytes).
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > MAX_BODY_BYTES:
                    return JSONResponse({"ok": False, "error": "payload_too_large"}, status_code=413)
            except ValueError:
                return JSONResponse({"ok": False, "error": "bad_request"}, status_code=400)
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            return JSONResponse({"ok": False, "error": "payload_too_large"}, status_code=413)

        # 3) Parse JSON defensively.
        try:
            import json

            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            return JSONResponse({"ok": False, "error": "bad_json"}, status_code=400)
        if not isinstance(payload, dict):
            return JSONResponse({"ok": False, "error": "bad_json"}, status_code=400)

        # 4) Validate + normalize the email.
        email = normalize_email(payload.get("email"))
        if email is None:
            return JSONResponse(
                {"ok": False, "error": "invalid_email", "message": "Enter a valid email address."},
                status_code=422,
            )

        source = _truncate(payload.get("source") if isinstance(payload.get("source"), str) else None, 120)
        ip = _client_ip(request)
        ua = _truncate(request.headers.get("user-agent"), 400)

        # 5) Append (parameterized) + honest recorded/already result. Never echo the DB error.
        try:
            result = request.app.state.store.record(email, source, ip, ua)
        except Exception as exc:
            # Log server-side for ops (never leaked to the client). The class + message of the store
            # error is what distinguishes a network/firewall timeout from an auth/SSL rejection.
            logger.error("signup store_unavailable: %s: %s", type(exc).__name__, exc)
            return JSONResponse(
                {"ok": False, "error": "store_unavailable", "message": "We couldn't save that just now."},
                status_code=503,
            )

        if result == Recorded.NEW:
            message = "You're on the early-access list. We onboard in batches — watch your inbox."
        else:
            message = "You're already on the early-access list. We onboard in batches — watch your inbox."
        return JSONResponse({"ok": True, "status": result, "message": message}, status_code=200)

    @app.post("/api/stripe/webhook")
    async def stripe_webhook(request: Request) -> JSONResponse:
        # Stripe POSTs verified purchase events here. The container holds ONLY the signing secret
        # (STRIPE_WEBHOOK_SECRET) — never STRIPE_SECRET_KEY — so a leak cannot move money. Order:
        # rate-limit -> require ARMED (secret AND durable store) -> bound+read body -> VERIFY SIGNATURE
        # (fail closed) -> parse -> append to the read-blind purchase ledger (dedup on event id).
        if not webhook_limiter.allow(_client_ip(request)):
            return JSONResponse({"ok": False, "error": "rate_limited"}, status_code=429)

        # ARMED requires BOTH a signing secret AND a durable store. With only the secret, a real event
        # would verify, land in the volatile in-memory fallback, and 200 -> Stripe never retries -> a
        # paid event is silently lost. Refuse (append nothing) unless both halves are configured.
        # STRIPE_WEBHOOK_SECRET may be a COMMA-SEPARATED list so ONE endpoint verifies both test-mode
        # and live-mode events (their signing secrets differ) — also the standard secret-rotation shape.
        # Test events land tagged livemode=false, so the ledger self-segregates from real purchases.
        signing_secrets = [s.strip() for s in (os.environ.get("STRIPE_WEBHOOK_SECRET") or "").split(",") if s.strip()]
        if not signing_secrets or not request.app.state.purchase_store_durable:
            return JSONResponse({"ok": False, "error": "not_configured"}, status_code=503)

        # Bound memory BEFORE buffering: reject an oversized declared Content-Length, then stream-read
        # with a hard cap so a lying/absent Content-Length (e.g. chunked) can't buffer an unbounded
        # body into RAM and OOM the container (which also serves the static site + /api/signup).
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > STRIPE_WEBHOOK_MAX_BODY_BYTES:
                    return JSONResponse({"ok": False, "error": "payload_too_large"}, status_code=413)
            except ValueError:
                return JSONResponse({"ok": False, "error": "bad_request"}, status_code=400)
        raw = b""
        async for chunk in request.stream():
            raw += chunk
            if len(raw) > STRIPE_WEBHOOK_MAX_BODY_BYTES:
                return JSONResponse({"ok": False, "error": "payload_too_large"}, status_code=413)

        sig_header = request.headers.get("stripe-signature")
        if not any(verify_stripe_signature(raw, sig_header, s) for s in signing_secrets):
            return JSONResponse({"ok": False, "error": "bad_signature"}, status_code=400)

        try:
            import json

            event = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, RecursionError):
            return JSONResponse({"ok": False, "error": "bad_json"}, status_code=400)
        if not isinstance(event, dict) or not event.get("id") or not event.get("type"):
            return JSONResponse({"ok": False, "error": "bad_event"}, status_code=400)

        try:
            result = request.app.state.purchase_store.record(_extract_purchase(event))
        except Exception as exc:  # noqa: BLE001
            # Log server-side; return 503 so Stripe retries (the event is not yet durably recorded).
            logger.error("stripe webhook store_unavailable: %s: %s", type(exc).__name__, exc)
            return JSONResponse({"ok": False, "error": "store_unavailable"}, status_code=503)

        return JSONResponse({"ok": True, "status": result}, status_code=200)

    # Static site LAST so the explicit routes above win. StaticFiles(html=True) serves index.html at
    # "/" and every legal page at its EXACT path (/privacy.html, /terms.html, /refund.html,
    # /legal/*, /app-ads.txt) — a hard app-store requirement.
    app.mount("/", _PublicStaticFiles(directory=str(root), html=True), name="site")
    return app


# Uvicorn entrypoint: `uvicorn server.app:app` (see Dockerfile).
app = create_app()
