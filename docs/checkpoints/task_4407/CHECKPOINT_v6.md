# Checkpoint v6 — Durable Postgres-backed management state

**Task:** #4407 · **Session:** ccf-4407-ralph-to-aq (living manager)
**Branch:** task/3981-causal-port-checkpoints · **Date:** 2026-08-01
**recommended_task_status_action:** `keep_in_progress_living_manager`

## What landed

The management API state was process-local in-memory (honest stub). v6 makes it
**durable** without changing any response shape — the contract locked with
`ccf-kevin-bootstrap`'s React shell (v7).

- **`management/api/store.py`** — one interface, two stores:
  - `InMemoryStore` (default; original behavior, no DB needed → smoke stays green).
  - `PgStore` — Postgres via psycopg2; `ralph_`-prefixed tables created with
    `CREATE TABLE IF NOT EXISTS` (additive; never touches the AQ mission schema);
    short-lived connection per op; seeds `ws-default` idempotently.
  - `build_store()` picks Pg when a DB URL (`AQ_MGMT_DB_URL` → `AQ_DB_URL`) is set and
    reachable, else in-memory. **Any** psycopg2/connection failure falls back to
    in-memory — a DB blip never 500s the API.
- **`management/api/app.py`** — endpoints call `store`; `state.backing` reflects the
  live store. Field shapes unchanged (version bumped 0.1.0 → 0.2.0). POST bodies identical.
- **`tests/test_management_store.py`** — in-memory contract, `build_store` selection,
  dead-DSN fallback, + a Pg integration test gated on `AQ_TEST_DB_URL` (skipped by default).

## Contract stability (for the React shell, v7)

Only observable change: `state.backing` flips `"in_memory_stub"` → `"postgres"` and
counts/items carry real rows. `/api/ralph/state`, `/api/workstreams`, `/api/tasks`,
`/api/agent-comms` field shapes and all POST bodies are byte-for-byte identical. Locked
with bootstrap in bus thread (msgs #45458/#45461).

## Verification (ground truth)

- **39 passed, 1 skipped** (Pg integration skipped without `AQ_TEST_DB_URL`).
- **Live PgStore integration** against the container's Postgres (`AQ_TEST_DB_URL=…:55490`):
  1 passed; `ralph_*` tables materialized.
- **Container OOTB** (`aq-ralph:4407-v6`, HEALTHY ~9s): `state.backing = "postgres"`
  automatically via `AQ_DB_URL` socket. Created a task via API → row confirmed in
  container Postgres `ralph_tasks`.
- **Durability**: `docker restart` → task-1 **survived** (data in PG, not process memory).

## Gaps / next

- Workstream/task **mutation** beyond create (status transitions, comms writes) still thin —
  endpoints exist for reads + task/replication/merge creates; extend as the React shell needs.
- Agent-comms is still read-only stub content (v8 wires real bus messages).
- Running container `aq-4407-v6-smoke` left up (console `localhost:18191`) for bootstrap to
  smoke the React shell against real Postgres-backed rows.
