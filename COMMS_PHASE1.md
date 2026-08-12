# Agent-comms Phase 1 — host-observed replica status → parent journal → React UI

Builds on Phase 0 (`COMMS_PHASE0.md`). Design doc: `docs/design/agent-comms-a2a-design.md`,
§10 Phase 1 + §9.2 (topology view) + §4.1 (envelope). Phase 1 is **pure observability**: the host
**observes** replicas; there is **no replica token and no inbound command path**. It grants no
capability — nothing here calls Docker, changes a replication gate, or actuates. Replica-authored
status arrives in Phase 2 and can never overwrite the host observation.

## What landed

### 1. Host fleet `/health` poller — `scripts/host_fleet_poller.py` (HOST-ONLY)
A host-side process (outside the guest import closure, exactly like the replication daemon) that:

- reads the **host-owned fleet registry** (Phase 0) for live replicas and polls each one's
  `/health` at `http://127.0.0.1:<app_mgmt_port>/health` — the port coming **only** from the
  registry;
- writes one **`health.observed`** envelope per poll to the **parent journal** (the comms store)
  with `trust=host_observed` — recording the observer, `observed_at`, endpoint, observed `git_sha`,
  loop heartbeat (`cycling` / `seconds_since_last_cycle`), the failure reason, and a `topology`
  block (instance/project/lineage/workflow/SHA/port/lifecycle/cap/credential state);
- updates the registry's topology **health** (`live` / `stale` / `down`) and a monotonic
  `last_successful_poll` **healthy watermark** (advances only on a live observation);
- `--once` (drain) and `--interval N` (until SIGINT/SIGTERM) modes.

**Health classification** (`classify_health`):
- reachable + `ok` + expected SHA (or none expected) → **live**;
- reachable but **degraded** (SHA mismatch / missing SHA / non-JSON) → **stale** + reason
  (e.g. `version mismatch: registry expected …, observed …`);
- unreachable (refused/timeout) → **stale** if the last healthy poll is within the stale threshold
  (recently alive, momentary miss), else **down**.

**Safety rails (design §10 Phase 1 safety review):**
- **SSRF-safe.** The only URL ever built is `http://127.0.0.1:<registry app_mgmt port>/health`.
  Endpoints come **only** from `registry.live()` — never the guest-writable proposal store, never an
  envelope, never a caller-supplied host. The entry's own `health_url` is ignored. It **cannot scan
  arbitrary addresses.**
- **Read-only /health only.** `_assert_loopback_health_url` re-checks, before every request, that the
  scheme is http, the host is loopback, and the path is exactly `/health` (no query/params) — so the
  poller can call **only** the read-only `/health` route, never a write/replication/actuation route.
- **Bounded.** Bounded thread-pool concurrency (`--max-workers`, default 8) + a hard per-poll timeout.
- **Observation only.** No capability granted; no replica-authored status accepted.

### 2. Fleet / topology React view — `management/frontend/`
- `management/api/fleet_view.py` (guest-reachable) projects the **latest `host_observed`
  `health.observed` event per instance** from the parent journal into the fleet view. It reads the
  **journal, never the host registry** — so the management API serves the fleet view without
  importing any host-only module (the import firewall stays intact). Only `trust=host_observed`
  drives health; a replica's `untrusted_claim` of the same kind is **ignored**.
- `GET /api/fleet` returns the host-authoritative topology: instance IDs, project, lineage,
  created_at, workflow/version, git SHA (observed + expected), host-observed health + last poll +
  heartbeat + live/stale/down, message counts, teardown state + whether it still counts against the
  cap, credential-present/revoked, and a **redacted host-local port** (`127.0.0.1:<port>
  (host-local)` text — never a clickable arbitrary URL).
- `FleetPanel.tsx` renders it as a **typed table** (not raw JSON) with live/stale/down health
  badges. The raw-JSON agent-comms `ListPanel` is replaced by a typed `CommsPanel.tsx` that shows a
  `host-observed` trust badge. `api.ts` gains `FleetInstance` / `FleetResponse` types.

### 3. Authenticated fleet GET — safety note **N3** (Phase-0 review, folded in)
`GET /api/fleet` is **operator-credentialed** (`_require_operator`): unauthenticated → **401**, a
bare instance credential → **403**, the operator credential → 200. With no operator credential
configured it fails closed (401) — the fleet/health view is **not world-readable** on the mgmt port.
(The Phase-0 legacy `GET /api/agent-comms` projection is left unchanged/unbroken.) The `FleetPanel`
holds the operator token in `localStorage`; with no token the panel is honestly **locked**.

## Safety invariants held
- **Observation only** — no replica-authored status yet (Phase 2); the poller derives health.
- **SSRF-safe, bounded, /health-read-only** — registry endpoints only; cannot hit a
  write/replication/actuation route (proven by test + the loopback/route guard).
- **Host-only** — the poller imports `ralph_portable.fleet_registry` (a forbidden guest import), so
  it is structurally outside the guest closure (import-firewall proof).
- **`health.observed` is `host_observed` and cannot be overwritten by a replica** — only the poller
  (writing directly to the store) mints `host_observed`; the authenticated API path always stamps
  `untrusted_claim`, and the fleet projection ignores non-host observations.
- **No capability granted** — replication cap/memory/approval + guest-proposes/host-executes
  boundaries untouched.

## Tests (design §10 Phase 1)
`tests/test_fleet_health_poller.py` (14): two replicas → two host_observed events; refusal→down,
timeout→timeout result, version-mismatch→stale+reason; restart/reopen → health resumes + watermark
preserved; monotonic ULID events; **live→stale→down threshold transition**; ignores proposal-row
ports (SSRF); requests only loopback `/health`; route guard rejects non-`/health`/non-loopback;
`poll_one` ignores a manipulated `health_url`; bounded concurrency; a replica cannot mint
`host_observed`; **real-socket end-to-end** (a live loopback `/health` server, genuine TCP).

`tests/test_fleet_api.py` (7): fleet GET operator-gated (401/403/200); projection semantics; latest
host observation wins; replica `untrusted_claim` ignored for health; message counts; redacted port.

`tests/test_import_firewall.py` (+2): the poller's import closure **reaches** the host registry
(positive control) yet is **not** in the guest closure; the fleet projection imports no host module.

`tests/test_fleet_poller_docker.py`: optional docker + `>45%`-free-memory-gated live check
(stand-up → observe → teardown → 0 orphans); **skips loudly** when docker/psutil/memory or
`AQ_FLEET_DOCKER_LIVE=1` are absent. The always-on real-socket check above is the portable evidence.

`management/e2e/tests/fleet.spec.ts`: Playwright — the Fleet card is operator-**locked** with no
token; with the token it renders the host-observed topology (redacted port) and a
**live → stale → down** transition (route-intercepted for determinism).

## Run
```
pytest -q                       # heavy docker check gated/skipped
python scripts/host_fleet_poller.py --once          # drain: poll every live replica once
python scripts/host_fleet_poller.py --interval 30   # poll every 30s (HOST-ONLY)
```
Do **not** point the poller at anything but the host registry; it reads `/health` only.
