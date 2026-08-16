"""Minimal, safe signup-capture service for the Agentricity marketing site.

ONE container serves the static one-pager (site/agentricity/) AND accepts the early-access email
capture at ``POST /api/signup``. It is deliberately tiny and self-contained so it can deploy as a
single isolated Azure ACA app (never prod talkingback).

Security posture (the untrusted public internet hits /api/signup):
  * The DB credential lives ONLY in the server env (``SIGNUPS_DB_DSN``); it is never in the page or
    client JS. The static page POSTs same-origin to /api/signup.
  * That credential is a dedicated, INSERT-ONLY, read-BLIND role (``aq_signups_writer``, see
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

import os
import re
import threading
import time
from pathlib import Path
from typing import Optional, Protocol

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
    """Writes via the dedicated INSERT-only ``aq_signups_writer`` credential (SIGNUPS_DB_DSN).

    A fresh short-lived autocommit connection per write keeps the service stateless and avoids a
    pool that could pin a leaked credential open. Two choices keep the writer INSERT-ONLY and fully
    read-blind (no SELECT grant at all):
      * dedup is read from ``cur.rowcount`` (the INSERT command tag: 1 on insert, 0 on conflict),
        NOT ``RETURNING`` — RETURNING would require SELECT on the returned columns;
      * the conflict clause is the BARE ``ON CONFLICT DO NOTHING`` (no ``(email)`` target) —
        naming the arbiter column would require SELECT on it. ``signups`` has exactly one unique
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
                    "INSERT INTO signups (email, source, ip, ua) VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT DO NOTHING",
                    (email, source, ip, ua),
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


def _client_ip(request: Request) -> str:
    # ACA sits behind a proxy; trust the FIRST X-Forwarded-For hop for coarse rate-limiting only
    # (never for auth). Fall back to the socket peer.
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _truncate(value: Optional[str], limit: int) -> Optional[str]:
    if value is None:
        return None
    return value[:limit]


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
) -> FastAPI:
    root = site_root or SITE_ROOT
    limiter = rate_limiter or RateLimiter(RATE_LIMIT_MAX, RATE_LIMIT_WINDOW_SEC)

    if store is None:
        dsn = os.environ.get("SIGNUPS_DB_DSN")
        store = PostgresStore(dsn) if dsn else InMemoryStore()

    app = FastAPI(title="Agentricity signup service", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store

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
        except Exception:
            return JSONResponse(
                {"ok": False, "error": "store_unavailable", "message": "We couldn't save that just now."},
                status_code=503,
            )

        if result == Recorded.NEW:
            message = "You're on the early-access list. We onboard in batches — watch your inbox."
        else:
            message = "You're already on the early-access list. We onboard in batches — watch your inbox."
        return JSONResponse({"ok": True, "status": result, "message": message}, status_code=200)

    # Static site LAST so the explicit routes above win. StaticFiles(html=True) serves index.html at
    # "/" and every legal page at its EXACT path (/privacy.html, /terms.html, /refund.html,
    # /legal/*, /app-ads.txt) — a hard app-store requirement.
    app.mount("/", _PublicStaticFiles(directory=str(root), html=True), name="site")
    return app


# Uvicorn entrypoint: `uvicorn server.app:app` (see Dockerfile).
app = create_app()
