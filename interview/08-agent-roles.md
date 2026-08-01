# Interview 8 — Agent roles (Ralph-control pack)

*Optional pack for multi-agent task control. Skip if the human only wants the single-loop AQ instance.*

**Default:** one resident engine does the loop; no dedicated worker/evaluator/manager fleet.

## What to establish

If they want a Ralph-style fleet (workers + evaluators + managers coordinating on a bus):

1. **Which agents can be workers?** (Claude Code / Codex / Copilot / API-only / other)
2. **Which agents can be evaluators?** Prefer a different model/account than the worker when possible.
3. **Who is the manager for each worker?** Manager is always required when fleet mode is on — no orphan workers.
4. **Non-coding agents allowed?** (ops bots, OpenClaw-style inbox agents, research agents) — yes/no; if yes, they still speak the bus protocol.

## Record

```yaml
ralph_control:
  enabled: false                 # default off
  roles:
    worker_engines: []           # e.g. [codex, claude_code]
    evaluator_engines: []
    manager_handle: ""           # required if enabled
  allow_non_coding_agents: false
```

## After this file

Continue to `09-comms-bus.md` if `ralph_control.enabled`, else skip to install readiness / remaining AQ interview files already completed.
