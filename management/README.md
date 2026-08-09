# Ralph-control management stack (Docker OOTB)

Task #4407 + Track E. Wired into the Compose `app` service: `container/Dockerfile` copies
`management/` and `ralph_portable/` into the image; `container/app-entrypoint.sh` starts FastAPI on
`AQ_MGMT_PORT` (default 8090) beside the status UI and mission loop. PostgreSQL is the separate
`postgres` service. The observability processes deliberately outlive a stopped loop so the browser
can report that it stopped rather than disappearing with it.

## Intent

- Docker kit: this code (React + FastAPI) should eventually **work out of the box** in the image.
- Prompt kit: abstract requirements in `docs/checkpoints/task_4407/PROMPT_KIT_ABSTRACT_REQUIREMENTS.md`;
  operator may choose other FE/BE.

## Layout

- `api/` — FastAPI API including `/api/flagship`, workstreams/tasks/comms, account connection,
  causal evidence, replication proposals, and manager decisions.
- `frontend/` — the React/Vite source. The image builds it and FastAPI serves it at `/`.
  Its leading cards are the flagship measure, cycle rationale/outcome/cost, learning trail, and
  the normally-empty >$3/high-blast-radius gate.
- Run API locally: `uvicorn management.api.app:app --port 8090`
- In-container: on by default; disable with `AQ_MGMT_ENABLED=0`.
