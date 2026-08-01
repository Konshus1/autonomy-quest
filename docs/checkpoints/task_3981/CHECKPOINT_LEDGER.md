# Task #3981 — AQ causal/Ralph port checkpoints

Goal: port Ralph self-improvement / causal-adjacent functionality into
`Konshus1/autonomy-quest` as **agent-followable, testable pieces**, and prove
each slice against a **Docker container COPY** (never mutate bootstrap's live
instance).

Discipline: test as we go; every checkpoint has IDENTITY + evidence + pass/fail.

| Version | Date (UTC) | What | Result |
|---|---|---|---|
| **v0-baseline** | 2026-08-01T02:17Z | Fresh docker COPY of `autonomy-quest:aq3981` → `ckpt-3981-v0-baseline`; host pytest 28 pass / 4 DB-gated fail; UI `/api/state` NOT_ALIVE; DB `aq` + AGE + 9 public tables | PASS (substrate) |
| **v1-portable-core** | 2026-08-01T02:18Z | Port `#3913` `ralph_portable` (SHA `59b00bd7…`) + fixtures + tests into AQ tree; host pytest 3/3; in-container `python -S` dry-run PASS | PASS |

## Authority boundary

- No push/merge to AQ public main without bootstrap/Kevin gate
- No mutation of `aq-3981-parent|clone|variant` containers
- No formal causal-planning claims in AQ v1 public messaging (BB #1229)
- Causal-principle DB/services remain TalkingBack-local until a later portable slice

## Worktrees

- AQ: `/Users/kevincthomas/src/autonomy-quest-wt-3981-causal-port` (`task/3981-causal-port-checkpoints`)
- TB (source): `/Users/kevincthomas/src/talkingback-fastapi/.worktrees/task-3981-aq-causal-port-checkpoints`
- Live test container: `aq-3981-ckpt-v0` (ports 55440/18040, volume `aq-3981-ckpt-v0-data`)
