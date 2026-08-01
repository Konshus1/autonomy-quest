# Task #4407 — Ralph workflow map (bootstrap-consumable)

**Audience:** `ccf-kevin-bootstrap`, coding agents running AQ setup, Cursor goal lane.
**SoT:** Postgres task #4407 + BB note #2141. This file is the portable map.

## 1. Target control loop (Kevin narrative)

```
ingest/evaluate causal principles
        ↓
formal plan MUST satisfy relevant principles
        ↓
formal planning rules validate plan
        ↓ (if too big for one PR/commit)
task breakdown into PR-sized chunks
        ↓
worker: choose highest feasible test level
        UI  >  API  >  unit
        ↓
evaluator: tests cover business intent AND pass on worker worktree
        ↓
worker/evaluator pair eligible for integration cohort
        ↓
shared integration branch + live full env (DB + FE/BE)
        ↓ fail → rework/evaluate loop → max attempts → worker's manager
        │         (continue rework OR operator_review)
        ↓ pass
managers cursory review (catch big misses)
        ↓ approve
merge integration → main; close worktrees; retire worker/evaluator sessions
```

Supporting substrate (not optional in the target story):

- **Agent communications bus** with protocols (not only tmux paste).
- **UI** for workstreams, tasks, and agent messages — usable by non-terminal agent types too.
- Every worker has a **specified manager**.

## 2. Validation vs TalkingBack Ralph (code + BB)

| Stage | TalkingBack today | Verdict |
|---|---|---|
| Causal principles store + selector + usage | `causal_principle*` tables; selector/maintenance/usage dry-run/credit assignment | ALIGNED substrate |
| Principles constrain formal plan | Learning-loop inject (#521); backlog planner/harness; breakdown epic #2338/#2587 | PARTIAL — not sole hard gate everywhere |
| Breakdown when too big | Formal planning / readiness paths | PARTIAL |
| Highest-feasible tests UI>API>unit | `ralph_change_classifier` ui_testable; completion evidence honesty | PARTIAL |
| Evaluator intent + pass on worktree | pairing + verifier_plan; auto_verifier intent holds | PARTIAL (intent adequacy historically report-only roots #1313/#3366) |
| Per-task rework max → manager | Strong | ALIGNED |
| Integration cohort on shared branch | Merge-train design #3452; pieces exist | PARTIAL |
| Auto merge → main after cohort | Desired by Kevin | **GAP** — BB #1268 / event #164126: human-gated / propose-only |
| Bus + Control Tower UI | `/api/agents/messages` + UI | ALIGNED in TB |
| Manager always specified | BB #1226 | ALIGNED policy |

## 3. Autonomy Quest posture (do not overclaim)

| Topic | AQ public/current | Implication for port |
|---|---|---|
| Formal causal planning as product claim | Out of scope for v1 claims (BB #1229) | Ship as optional/advanced layer or TB-derived internal; do not put in public claim surface |
| Container = example of following prompts | Decision #721 | Prompt-only kit is primary; Docker is reference instance |
| UI stack today | `ui/server.py` **stdlib-only** (explicitly no FastAPI/React) | Kevin's React+FastAPI management UI is a **kit variant / build target**, not current default UI |
| Datastore default | Postgres + AGE (+ pgvector where installed) | Matches Kevin's OOTB DB story |
| Replication / spawn | Operator-managed; loop propose-only (#711/#713/#721); Contract C / #3981 proof work | Default: operator approval; host override config allowed |
| Interview exists | `setup.md` + `interview/00–07` | Extend with Ralph role/agent/test-ladder questions — do not replace mission spine |

## 4. Dual-kit mapping

### Kit A — Prompt-only (coding-agent builds it)

Entry: `setup.md` → interview → `instance.yaml` → install.

Extra interview layers for Ralph-class multi-agent control (new files proposed under `interview/`):

| File | Decision |
|---|---|
| `08-agent-roles.md` | Which coding agents / models for worker, evaluator, manager; non-coding agent types allowed? |
| `09-comms-bus.md` | Bus substrate (HTTP bus / chat / file mailbox); protocols required |
| `10-work-visualization.md` | Surfaces for workstreams/tasks/comms (stdlib UI vs React+FastAPI build) |
| `11-validation-ladder.md` | Enforce UI>API>unit preference; integration env availability |
| `12-replication.md` | Allow container to request copy / copy-with-mod; operator gate vs host override |

User preferences recorded into `instance.yaml` under a `ralph_control:` (name TBD) block.

### Kit B — Docker OOTB management system

Kevin target: React frontend + FastAPI backend + Postgres/pgVector/AGE.

Current AQ container: Postgres+AGE + stdlib status UI + runner — **substrate**, not the full Ralph Control Tower.

Port path:

1. Keep AQ container as durable substrate + mission loop (do not break #721 “agent-buildable” story).
2. Add an optional **management overlay** (React+FastAPI) that can be selected in interview `10-work-visualization.md` / container profile `ralph_management`.
3. Overlay consumes bus + task/workstream tables; visualizes sessions/comms; does not auto-merge to main unless host policy explicitly enables a future gate (today: propose-only).

## 5. Replication protocol (intent)

Guest container may request:

1. **straight_copy** — new instance with identified mission (from request or interview).
2. **copy_with_modifications** — copy + apply specified deltas (config-layer and/or code-layer).

Host behavior:

- Default: **operator approval required** before spawn.
- Override: host config may skip approval (dangerous; document loudly).
- Guest never holds docker.sock in public kit; host/broker executes (#713/#711; Phase B broker in spawn spec).
- Purpose: scale working systems, experiment before adopting changes, evolutionary search.

## 6. Honest gap flags (do not paper over)

1. **Auto merge-to-main after cohort** — target narrative; TB not shipped; AQ must not claim it as built.
2. **React+FastAPI as default Docker UI** — conflicts with current AQ stdlib-only UI doctrine; needs human choice as kit profile.
3. **Formal causal planning** — TB partial; AQ v1 claims exclude it.
4. **Intent-adequacy enforcement** — not uniformly hard-gated yet.

## 7. How bootstrap should proceed

1. Read this map + BB #2141 + task #4407.
2. Keep `setup.md` as the only entry point; add interview 08–12 as optional Ralph-control pack.
3. For Docker kit: treat React+FastAPI management as an **opt-in profile** built by the setup agent (or shipped later as overlay image), while substrate container stays shippable.
4. Wire replication requests as **propose-only** messages to host/operator until override configured.
5. Port TB logic selectively: portable cores first (already started via #3981 `ralph_portable`), then pairing/evaluator/test-ladder docs, then cohort merge-train as design (not auto-merge).
