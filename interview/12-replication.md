# Interview 12 — Replication / copy requests (Ralph-control pack)

*Only if they chose the Docker/container substrate, or want host-mediated copies of a native instance.*

## Doctrine to explain

A running instance may request:

- **straight_copy** — new instance with an identified mission
- **copy_with_modifications** — copy + apply requested deltas (config and/or code), for experiments before adopting changes

Default: **operator approval required**. Host may configure an override to skip that gate — treat as dangerous and say so out loud.

Public kit posture: the guest proposes; the **host** executes. Guests do not get docker.sock.

## Record

```yaml
ralph_control:
  replication:
    enabled: false
    modes: [straight_copy, copy_with_modifications]
    approval: operator_required   # operator_required | host_override_skip
    host_broker: ""               # how the host receives requests
```
