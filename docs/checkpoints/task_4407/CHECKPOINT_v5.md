# Checkpoint v5 — Docker OOTB management wire-in

**Task:** #4407 (EPIC, parent #2571) · **Session:** ccf-4407-ralph-to-aq (living manager)
**Branch:** task/3981-causal-port-checkpoints · **Date:** 2026-08-01
**recommended_task_status_action:** `keep_in_progress_living_manager`

## What landed

The FastAPI management API existed in-repo but was **not in the container path** — the
Dockerfile never copied `management/` and the entrypoint never started it. v5 closes that gap
so the image ships **working management surfaces OOTB**, not only the stdlib `ui/server.py`.

1. **Dockerfile** — `COPY management/` + `COPY ralph_portable/` into `/app`; `EXPOSE … 8090`.
2. **entrypoint.sh** — `supervise_management` starts `uvicorn management.api.app:app` on
   `AQ_MGMT_PORT` (default 8090), gated by `AQ_MGMT_ENABLED` (default 1). Follows the existing
   supervisor doctrine: restart-on-crash with `record_crash`, **never blocks / stops the loop**,
   **never gates container health**, and **skips cleanly** (logs, returns) if fastapi/uvicorn
   are absent from the image.
3. **management/api/app.py** — added `GET /` (interim console) and `GET /api/ralph/state`
   (plan slice 1 snapshot: merge gate, replication override state, counts).
4. **management/web/index.html** — interim **vanilla-JS** console (no build step), served OOTB
   by FastAPI. Honestly labeled "interim shell — React/Vite build is a follow-up slice".
5. **requirements.txt** — pinned the Docker-kit BE stack: fastapi 0.141.1, uvicorn[standard]
   0.38.0, pydantic 2.12.5 (previously undeclared → mgmt test errored on collection).
6. **tests/test_management_api.py** — guarded with `pytest.importorskip("fastapi")` so the smoke
   suite stays green/honest in stdlib-only envs; added `/` and `/api/ralph/state` cases.

## Verification (ground truth)

- **26 pytest green:** `pytest tests/test_ralph_control_config.py tests/test_manager_merge_gate.py
  tests/test_replication_request.py tests/test_management_api.py
  tests/test_ralph_portable_surprise_resolution.py -q` → `26 passed`.
- **Live uvicorn smoke** (the exact entrypoint command): `/health` → `merge_gate:"manager_gated"`;
  `/api/ralph/state` → `backing:"in_memory_stub"`, counts + replication override; `/` →
  `text/html`, contains "interim". Health/state endpoints pass.
- `bash -n container/entrypoint.sh` → OK.

## Honest gaps / uncertainty (documented, not hidden)

- **State is process-local in-memory**, not DB-backed yet → v6-durable-state (BB #2141/#2144).
- **Interim console is vanilla-JS, not React.** React/Vite build stage deliberately deferred to
  avoid a Node toolchain in the image before the shell proves alive → v7-react-build.
- **No container-level build/run smoke** here (no Docker host in this session). Wire-in verified
  at code + live-uvicorn level; `docker build`/`docker run` curl `:8090/health` needs
  bootstrap/Docker host → coordinate with `ccf-kevin-bootstrap`.
- Agent-comms endpoint still returns `[]` stub → v8-agent-bus.

## Next slice (living manager continues)

Prefer **v6-durable-state** or **v9-host-replication-exec** next; both advance DoD without a
Docker host. Container smoke is queued for bootstrap coordination.
