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

## Build slices (next)

1. Skeleton FastAPI app inside container (health + `/api/ralph/state` stub reading DB).
2. Minimal React shell (workstreams/tasks/comms placeholders) served by FastAPI or sidecar nginx.
3. Wire agent-bus message list (local table or proxy).
4. Manager merge decision endpoint: `approve_merge` | `escalate_human` (no silent auto).
5. Keep mission-loop / stdlib status path available until React shell proves ALIVE.

## Non-goals for this checkpoint

- Shipping public AQ main with React forced for all non-Ralph users.
- Giving guest containers docker.sock.
- Claiming TB production already does manager-gated merge (#1268 gap remains until implemented).
