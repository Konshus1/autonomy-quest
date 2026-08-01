# Prompt-only kit — abstract requirements (best guess)

Uncertainty: exact option menus may expand; this is the contract a setup coding agent must meet regardless of FE/BE choice. Documented in BB #2144.

## Abstract components (must exist after setup)

1. **Roles:** worker, evaluator, manager (manager always specified when fleet on).
2. **Comms bus** with protocols: task_claim, handoff, rework_request, spawn_request.
3. **Visualization:** workstreams, tasks, agent communications (not terminal-only).
4. **Validation ladder:** UI > API > unit; evaluator intent adequacy + pass on worktree.
5. **Integration cohort** on shared branch with live FE/BE/DB.
6. **Manager-gated merge:** approve_merge | escalate_human.
7. **Replication requests:** guest propose only; host executes; operator gate + `AQ_REPLICATION_AUTO_APPROVE` OVERRIDE.
8. **Causal principles (optional advanced):** if enabled, formal plan must satisfy relevant principles — not an AQ v1 public claim.

## Defaults (operator may switch)

| Slot | Default | Examples of switches |
|---|---|---|
| Frontend | React | SvelteKit, Vue, Next, HTMX+server templates |
| Backend | FastAPI | Django, Express, Rails, Go chi |
| Datastore | Postgres + pgVector + AGE | Postgres alone (graph features degrade) |
| Worker engines | whatever interview found | Codex / Claude Code / Copilot / API |
| Bus | http_bus or local_mailbox | chat channel |

## Setup-agent instruction (paste into prompt kit)

> Build the abstract components above on the platforms recorded in `instance.yaml` under `ralph_control.platforms`. If the operator kept defaults, implement React + FastAPI + Postgres/pgVector/AGE. If they switched, still deliver the same components — do not silently fall back to defaults. Do not grant guests docker.sock. Replication is host-mediated. Cohort merge is manager-gated.

## Uncertainty

- Exact UI IA for workstreams/comms is not fully specified → best guess: three primary views (Workstreams, Tasks, Agent Comms) + manager merge decision panel.
- Non-coding agent adapters → stub protocol handlers first; deep OpenClaw parity later.
