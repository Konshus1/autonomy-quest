# Ralph-control management stack (Docker OOTB target)

Best-guess skeleton for Task #4407. Uncertainty: final packaging into `container/Dockerfile`
vs a second image profile — proceeding with an in-repo FastAPI app that can run beside
the existing Postgres substrate.

## Intent

- Docker kit: this code (React + FastAPI) should eventually **work out of the box** in the image.
- Prompt kit: abstract requirements in `docs/checkpoints/task_4407/PROMPT_KIT_ABSTRACT_REQUIREMENTS.md`;
  operator may choose other FE/BE.

## Layout

- `api/` — FastAPI management API (workstreams/tasks/comms stubs + manager merge + replication propose)
- `web/` — React shell (placeholder until Vite app is generated)
- Run API: `uvicorn management.api.app:app --port 8090`
