# Task #4407 — Docker OOTB React + FastAPI management plan

## Doctrine (Kevin clarification 2026-07-31)

| Kit | Stack |
|---|---|
| **Docker** | Concrete working code in the image: React FE + FastAPI BE + Postgres/pgVector/AGE |
| **Prompt-only** | Same stack as **defaults**; interviewed human may switch FE/BE; agent builds abstract components on chosen platforms |

Abstract components both kits must deliver: roles, bus, workstreams/tasks/agent-comms UI, validation ladder, manager-gated cohort→main, replication request surface.

## Current AQ container (gap)

Today: Postgres+AGE + stdlib `ui/server.py` + runner. Mission/loop substrate — **not** the Ralph management Control Tower.

## Target Docker composition

```
┌─────────────────────────────────────────────┐
│  container                                  │
│  ┌─────────────┐  ┌──────────────────────┐  │
│  │ React UI    │→ │ FastAPI management   │  │
│  │ workstreams │  │ API + agent bus      │  │
│  │ tasks/comms │  │ protocols            │  │
│  └─────────────┘  └──────────┬───────────┘  │
│                              │              │
│                   Postgres + pgVector + AGE │
│                   (mission loop + ralph ctl)│
└─────────────────────────────────────────────┘
```

Merge gate in software: **manager_gated** — manager coding agent approves merge or escalates to human. Not operator-gated for cohort→main. (Replication/spawn remains operator-approved by default.)

## Build slices

1. ✅ **(v5)** FastAPI app wired into container (Dockerfile COPY `management/` + `ralph_portable/`;
   entrypoint `supervise_management` on 8090). `health` + `/api/ralph/state` stub live.
   *Uncertainty:* state is process-local in-memory, not yet DB-backed (BB #2141/#2144).
2. ✅ **(v5, interim)** Management shell served OOTB by FastAPI at `/` — honest **vanilla-JS**
   console (workstreams/tasks/comms/state), no build step. **React/Vite build stage is the
   remaining upgrade** (Node build stage in Dockerfile) — deliberately deferred to avoid a
   Node toolchain in the image before the shell proves alive.
3. Wire agent-bus message list (local table or proxy) — comms endpoint still returns stub `[]`.
4. ✅ **(v2b)** Manager merge decision endpoint: `approve_merge` | `escalate_human` (no silent auto).
5. ✅ Mission-loop / stdlib status path (`:8080`) unchanged; management API is additive on `:8090`.

### Next after v5
- Durable state: back `/api/ralph/state` + workstreams/tasks/comms with DB tables (schema migration).
- React/Vite build stage → replace interim console (keep console as no-JS-build fallback).
- Container-level smoke: `docker build` + `docker run` + curl `:8090/health` (needs bootstrap/Docker host).

## Non-goals for this checkpoint

- Shipping public AQ main with React forced for all non-Ralph users.
- Giving guest containers docker.sock.
- Claiming TB production already does manager-gated merge (#1268 gap remains until implemented).
