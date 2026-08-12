# Agent-comms Phase 0 — contract, topology truth, authentication

Foundation for the agent-comms system (design doc: `docs/design/agent-comms-a2a-design.md`,
§4 envelope/addressing, §8 security, §10 Phase 0). Phase 0 is **data model + auth + topology
registry only** — there is **no relay and no live inter-instance traffic**. The comms plane grants
no capability: nothing here calls Docker, changes a replication gate, or actuates.

## What landed

### 1. Host-owned replica topology registry — `ralph_portable/fleet_registry.py`
The single authoritative parent record of the fleet, closing the design's noted gap (the daemon
ledger kept only project/instance_id/health_url and dropped the port map). `FleetRegistryStore` holds,
per replica: `instance_id`, compose `project`, `parent/requester_instance_id` (lineage), the full
4-port map (`postgres/governance/app_ui/app_mgmt`) + `health_url`, `workflow`/`workflow_version`,
`git_sha`, `created_at`, `lifecycle_state` (`standing_up` / `live` / `torn_down`), and a comms
**credential fingerprint** (sha256 handle — never the secret) + revocation flag.

- Populated **transactionally** (atomic temp-file + `os.replace` under an advisory host flock) from
  `stand_up_replica_stack` (on `executed`) and `teardown_replica`.
- **Host-owned**: written only by host lifecycle code. It is in the import firewall's forbidden set,
  so a guest/replica cannot write it or inject an endpoint/port.
- **Reconcile on restart**: `reconcile_fleet_registry()` (called at host daemon startup) rebuilds
  truth from Docker labels (`com.docker.compose.project=aq-replica-*`) + each replica's
  `replica.json`; a registered project whose stack has vanished is marked `torn_down` + revoked.

### 2. Versioned message envelope — `management/api/comms_envelope.py`
Append-only, versioned envelope (design §4.1) replacing the opaque `ralph_comms` payload:
`{id (ULID-sortable), origin_instance_id, principal_id, channel, target{instance_id,handle}, kind,
payload, correlation_id, in_reply_to, idempotency_key, created_at, expires_at, trust, delivery}`.

- `kind` is an **allowlisted enum** (`health.observed`, `status.report`, `experiment.progress`,
  `experiment.result`, `work.request`, `work.response`, `receipt`, `operator.message`) — there is
  deliberately no generic `command`/`shell` kind.
- **Server derives** `origin_instance_id` + `principal_id` from the credential and **ignores/rejects**
  claimed identity fields (the request model uses `extra="forbid"`, so naming another principal is a
  422, not an accepted spoof).
- Durable table `ralph_comms_envelopes` with indexes on channel/origin/kind and a **unique partial
  index on `(origin_instance_id, idempotency_key)`**; a repeated key is suppressed (no duplicate row).
- **Legacy GET stays a read projection** — `GET /api/agent-comms` still returns
  `{id, from_handle, to_handle, text, kind, ts}` rows (now derived from the envelope) so the React
  poll is unbroken.

### 3. Authentication + principal derivation + ACLs — `management/api/comms_auth.py`
- Per-instance relay credential (`AQ_COMMS_INSTANCE_TOKEN`, generated at stand-up, distinct from the
  DB/governance secrets, bound to `AQ_INSTANCE_ID` + lineage + kind allowlist) and a separate
  operator credential (`AQ_COMMS_OPERATOR_TOKEN`).
- Identity is **derived from the credential**, never from `from_handle`/`sender_name`/a payload
  `AQ_INSTANCE_ID`. Constant-time credential compare.
- **ACL**: a principal may publish a given `kind` only to channels its lineage allows
  (`instance/<self>/...`, `lineage/<x>/...` for `x` in lineage); another instance's channel or an
  out-of-lineage direct target is denied (403). Operator may only send `operator.message`.
- The **unauthenticated POST is deprecated** → 401. GET stays open (compat read projection).

### 4. Fail-closed durable writes — `management/api/store.py` + `app.py`
The startup fallback-to-memory would be unsafe for a bus. The comms **write** path now fails closed:
when a durable DB was configured but is unavailable, `POST /api/agent-comms` returns **503** and
accepts nothing — never an ephemeral-fallback ack. A mid-session DB outage surfaces as
`StoreUnavailable` → 503 via the existing `_db_guard`. (Reads still degrade to in-memory so the API
boots; only writes fail closed.)

## Safety invariants held
- Comms plane grants no capability; server-derived identity only (no principal spoofing).
- Topology registry is host-owned — a guest/replica cannot write it or inject a port/endpoint.
- Guest-proposes / host-executes + the replication cap/memory/approval boundaries are untouched.
- Import firewall proves guest-reachable code cannot import the host registry
  (`ralph_portable.fleet_registry` / `FleetRegistryStore`) or the docker step.

## Tests
- `tests/test_comms_envelope.py` — schema/contract, allowlisted kinds, payload fuzz/size limits,
  ULID ordering, legacy projection, store idempotency suppression.
- `tests/test_comms_auth.py` — principal derivation, unknown-credential rejection, ACL allow/deny.
- `tests/test_comms_publish_api.py` — impersonation rejected (422 + derived identity), cross-instance
  ACL denial (403), duplicate-idempotency-key suppressed, **DB-outage → 503 (no ephemeral ack)**,
  payload size (400), unauthenticated POST (401), legacy GET projection.
- `tests/test_fleet_registry.py` — stand-up populates registry, teardown revokes credential +
  marks torn_down, reconciliation from Docker labels + replica.json.
- `tests/test_import_firewall.py` — guest closure excludes the host-owned registry writer; positive
  control that the host stack is the writer.

`pytest -q`: **856 passed, 63 skipped**.

## Explicitly NOT in Phase 0
No relay, no host→replica or replica→replica traffic, no SSE, no loop/executor integration, no live
loop redeploy. Those are Phase 1+.
