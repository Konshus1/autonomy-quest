# Checkpoint v8 — Agent-comms wired to a durable local table

**Task:** #4407 · **Session:** ccf-4407-ralph-to-aq (living manager)
**Branch:** task/3981-causal-port-checkpoints · **Date:** 2026-08-01
**recommended_task_status_action:** `keep_in_progress_living_manager`

## What landed

`/api/agent-comms` returned a `[]` stub — the last abstract component not wired. v8 backs it
with a durable local table so the React shell renders real messages.

- **`management/api/store.py`** — `create_comm(from_handle, to_handle, text, kind)` on both
  stores; item shape `{id, from_handle, to_handle, text, kind, ts}` (`ts` = UTC ISO-8601,
  stamped identically in both). `comms()` returns rows with `comm-<n>` ids. Pg uses the
  existing `ralph_comms` table (seq → `comm-<seq>`).
- **`management/api/app.py`** — `POST /api/agent-comms` (validates `from_handle`+`text`;
  `to_handle` optional = broadcast; `kind` default `message`). **Additive**: `GET` still
  returns `{"items": [...]}`, now non-empty.
- **`tests/test_management_store.py`** — in-memory comm shape/id/ts + broadcast; Pg integration
  extended to comms.
- **`tests/test_management_comms_api.py`** (new, kept out of the co-owned `test_management_api.py`)
  — POST→GET, broadcast, validation (422 on empty text / missing sender), state-count reflection.

## Scope honesty

This is the **kit's own in-container comms log**, not the live `tbagents` bus — it gives the
management shell real, persistent rows without coupling the image to an external bus. A bus
proxy/bridge remains a later option (plan slice 3 alt).

## Verification (ground truth)

- **45 passed, 1 skipped** (Pg integration gated on `AQ_TEST_DB_URL`).
- **Live Pg integration** (container DB :55490): comms roundtrip passes.
- **Live uvicorn against container Postgres**: `backing=postgres`; `POST /api/agent-comms` →
  item `comm-2` with `ts`; `GET` lists it; row confirmed in container `ralph_comms`
  (`payload->>'text' = "v8 durable comms live"`).

## Contract note (sent to ccf-kevin-bootstrap)

Comm item shape `{id, from_handle, to_handle, text, kind, ts}` — their `ListPanel` can render
`from_handle/to_handle/text/ts` instead of raw JSON. No port/env change; GET shape stable.
