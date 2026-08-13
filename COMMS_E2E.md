# Real two-stack comms e2e (#4834 Phases 2/3/5)

`tests/test_comms_two_stack_live_e2e.py` stands up a **real** `aq-replica-*` Docker stack from the
current checkout and exercises the agent-comms wire over **real loopback HTTP**, asserting CONTENT
against GROUND TRUTH (real replica Postgres rows read via `psql`, real HTTP status + body). It
replaced the former hollow `test_live_docker_two_stack` stub — which was all skip-gates with no body
and "passed" in ~1.4s without ever standing up a stack.

The in-process, always-run companion (`tests/test_comms_two_stack_e2e.py::
test_two_stack_relay_across_separate_stores`) stays as the portable, deterministic layer.

## How to run

```bash
AQ_COMMS_DOCKER_LIVE=1 AQ_REPLICATION_MIN_FREE_MEM_PCT=12 \
    python -m pytest tests/test_comms_two_stack_live_e2e.py -q -s
```

- `AQ_COMMS_DOCKER_LIVE=1` opts into the heavy path (kept out of the default suite — without it the
  test SKIPS LOUDLY, and `pytest -q` stays green).
- `AQ_REPLICATION_MIN_FREE_MEM_PCT=12` is the resource gate override (see the memory note below).
- A real replica build + health takes ~35–40s (parent image cached). Only ONE thing may stand up
  stacks at a time; the test refuses to run if any `aq-replica-*` already exists.

## What it proves (all over the real wire, ground-truthed)

- **Stand-up (contract #1):** a real replica stack comes up; the test asserts `/health` is reachable
  with a real `git_sha` before proceeding. If stand-up fails the test FAILS (never skips).
- **Phase 2 (outbox → parent journal):** the replica publishes `status.report` + `experiment.result`
  to its own outbox via its real authenticated API; `scripts/host_outbox_relay.run_once` pulls the
  replica's REAL `/api/agent-comms/outbox` over `127.0.0.1:<port>` into a distinct parent store. Asserts
  the parent journal rows carry `trust=untrusted_claim`, the relay provenance block
  (`source_instance_id`, `original_created_at`), the real payload content, and that a 2nd pass is
  idempotent (0 re-copied). The replica-side envelopes are ground-truthed in its real Postgres.
- **Phase 3 (parent → replica work.request + gated import):** `scripts/host_workrequest_relay`
  POSTs an authenticated `work.request` to the replica's REAL `/inbox` (stored inert). A work.request
  carrying forbidden fields (`run_shell` / `replicate` / `AQ_REPLICATION_AUTO_APPROVE` / `auto_approve`
  / `requires_human=false`) has them quarantined under `_untrusted_extra` (asserted in the real inbox
  row). The REAL importer runs inside the replica (`docker exec`) against its REAL inbox + REAL
  planning queue with `AQ_COMMS_WORK_IMPORT=1`; the `work` table is then read directly: the two
  parent-delivered requests import as GATED proposals (`requires_human=true`, `blast_radius_level=3`,
  a real gate reason, forbidden fields absent), and a self-authored `POST /api/agent-comms`
  work.request is NOT imported (the `delivery=delivered` + `origin=parent` guard).
- **Phase 5 (A2A over the real mgmt port):** `/.well-known/agent-card.json` → 200, 3 skills,
  `streaming=false`, honest conformance label, no instance id leaked; `message/send` → an untrusted
  claim (`aq:authority=none`, `aq:importState=not_imported`); a spoofed `aq:trust` → `-32602`; a
  FilePart `uri` → `-32005` refused; cross-principal `tasks/get` → `-32001` TaskNotFound; no-auth → 401.
- **Safety invariant:** after the import + A2A traffic, every imported item is `requires_human=true`
  (parked, not executed), no `imported_work_request` row is auto-approved, and zero verification rows
  exist (A2A/relay traffic mints no verification and flips no gate).
- **Teardown (contract #5):** in a `finally`, and then asserts 0 orphan `aq-replica-*` containers/
  volumes remain and the parent stack (`aq-seed-c2f4834`) is untouched.

## Red-first proof (this test is not hollow)

Two env-gated sabotage hooks (default OFF) break exactly one wire so the corresponding CONTENT
assertion goes RED; unset them and it passes again:

```bash
# Phase 2 relay pointed at a dead port -> "Connection refused" -> relayed=0 -> Phase 2 assertion RED
AQ_E2E_SABOTAGE=relay_dead_port AQ_COMMS_DOCKER_LIVE=1 AQ_REPLICATION_MIN_FREE_MEM_PCT=12 \
    python -m pytest tests/test_comms_two_stack_live_e2e.py -q -s

# Importer flag off -> enabled=False, 0 imported, no work rows -> Phase 3 import assertion RED
AQ_E2E_SABOTAGE=import_off AQ_COMMS_DOCKER_LIVE=1 AQ_REPLICATION_MIN_FREE_MEM_PCT=12 \
    python -m pytest tests/test_comms_two_stack_live_e2e.py -q -s
```

Both go RED (and still tear down cleanly); the unsabotaged run is GREEN.

## The psutil-vs-real memory note

The stand-up's resource gate (`AQ_REPLICATION_MIN_FREE_MEM_PCT`, default 35) uses `psutil` when
present, which on macOS reports *available* memory pessimistically (~22% here) versus
`memory_pressure` (~45% free). A replica is a ~0.3GB stack. Do **not** hardcode a 45% psutil gate —
it would skip forever on macOS. The override `AQ_REPLICATION_MIN_FREE_MEM_PCT=12` clears the pessimism
while still refusing on a genuinely starved host. When resources are truly absent the test SKIPS
LOUDLY; it never "passes" with an empty body.

## Provisioning (how the comms env reaches the replica)

The comms flags are not in the base `docker-compose.yml` app environment. The test provisions them
without touching production stand-up code: a custom `compose_runner` (a) appends the comms vars to the
replica's sourced secret file (last-wins over any generated dup) and (b) writes a `comms.yml` overlay
adding them to the app service's `environment`, injected as an extra `-f` before `up`. Both files live
in the replica state dir, so teardown reaps them. Vars provisioned: `AQ_COMMS_INSTANCE_TOKEN`,
`AQ_COMMS_OUTBOX_READ_TOKEN`, `AQ_COMMS_INBOX_TOKEN`, `AQ_COMMS_PARENT_INSTANCE_ID`,
`AQ_COMMS_WORK_IMPORT=1`, `AQ_A2A_ENABLE=1`, plus `AQ_MGMT_DB_URL` (see the gap below).

## FINDING — the comms durable store does not run under the least-privilege `aq_loop` role

The management comms store creates `ralph_comms_envelopes` / `ralph_comms_verifications` /
`ralph_comms_imports` lazily in `build_store()`, but those tables are **not** in the migration schema
(only `ralph_workstreams/tasks/comms/replication/merges` are, via `schema/022`), and
`schema/999_container_role_grants.sql` `REVOKE`s `CREATE ON SCHEMA public FROM aq_loop`. So under the
default `AQ_DB_URL` (`aq_loop`) the comms store fails to init, falls back to in-memory with
`durability_required=True`, and **every comms write returns 503 (fail-closed)** — the Phase-0..5 comms
feature landed inert and its durable store was never actually exercised in-container. This e2e
surfaced that. To exercise the real wire, the test provisions the replica's comms store via the owner
DSN (`AQ_MGMT_DB_URL=postgresql://aq_owner:${AQ_DB_OWNER_PASSWORD}@postgres:5432/aq`, expanded from the
secret file at compose source-time). **This is a test provisioning choice, not the production posture.**
The proper deployment fix is a migration that creates the three comms tables (numbered `< 999` so the
existing blanket `GRANT SELECT,INSERT,UPDATE,DELETE ... TO aq_loop` covers them) so the comms store
runs under `aq_loop`. That fix is out of scope for this test slice and is flagged here for a follow-up.

## Phase 4 is NOT covered here (and why)

A TRUE live multi-agent role run needs Codex subscription auth inside the replica. A fresh replica
gets an EMPTY `aq-codex-auth` volume, so its loop hibernates awaiting an operator device-auth click.
Faking a live agent run would be dishonest, so this automated e2e does not attempt it. Phase 4's live
multi-agent path is a **separate, codex-auth-gated manual step** (an operator authenticates the
replica's Codex, then arms `AQ_WORKFLOW_MULTI_AGENT` with a role-declaring workflow). The default-off
byte-identical single-executor behavior and the "arming without a role-declaring workflow is inert"
property are covered by the in-process Phase-4 unit tests, not by this Docker e2e.
