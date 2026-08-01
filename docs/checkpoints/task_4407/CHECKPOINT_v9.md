# Checkpoint v9 — Safe host-executed replication copy

**Task:** #4407 · **Session:** ccf-4407-ralph-to-aq (living manager)
**Branch:** task/3981-causal-port-checkpoints · **Date:** 2026-08-01
**recommended_task_status_action:** `keep_in_progress_living_manager`

## What landed

The host broker previously stopped at `host_executing` with *planned* action strings —
nothing was materialized. v9 makes the host actually execute, at the safest useful level.

- **`ralph_portable/host_replication_executor.py`** — `execute_replication_copy()`: for an
  approved/auto_approved request, materializes a new instance directory from a source
  workspace: copies the tree, writes `instance_overrides.yaml` for `copy_with_modifications`,
  and writes `replication_manifest.json` (provenance → request). Deterministic
  `allocate_instance_id()` doubles as an idempotency guard.
- **`scripts/host_replication_broker.py`** — new `--source-workspace` / `--dest-root` /
  `--allow-existing-dest`. With source+dest under `--execute`, performs the REAL copy;
  without them, the original dry-run plan path is unchanged (backward compatible).
- **`tests/test_host_replication_executor.py`** — 8 tests: deterministic id, straight_copy,
  copy_with_modifications overrides, refuse-without-approval, refuse-overwrite, reject
  docker.sock request, + 2 broker subprocess tests (real copy via OVERRIDE, gate blocks).

## Safety envelope (explicit)

- **No docker, no docker.sock, no container spin-up, no subprocess, no network.** Container
  provisioning remains a later host-only step (BB #2144/#3981).
- Writes only under a caller-provided `dest_root`; refuses to overwrite by default.
- Executes only from an approved gate; guest requests carrying `requires_docker_sock` /
  `guest_executes` are rejected upstream by `validate_replication_request`.

## Verification (ground truth)

- **34 pytest green** (full smoke incl. v9).
- **Live broker demo**: without OVERRIDE → `awaiting_operator`, `ok=False` (operator gate holds);
  `AQ_REPLICATION_AUTO_APPROVE=1` → `completed`, `mode_of_execution=host_copy`,
  `used_docker_sock=False`, instance dir materialized with `instance.yaml`, `runner.txt`,
  `instance_overrides.yaml`, `replication_manifest.json`.

## DoD impact

Satisfies: *"Replication: propose → operator/OVERRIDE → host executes, tested at least at
broker/API level."* Container-level provisioning is the remaining (host-host) step.
