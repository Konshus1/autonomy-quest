# Task #4407 — Bootstrap / goal-machine handoff charter

**Use this if Cursor is offline and work must continue overnight.**

## Intent (locked)

1. Port Ralph multi-agent control workflow into Autonomy Quest as two kits:
   - **Docker OOTB:** React + FastAPI + Postgres/pgVector/AGE **working in the image**.
   - **Prompt-only:** abstract requirements; React/FastAPI/Postgres defaults; operator may pick other FE/BE; agent builds the same abstract components.
2. Workflow: causal principles → formal plan → breakdown → UI>API>unit tests → evaluator intent+pass → integration cohort → rework/max→manager → **manager-gated** merge (approve or escalate to human) → retire sessions.
3. Replication: guest proposes; **operator-gated by default**; host env `AQ_REPLICATION_AUTO_APPROVE` OVERRIDE; **host executes** replication.
4. No optional questions to Kevin. Document uncertainty in BB; best-guess implement; test.

## Where to work

- AQ branch/worktree: `/Users/kevincthomas/src/autonomy-quest-wt-3981-causal-port` (`task/3981-causal-port-checkpoints`)
- TB clean wt (goal rules): `/Users/kevincthomas/src/talkingback-fastapi/.worktrees/task-3981-aq-causal-port-checkpoints`
- Task **#4407**, BB notes **#2141**, **#2144**
- Checkpoints: `docs/checkpoints/task_4407/`

## Already done (do not redo)

- Workflow map, interview 08–12, `ralph_control` validator, manager_merge_gate, replication_request + OVERRIDE
- Tags: `ckpt-4407-v0` … `ckpt-4407-v3-*`

## Next work (any order OK)

1. Expand prompt-kit option catalogs (FE/BE/engine choices) + abstract requirements prompt file.
2. Docker kit: FastAPI+React management skeleton that runs in container.
3. Wire host replication broker script that reads OVERRIDE and refuses guest execution.
4. Bootstrap smoke: run portable pytest suite; start ckpt container; verify `/api` health when skeleton exists.
5. Keep testing; checkpoint after each slice; update task #4407.

## Tests to run now

```sh
cd /Users/kevincthomas/src/autonomy-quest-wt-3981-causal-port
python -m pytest tests/test_ralph_control_config.py tests/test_manager_merge_gate.py tests/test_replication_request.py tests/test_ralph_portable_surprise_resolution.py -q
```

## Rule

Proceed with best guesses. Do not stop for non-blocking questions. Escalate only on destructive/public push/merge gates.
