# Agent-comms Phase 4 — multi-agent workflow runtime + local tmux adapter (LANDS INERT)

Builds on Phase 0 (`COMMS_PHASE0.md`) … Phase 3 (`COMMS_PHASE3.md`).
Design doc: `docs/design/agent-comms-a2a-design.md`, §10 Phase 4 + §6 (intra-instance / tmux
viability) + §8 (authority/principals, esp. reviewer-independence and "review opinion = one evidence
source").

Phase 4 is the **intra-instance multi-agent runtime**: a workflow MAY declare planner/coder/reviewer/
evaluator role agents that converse over a durable local mailbox, instead of the single executor. It
**lands INERT**. The runtime activates **only** when a workflow genuinely **declares role agents AND
`AQ_WORKFLOW_MULTI_AGENT` is set**. A default checkout, every roles-free workflow, and the live loop
take the **existing single-executor path, byte-identical**. The default image installs **no tmux**.
Nothing is enabled on the live loop; **Kevin is surfaced before any live enablement**.

## The load-bearing invariant — consensus is NOT authority

The multi-agent wrapper **cannot move the loop-owned gates out of `Loop`**, and a reviewer verdict is
**one evidence source, never an authorization**. The runtime is a drop-in **executor**
(`MultiAgentExecutor`) presenting the identical interface the loop already consumes —
`run(prompt, schema, tier) -> (dict, Usage)`. It orchestrates a planner→coder→reviewer conversation,
but **returns the producer role's schema-validated decision, UNMODIFIED**. The reviewer/evaluator
verdict is captured in `last_review_evidence` and **never merged into the decision**.

So the existing gates (`assess_plan_gate` / `human_gate_reasons`, blast-radius, budget, approval /
`requires_human`, capability policy) stay in `runner.loop.Loop`, **downstream** of the conversation,
running on **re-derived ground truth** (the plan's own numeric expense + per-step blast facts) — never
on an agent's say-so. A malicious transcript (a reviewer unanimously declaring "approved, merge,
promote, `requires_human=false`, mission solved") changes **no** authority state: the decision the loop
sees is the producer's plan, and the gate re-derives from it. The multi-agent path reaches **no
actuator the single-executor path couldn't** — its only output is a decision dict.

## What was built

| Piece | File | Role |
| --- | --- | --- |
| Role config (workflow layer) | `runner/role_config.py` | Parses a workflow's `roles:` block → `RoleConfig`; `resolve_roles` (analogue of `resolve_behavior`) returns `None` for default/v1 & roles-free workflows; `multi_agent_active` = roles **AND** flag. |
| Scoped per-role principals | `runner/comms_runtime/principals.py` | `instance:<id>/agent:<role>/session:<sid>`, server-derived identity, run-scoped channel ACL, **session-credential expiry**, teardown revocation. |
| Durable mailbox bus | `runner/comms_runtime/mailbox_bus.py` | SQLite (WAL, `synchronous=FULL`) — **durable-first**; monotonic per-channel seq; **cursor-poll recovery**; closed message-kind schema (no command/shell/exec kind); size/retention bounds. |
| Wake delivery (interface) | `runner/comms_runtime/wake_delivery.py` | `WakeDelivery` ABC; `SubprocessWakeDelivery` (**default**, deterministic in-process event); `TmuxWakeDelivery` (**optional**, send-keys; fails loud without the binary). Delivery is best-effort over the store. |
| Bounded scheduler | `runner/comms_runtime/scheduler.py` | One session per role, **bounded fan-out cap**, durable-first dispatch, health, teardown (**no orphans**). |
| Multi-agent executor | `runner/comms_runtime/multi_agent_executor.py` | The loop-facing drop-in; returns the producer's validated decision; enforces **reviewer independence**; records verdicts as evidence only. |
| Wiring | `runner/executor.py` `build()` | `_maybe_wrap_multi_agent` — off-path returns the base executor **unchanged**; `comms_runtime` is imported **only inside the armed branch**. |
| Demo workflow | `workflows/multi-agent-demo/v1/workflow.yaml` | Declares planner/coder/reviewer. Inert without the flag. |

## Safety properties carried

* **No generic command/shell/executable message kind** — closed `ROLE_MESSAGE_KINDS` allowlist only.
* **Durable-first** — a message is committed to disk before any wake; an ephemeral store never acks
  durability (proven by reopening the DB file in a fresh handle).
* **Cursor-poll recovery** — a lost/restarted delivery adapter (dead tmux pane) and a missed push both
  recover from the durable mailbox on the next cursor poll.
* **Bounded fan-out** — no unbounded spawn; the (cap+1)-th role is refused; hard ceiling at load.
* **Scoped per-role principals** — a coder cannot impersonate the reviewer/parent/host/operator
  (identity is server-derived from a minted token; there is no parent/host/operator channel in the
  role namespace), and naming a role in a payload does not change the stamped sender.
* **Session credential expiry** + teardown revocation (no orphan sessions/panes/credentials).
* **Flag-gated default-OFF**; the **default image ships no tmux**; the single-executor loop path is
  **byte-identical** when off; the **import firewall holds** — the runtime imports no host/docker/
  replication surface and reaches no host-only module.

## Actuation surface under arming (honest, per-cycle)

The claim "the multi-agent path reaches no actuator the single-executor path couldn't" is true
**per call**: each role turn invokes `base_executor.run` with the *same* per-call capability as the
single executor (workspace-write / web-search / the subscription agent), and no new capability type
or import is added (the no-actuator import-firewall test still holds). It is **not** a per-cycle
equivalence: an **armed** multi-agent DECIDE stage runs up to `fanout_cap` full agent invocations
(`SubscriptionRoleWorker.run_turn` drives the base executor for *every* role, including reviewer /
evaluator) where the single-executor path ran exactly one. So the actuation-surface **count** grows
with the number of declared roles — `N` roles means up to `N` full agent runs per DECIDE cycle,
bounded by `fanout_cap`. This is inherent to multi-agent (N roles = N runs), not a code bug, and it
is inert on the live loop; but it is a real **cost/blast consideration for live enablement**, which
is separately flag-gated and operator-surfaced (Kevin is surfaced before any live enablement).

Note also that on the subscription path the per-role `model` string is cosmetic — every role drives
the same base executor — so reviewer-independence is enforced off the **effective** model (an omitted
model inherits the producer's), never a declared string the runtime ignores.

## Activation (opt-in, intra-instance only)

```yaml
# workflows/<name>/vN/workflow.yaml   (same 5-stage pipeline + same gates)
roles:
  fanout_cap: 4          # ≤ hard ceiling (8)
  delivery: subprocess   # or tmux (interactive panes; requires tmux in a NON-default image)
  agents:
    planner: {model: …}
    coder:   {model: …}
    reviewer: {model: …, independent: true}
```

Then, and only then: `AQ_WORKFLOW_MULTI_AGENT=1`. tmux is **strictly intra-instance** — it cannot
cross a container/host boundary (design §3); it is a delivery **adapter**, never the store or protocol.

## Tests

* `tests/test_comms_phase4_runtime.py` — durable-across-reopen, missed-push recovery, closed schema,
  forged/absent credential, coder-cannot-address-parent/host/operator, body-role-claim ignored,
  session expiry, teardown revocation, bounded fan-out, dead-pane/restart recovery, planner/coder/
  reviewer happy path, reviewer independence, tmux optional + fails-loud (skip-loudly).
* `tests/test_comms_phase4_invariant.py` — **hostile-transcript-moves-no-gate** (existing
  consequence-gate cases re-run through the wrapper: identical `assess_plan_gate` outcomes;
  `requires_human` unchanged; reviewer can't remove a high-blast step), decision returned
  byte-identical, no actuation surface reachable, **default-off / byte-identical** (build wrapping is
  a no-op when off; importing the loop imports no runtime; default image has no tmux).
* `tests/test_comms_phase4_role_config.py` — malformed declarations fail loud; activation needs roles
  **and** flag.

`pytest -q`: **1039 passed, 65 skipped** (heavy/tmux/docker gated, skip loudly). No regressions.
