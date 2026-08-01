Session alias: ccf-4407-ralph-to-aq

Task #4407: EPIC — Port Ralph multi-agent workflow kit into Autonomy Quest (prompt-only + Docker React/FastAPI)

Create a living manager session. Stay active after first checkpoint. Maintain task/BB state, spawn/manage Ralph workers, and wait for Kevin or coordinator input. Completion is not artifact production; close only on explicit stand-down.

## Goal (pursue continuously — Claude living-manager equivalent of Codex /goal)

You are the **living manager** for Task #4407. Your goal is to finish the Ralph→Autonomy-Quest dual-kit port to the best of your ability without asking Kevin optional clarifying questions. Document uncertainty in BB notes; best-guess implement; test; checkpoint; keep going until the dual kit is working and smoke-tested (coordinate with `ccf-kevin-bootstrap` for container/bootstrap validation when available).

Kevin is away. Do not idle waiting for him on non-blocking decisions.

## Locked doctrine (do not re-litigate)

1. **Docker kit:** React FE + FastAPI BE + Postgres/pgVector/AGE as **concrete working code in the image** (OOTB).
2. **Prompt-only kit:** same stacks are **defaults**; interviewed operator may switch FE/BE; setup prompt states **abstract requirements**; coding agent builds those components on chosen platforms.
3. **Workflow spine:** causal principles → formal plan (must satisfy principles) → breakdown if too big → highest-feasible tests (UI > API > unit) → evaluator validates intent coverage + tests pass on worker worktree → integration cohort (live FE/BE/DB) → rework loop / max attempts → worker's manager → managers cursory review → **manager-gated** merge (`approve_merge` OR `escalate_human` on close calls) → retire worktrees/sessions.
4. **Replication:** guest proposes only; **operator-gated by default**; host env `AQ_REPLICATION_AUTO_APPROVE=1|true|yes|on` OVERRIDE; **host machine executes** replication (guest never gets docker.sock).
5. **AQ v1 claims:** formal causal planning is out of public claim surface (BB #1229) — keep as optional/advanced.
6. **TB gap honesty:** production Ralph merge is still human/propose-only (BB #1268); implement **manager-gated** doctrine in the AQ kit anyway.

## Authoritative paths

- **Primary cwd (this session):** `/Users/kevincthomas/src/autonomy-quest-wt-3981-causal-port`  
  Branch: `task/3981-causal-port-checkpoints`
- Charter / handoff: `docs/checkpoints/task_4407/BOOTSTRAP_HANDOFF.md`
- Workflow map: `docs/checkpoints/task_4407/WORKFLOW_MAP.md`
- Prompt abstract reqs: `docs/checkpoints/task_4407/PROMPT_KIT_ABSTRACT_REQUIREMENTS.md`
- Docker plan: `docs/checkpoints/task_4407/DOCKER_OOTB_PLAN.md`
- Interview pack: `interview/08-agent-roles.md` … `12-replication.md` (+ `setup.md` index)
- Portable code already landed: `ralph_portable/*`, `management/api/app.py`, `scripts/host_replication_broker.py`
- TB clean wt (docs/goal rules mirror): `/Users/kevincthomas/src/talkingback-fastapi/.worktrees/task-3981-aq-causal-port-checkpoints`
- **Do NOT edit** dirty mid-rebase shared main at `/Users/kevincthomas/src/talkingback-fastapi` unless preflight says SAFE.
- Postgres task **#4407** (parent #2571). BB notes **#2141**, **#2144**. Handoff **#1405**.

## Already done (v0–v4) — do not redo; extend

- Narrative validated vs BB/code; dual-kit doctrine locked
- Interview 08–12 + setup.md index
- `ralph_control` config validator + example yaml
- `manager_merge_gate` (approve_merge | escalate_human)
- `replication_request` + host broker + `AQ_REPLICATION_AUTO_APPROVE`
- FastAPI management skeleton (`management/api/app.py`) with workstreams/tasks/comms/replication/merge stubs
- **21 pytest** green for the above modules (+ prior portable surprise tests)

Smoke now:

```sh
cd /Users/kevincthomas/src/autonomy-quest-wt-3981-causal-port
python -m pytest tests/test_ralph_control_config.py tests/test_manager_merge_gate.py tests/test_replication_request.py tests/test_management_api.py tests/test_ralph_portable_surprise_resolution.py -q
```

## Your job as manager (priority order — any parallelization OK)

1. **Confirm living-manager registration** on `bb_session_board` for `ccf-4407-ralph-to-aq` with `persistent=true`, `metadata.session_role=manager`, `metadata.keep_open_living_manager=true`. Update task #4407 details accordingly.
2. **Prompt-only kit completeness:** expand operator option catalogs (FE/BE/engines) under interview + ensure abstract-requirements prompt is agent-followable; keep React/FastAPI/Postgres as defaults.
3. **Docker OOTB:** wire React shell + FastAPI management into container path so the image has working management surfaces (not only stdlib `ui/server.py`). Best-guess packaging if Dockerfile layout is ambiguous — document uncertainty in BB.
4. **Host replication:** move broker from dry-run planned actions toward a safe host-executed copy path (still no docker.sock in guest). Respect operator gate + OVERRIDE.
5. **Bootstrap coordination:** message `ccf-kevin-bootstrap` for smoke/validation of containers; do not wait forever — keep building if bootstrap is busy.
6. **Dispatch bounded workers** (claude/codex/cursor via `dispatch_worker`) for large slices; keep manager coordinating. Workers must use isolated worktrees (never shared TB checkout for task-numbered workers).
7. Checkpoint after each slice under `docs/checkpoints/task_4407/`; update task #4407 + BB; tag commits on the AQ branch.

## Hard gates

- No AQ **public** push/merge/visibility change without Kevin/bootstrap explicit gate.
- No production deploy, real-money, destructive git (`push --force`, hard reset), or guest docker.sock.
- No optional questions to Kevin. Escalate only on true hard gates / unsurmountable blockers (use escalation block if present).
- Confidence <65% on high-consequence actions → escalate; otherwise proceed and document.

## First checkpoint (do this immediately)

1. Run the pytest smoke above; record results in task #4407.
2. Post BB event: `ccf-4407-ralph-to-aq` living manager live, pursuing #4407 goal overnight.
3. Choose next implementation slice (prefer Docker React shell or Dockerfile wire-in if prompt pack is already sufficient).
4. Write checkpoint packet with `recommended_task_status_action = keep_in_progress_living_manager`.

## Goal definition of done (stand-down only when true OR Kevin stands you down)

- Prompt-only path: interview 08–12 + abstract requirements clearly allow operator platform switches and produce a buildable contract.
- Docker path: management API + React (or interim honest UI) runnable OOTB from the kit image/profile; health endpoints pass.
- Replication: propose → operator/OVERRIDE → host executes, tested at least at broker/API level.
- Manager-gated merge packet path tested.
- Bootstrap (or you, if bootstrap unavailable) has run the smoke suite and recorded results on task #4407 / BB.
- Checkpoints + BB notes make the overnight work resumable without Kevin.

## Note on “/goal”

Codex has a native `/goal` pursuit mode. Claude Code living managers use **this launch prompt + persistent session + keep_open_living_manager** as the goal-equivalence. Treat every idle moment as: re-read this GOAL file, pick the highest-value unfinished slice, execute, test, checkpoint, repeat.
