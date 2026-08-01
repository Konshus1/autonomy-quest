# Interview 10 — Work visualization + platform stack (Ralph-control pack)

*Only if `ralph_control.enabled`.*

## Doctrine (two kits, same abstract components)

The system always needs the same abstract pieces: workstream/task visualization, agent-comms
surface, bus-backed protocols, worker/evaluator/manager roles. How those are *implemented* differs:

| Kit | Stack choice |
|---|---|
| **Docker OOTB** | Concrete code in the image: **React frontend + FastAPI backend + Postgres/pgVector/AGE**. Works out of the box. |
| **Prompt-only setup** | React + FastAPI (+ Postgres/AGE) are the **defaults**, not locks. The human may switch frontend/backend (and sometimes datastore) for something else. The coding-agent prompt states the **abstract requirements**; the agent implements them on the chosen platforms. |

## What to establish (prompt-only / interview path)

1. Keep the defaults (React + FastAPI + Postgres/AGE), or pick alternatives?
2. If alternatives: name frontend and backend platforms clearly.
3. Confirm they still want the abstract surfaces: workstreams, tasks, agent communications.

Do **not** invent a platform they did not choose. Record their choice; build to the abstract contract.

## Record

```yaml
ralph_control:
  ui:
    # Docker kit ignores swaps and ships react_fastapi_management.
    # Prompt kit: default react_fastapi_management; use custom if they switch.
    profile: react_fastapi_management   # react_fastapi_management | stdlib_status | cli_only | custom
    show_workstreams: true
    show_agent_comms: true
  platforms:
    frontend: react                     # default; human may change in prompt kit
    backend: fastapi                    # default; human may change in prompt kit
    datastore: postgres_pgvector_age    # default
```
