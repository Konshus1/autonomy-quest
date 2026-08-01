# Checkpoint v10 — React shell baked into the container (OOTB)

**Task:** #4407 · **Session:** ccf-4407-ralph-to-aq (living manager)
**Branch:** task/3981-causal-port-checkpoints · **Date:** 2026-08-01
**recommended_task_status_action:** `keep_in_progress_living_manager`

## What landed

The React shell (v7) built to `management/web/dist` but the image didn't bake it, so the
container served the interim console. v10 bakes it from source so the image serves the
**React shell OOTB**, and fixes an image-bloat bug found along the way.

- **`.dockerignore`** (new) — excludes `**/node_modules` and `management/web/dist`.
  *Bug fixed:* v6's `COPY management/` had swept in **64M of host `node_modules`**; the image
  dropped **952MB → 869MB**. Also excludes `__pycache__`, `.git`, pyc, etc.
- **`container/Dockerfile`** — multi-stage:
  - `FROM node:20-slim AS webbuild` → `npm ci` (lockfile-cached) → `npm run build`
    (`vite` outDir `../web/dist`).
  - Runtime: `COPY --from=webbuild /build/web/dist /app/management/web/dist`, after
    `COPY management/`. `console()` already prefers `web/dist` → serves React; interim console
    remains the fallback for a partial image.

## Verification (ground truth — image `aq-ralph:4407-v10`)

- Node stage built from source; `web/dist` (index.html + hashed assets) baked into the image;
  `frontend/node_modules` NOT in the image. Image 869MB (< v6's 952MB).
- Container HEALTHY ~9s. `GET /` → React shell (`id="root"`, **no** "interim" marker);
  hashed asset `/assets/index-*.js` → 200; `/api/ralph/state` → `backing=postgres`,
  `merge_gate=manager_gated` **OOTB** (no extra flags beyond `-e POSTGRES_PASSWORD=x`).

## Run/env contract (OOTB)

```
docker build -t aq-ralph:4407 -f container/Dockerfile .
docker run -d -e POSTGRES_PASSWORD=x -p 8090:8090 -p 8080:8080 aq-ralph:4407
# HEALTHY on Postgres+AGE; then:
#   :8090/         React management shell
#   :8090/health   ok, merge_gate=manager_gated
#   :8090/api/ralph/state   backing=postgres (via AQ_DB_URL socket)
#   :8080/         stdlib mission-status UI (unchanged)
```

Env knobs: `AQ_MGMT_ENABLED` (1/0), `AQ_MGMT_PORT` (8090), `AQ_MGMT_BIND`,
`AQ_MGMT_DB_URL`→`AQ_DB_URL` (state backing select; in-memory fallback if unreachable),
`AQ_REPLICATION_AUTO_APPROVE` (host OVERRIDE for the operator replication gate).

Shared with ccf-kevin-bootstrap for `docs/build-the-status-ui.md` (BB #721, container-as-example).

## Definition-of-done impact

Docker path is now **fully OOTB**: React shell + FastAPI management + durable Postgres state,
all from one `docker run`. Interim console retained as an honest no-build fallback.
