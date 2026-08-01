# Ralph-control management stack (Docker OOTB)

Task #4407. Wired into the single AQ container: `container/Dockerfile` copies `management/`
and `ralph_portable/` into the image; `container/entrypoint.sh` starts this FastAPI app via
`supervise_management` on `AQ_MGMT_PORT` (default 8090) beside the Postgres substrate and the
mission loop. It is supplementary — a management-API crash is recorded and restarted but never
stops the loop, and it never gates container health.

## Intent

- Docker kit: this code (React + FastAPI) should eventually **work out of the box** in the image.
- Prompt kit: abstract requirements in `docs/checkpoints/task_4407/PROMPT_KIT_ABSTRACT_REQUIREMENTS.md`;
  operator may choose other FE/BE.

## Layout

- `api/` — FastAPI management API (workstreams/tasks/comms stubs + `/api/ralph/state`
  + manager merge + replication propose); serves the interim console at `/`.
- `web/index.html` — interim vanilla-JS console (no build step, OOTB). React/Vite build
  is a follow-up slice (see `../docs/checkpoints/task_4407/DOCKER_OOTB_PLAN.md`).
- Run API locally: `uvicorn management.api.app:app --port 8090`
- In-container: on by default; disable with `AQ_MGMT_ENABLED=0`.
