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
        ↓ manager-gated: approve OR escalate to human on close calls / uncertainty
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
| Cohort → main after manager review | **Intended: manager-gated** (coding-agent manager approves or escalates to human) | **GAP in TB today** — BB #1268 still human/propose-only; doctrine is manager-gated, not operator-gated |
| Bus + Control Tower UI | `/api/agents/messages` + UI | ALIGNED in TB |
| Manager always specified | BB #1226 | ALIGNED policy |

## 3. Autonomy Quest posture (do not overclaim)

| Topic | AQ public/current | Implication for port |
|---|---|---|
| Formal causal planning as product claim | Out of scope for v1 claims (BB #1229) | Ship as optional/advanced layer or TB-derived internal; do not put in public claim surface |
| Container = example of following prompts | Decision #721 | Prompt-only kit is primary; Docker is reference instance |
| UI stack today | AQ substrate `ui/server.py` is stdlib-only | **Docker kit target:** React+FastAPI working OOTB in image. **Prompt kit:** React+FastAPI are defaults; human may switch FE/BE; agent builds abstract components on chosen stack |
| Datastore default | Postgres + AGE (+ pgvector where installed) | Matches Kevin's OOTB DB story |
| Replication / spawn | Operator-managed; loop propose-only (#711/#713/#721); Contract C / #3981 proof work | Default: operator approval; host override config allowed |
| Interview exists | `setup.md` + `interview/00–07` | Extend with Ralph role/agent/test-ladder questions — do not replace mission spine |

## 4. Dual-kit mapping

Same **abstract components** in both kits: roles, bus, workstream/task/comms UI, validation ladder, manager-gated cohort merge, replication requests.

### Kit A — Prompt-only (coding-agent builds it)

Entry: `setup.md` → interview → `instance.yaml` → install.

React + FastAPI + Postgres/AGE are **interview defaults**, not locks. The human may switch frontend/backend. The setup prompt states **abstract requirements**; the agent chooses implementation details on the selected platforms and still must deliver the abstract components.

| File | Decision |
|---|---|
| `08-agent-roles.md` | Workers / evaluators / manager; non-coding agents? |
| `09-comms-bus.md` | Bus substrate + protocols |
| `10-work-visualization.md` | Defaults vs custom FE/BE platforms; workstreams/comms surfaces |
| `11-validation-ladder.md` | UI>API>unit; integration cohort; **manager-gated** merge |
| `12-replication.md` | copy / copy-with-mod; operator gate vs host override |

Recorded under `ralph_control:` in `instance.yaml`.

### Kit B — Docker OOTB (concrete code in the image)

**Must work out of the box:** React frontend + FastAPI backend + Postgres/pgVector/AGE implementing the management surfaces (workstreams, tasks, agent comms) plus the mission/loop substrate.

Current AQ container (stdlib status UI) is the older substrate; the Ralph-control Docker kit **ships the React+FastAPI management stack as working code**, not as a later optional overlay the user has to invent.

Merge policy in the running system: **manager-gated** (manager coding agent approves or escalates to human).

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

1. **Manager-gated cohort→main** — intended doctrine; TB still human/propose-only (#1268). Implement as manager approve-or-escalate, not operator gate.
2. **Docker OOTB React+FastAPI management** — not yet the current AQ `ui/server.py` substrate; must be built into the Docker kit image.
3. **Formal causal planning** — TB partial; AQ v1 claims exclude it.
4. **Intent-adequacy enforcement** — not uniformly hard-gated yet.

## 7. How bootstrap should proceed

1. Read this map + BB #2141 + task #4407.
2. Keep `setup.md` as the only entry point; interview 08–12 is the optional Ralph-control pack.
3. **Docker kit:** ship React+FastAPI+Postgres/pgVector/AGE management code working OOTB.
4. **Prompt kit:** abstract requirements + defaults; let human swap FE/BE; agent builds to the contract.
5. Cohort merge: manager-gated (approve or escalate to human). Replication: still operator-approved by default (host override optional).
6. Port TB logic selectively: portable cores (#3981), then pairing/evaluator/test-ladder, then manager-gated merge-train.

## 8. Causal front-half — IMPLEMENTED state + formal-integration seam (for hrf-2878)

Updated 2026-08-02. What is actually built + where the formal ASP layer inserts.

**LIVE now (all FUZZY / judgment; scores but does not yet choose work):**
- 2a mine — `ralph_portable/principle_mining.py::mine_causal_edges()`: fuzzy edges from completed+succeeded runs whose measure moved; dedup by distinct run; provenance = highest-confidence learning per run; `predicted_certainty` capped 0.34.
- 2b store + viewer — `ralph_portable/causal_edge_store.py` (InMemory) + `management/api/causal_store.py` (Pg, table `ralph_causal_edges`); `/api/causal/*`; React `CausalPanel`. **PUBLIC @4ac5fff.**
- 2c produce — `runner/loop.py` execute_work post-commit hook nudges `/api/causal/mine` each productive cycle (via `runner/causal_sync.py`).
- 2d consult (READ-ONLY) — pre-act `assess_plan_certainty(work.kind→measure_up)` records a prediction; post-commit `record_outcome_surprise()` scores actual-vs-predicted → earns support (confirm) or proposes a gated demotion. Surprise loop runs live; verified (a self-mined `demo` edge earned support from real cycles).

**Doctrine already in code:** three dials `formality(fuzzy|evidential|formal)` / `strictness(advisory|soft|hard)` / `directness(judgment|predicate|script)`; `EXECUTOR_KINDS=(judgment,predicate,script,constraint)`; `is_guaranteed = formal+script only`; promotion GATED (`propose_update`, needs `support_count≥promote_support` AND a non-judgment executor — a mined fuzzy edge is `directness=judgment` so it CANNOT reach formal until given a real executable executor). `plan_certainty()` already emits a per-step certainty profile (guaranteed proportion rises as edges formalize).

**THE FORMAL SEAM (where hrf-2878's assets attach):**
- Asset #1 (15 ASP/clingo encodings + RED/GREEN oracles) → the **executor slot** of `ralph_portable/causal_edges.py`: a clingo/ASP encoding = a `constraint` executor (formal top of the STRICTNESS dial, hard); a PDDL/action-model = a `script` actuator (formal top of DIRECTNESS). The RED/GREEN oracle = the evidence that lets `propose_update` promote fuzzy→formal, and (once formal+script) `plan_certainty` scores that step at 1.0. This is the "increasing proportion of guaranteed outcomes as principles formalize" arc.
- Contract to preserve: identity = (cause, effect, scope) IMMUTABLE; never bind identity to the mutable dials/support/evidence. Support is EARNED via the surprise loop; the miner never writes it.

**SHIPPED (Slice 3, consult-act v1):** principles now GATE a decided step — a low-certainty side-effecting action is deferred to human review instead of auto-executing (one-directional: only ever more cautious). The public line updates from "I don't act on them yet" to "they can now make me MORE cautious (defer a risky step to review), never more reckless; they still don't choose or reorder my work."

**ROADMAP (not built, do not claim live):** fuzzy→formal PROMOTION application; principles SELECTING/REORDERING which work is chosen (consult-act only gates a single decided step so far); formal planning proof over multi-step plans; evaluator test-ladder as a live loop step; manager-gated merge as a live step; replication as a self-evolving step.
