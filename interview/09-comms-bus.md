# Interview 9 — Communications bus (Ralph-control pack)

*Only if `ralph_control.enabled`.*

## What to establish

Agents must share a bus — not only terminal paste.

| Option | When to choose |
|---|---|
| **HTTP agent bus** *(preferred if they already have TalkingBack-class APIs)* | Multi-session, multi-type agents, durable message history |
| **Chat channel** (Discord/Slack) | Human-visible, weaker protocol guarantees |
| **Local mailbox** (DB/table or files) | Single-box, minimal deps |

Ask what protocols they need beyond free text (task claims, handoffs, rework requests, spawn requests).

## Record

```yaml
ralph_control:
  bus:
    kind: http_bus               # http_bus | chat | local_mailbox
    endpoint: ""                 # if http_bus
    protocols:
      - task_claim
      - handoff
      - rework_request
      - spawn_request
```
