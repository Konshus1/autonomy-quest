# AQ Ralph-control — Playwright UI + DB validation (Task #4407)

End-to-end tests that drive the **React management shell** (served OOTB by the FastAPI
management API) and assert both the **UI** and the **Postgres** behind it.

## What it validates

1. The built React shell renders (`#root`, header) with **DB-backed** state — the
   Ralph-state card shows `backing = postgres`, `merge_gate = manager_gated`, and
   replication `operator-gated` (the honest safe default with no host OVERRIDE).
2. Creating a task **through the UI form** persists to Postgres: the row survives a full
   page reload (no client state) and the postgres-backed API reflects it
   (`counts.tasks` matches `/api/tasks`).
3. An agent-comms message posted to the API renders in the UI comms list.

## Run it

Point at a live management container and run:

```sh
# 1. Have a container up (OOTB image; see docs/checkpoints/task_4407/CHECKPOINT_v10.md):
#    docker run -d -e POSTGRES_PASSWORD=x -p 18195:8090 aq-ralph:4407
# 2. Install + run:
cd management/e2e
npm install
npm run install-browser          # one-time: downloads Chromium
AQ_MGMT_URL=http://localhost:18195 npm test
```

`AQ_MGMT_URL` defaults to `http://localhost:18195`. Results are written to
`results/results.json` (JSON reporter) for a reviewer to inspect; traces/screenshots are
retained only on failure.

## Notes

- Tests are **idempotent-friendly**: task/comms titles are timestamped, so re-runs don't
  collide and each run adds fresh rows (counts are asserted as deltas, not absolutes).
- No app code is mutated; this is pure black-box validation against the running kit.
