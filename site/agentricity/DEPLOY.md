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
