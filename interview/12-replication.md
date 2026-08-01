# Interview 12 — Replication / copy requests (Ralph-control pack)

*Only if they chose the Docker/container substrate, or want host-mediated copies of a native instance.*

## Doctrine to explain

A running instance may request:

- **straight_copy** — new instance with an identified mission
- **copy_with_modifications** — copy + apply requested deltas (config and/or code), for experiments before adopting changes

**Default: operator approval required.** The guest only proposes.

**OVERRIDE:** the **host** may set env `AQ_REPLICATION_AUTO_APPROVE=1` (also `true`/`yes`/`on`) so the host auto-approves without waiting for an operator click. Say out loud that this is powerful and risky.

**Who executes:** always the **host machine**. The guest never gets `docker.sock` and never performs the copy itself. Even with OVERRIDE, the host still does the work.

## Record

```yaml
ralph_control:
  replication:
    enabled: false
    modes: [straight_copy, copy_with_modifications]
    approval: operator_required   # operator_required | host_override_skip
    # Host env OVERRIDE (not stored as a secret in yaml): AQ_REPLICATION_AUTO_APPROVE
    override_env: AQ_REPLICATION_AUTO_APPROVE
    host_executes: true           # always; guest proposes only
    host_broker: "operator_ui"
```
