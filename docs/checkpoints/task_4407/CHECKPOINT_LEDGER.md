# Task #4407 — Ralph→AQ dual-kit checkpoints

Epic under #2571. Cursor goal-persistence lane. BB note #2141.

| Version | Date (UTC) | What | Result |
|---|---|---|---|
| **v0-validate** | 2026-08-01T03:10Z | Narrative ↔ BB ↔ code validation; epic #4407 filed | PASS |
| **v1-workflow-map** | 2026-08-01T03:20Z | Bootstrap-consumable `WORKFLOW_MAP.md` + interview 08–12 Ralph-control pack + setup.md index | PASS (docs) |
| **v2-instance-schema** | 2026-08-01 | `ralph_control` config validator + example yaml + tests | PASS |
| **v2b/v3-merge+doctrine** | 2026-08-01 | `manager_merge_gate` (approve_merge \| escalate_human) + dual-kit stack doctrine | PASS |
| **v4-replication+api** | 2026-08-01 | `replication_request` + host broker + `AQ_REPLICATION_AUTO_APPROVE` OVERRIDE; FastAPI mgmt skeleton | PASS |
| **v5-docker-wirein** | 2026-08-01T04:xxZ | FastAPI mgmt API + interim console **wired into container** (Dockerfile COPY, entrypoint `supervise_management` :8090); `/api/ralph/state`; requirements pin FastAPI stack; test guarded w/ importorskip | PASS — 26 pytest green + live uvicorn curl smoke |
| **v9-host-replication-exec** | 2026-08-01 | Broker dry-run → **real safe host-side copy** (`host_replication_executor`): materializes instance dir (workspace copy + `instance_overrides.yaml` + `replication_manifest.json`); no docker/docker.sock/container; broker `--source-workspace`/`--dest-root` wire-in; operator gate + OVERRIDE respected | PASS — 34 pytest green + live gate/OVERRIDE broker demo |
| **v6-durable-state** | 2026-08-01 | Pluggable state backing (`management/api/store.py`): `PgStore` (durable Postgres, `ralph_*` tables) + `InMemoryStore` fallback; endpoints unchanged in shape (`state.backing` flips in_memory_stub→postgres); contract locked w/ bootstrap React shell (v7) | PASS — 39 passed/1 skipped + live PgStore integ + container OOTB backing=postgres, task survived `docker restart` |
| **v7-react-shell** | 2026-08-01 | React/Vite management shell (`management/frontend/`) bound to locked contract, builds to `web/dist`; `console()` prefers built shell w/ interim fallback — **by ccf-kevin-bootstrap** | PASS — combined commit `defae5f`; real-DB smoke both agents |
| **v10-react-ootb** | 2026-08-01 | `.dockerignore` (fixes 64M node_modules bake, 952→869MB) + multi-stage Dockerfile (node:20 build → `COPY --from` dist into runtime) → React shell serves OOTB | PASS — image `aq-ralph:4407-v10` HEALTHY ~9s, `/` id=root (no interim), asset 200, backing=postgres OOTB |
| **v8-agent-comms** | 2026-08-01 | `/api/agent-comms` []-stub → real persisted messages: POST endpoint + `store.create_comm` (id/from/to/text/kind/ts) durable in `ralph_comms`; additive surface (GET stays `{items:[]}`) | PASS — 45 passed/1 skipped + live uvicorn POST→persist→GET vs container Postgres |

## Living-manager cadence
- Smoke suite (26 tests): `pytest tests/test_ralph_control_config.py tests/test_manager_merge_gate.py tests/test_replication_request.py tests/test_management_api.py tests/test_ralph_portable_surprise_resolution.py -q`
- Live mgmt smoke: `uvicorn management.api.app:app --port 8090` → curl `/health`, `/api/ralph/state`, `/`.

Next candidates:
1. **v6-durable-state** — DB-back `/api/ralph/state` + workstreams/tasks/comms (schema migration).
2. **v7-react-build** — Vite/React build stage in Dockerfile; replace interim console (keep as no-build fallback).
3. **v8-agent-bus** — wire real agent-comms message list (local table or proxy).
4. ~~**v9-host-replication-exec**~~ ✅ done (see row above).
5. ~~**container-smoke**~~ ✅ PASSED 2026-08-01 (self-served on Docker Desktop; image `aq-ralph:4407-v5`, HEALTHY ~9s, all mgmt endpoints + stdlib UI green). See DOCKER_OOTB_PLAN.md. Handoff #1408 resolved by manager.

## Clarifications locked (Kevin 2026-07-31)
- Docker kit: React+FastAPI **working OOTB in image**.
- Prompt kit: React+FastAPI are **defaults**; human may switch FE/BE; agent builds abstract components.
- Cohort→main: **manager-gated** (coding-agent manager approves or escalates to human) — not operator-gated.
