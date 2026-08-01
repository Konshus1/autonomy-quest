# Interview 10 — Work visualization UI (Ralph-control pack)

*Only if `ralph_control.enabled`.*

## What to establish

They need to see workstreams, tasks, and agent communications.

| Profile | Tradeoff |
|---|---|
| **stdlib status UI** *(AQ default)* | Zero build step; mission/loop health; not a full Control Tower |
| **React + FastAPI management overlay** *(Kevin target kit)* | Workstreams/tasks/comms visualization; heavier; agent may need to build it from prompts |
| **CLI only** | Fine for operators who live in terminals |

Be honest: the shipped AQ container UI is stdlib-only by design. Choosing React+FastAPI means the setup agent **builds or installs an overlay**, not that the base image already is that stack.

## Record

```yaml
ralph_control:
  ui:
    profile: stdlib_status       # stdlib_status | react_fastapi_management | cli_only
    show_workstreams: true
    show_agent_comms: true
```
