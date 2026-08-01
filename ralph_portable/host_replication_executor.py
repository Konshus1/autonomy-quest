"""Host-side replication executor (Task #4407, slice v9).

The host broker approves a guest replication request, then the HOST performs the
work. This module is the first *real* (non-dry-run) execution step: a safe,
filesystem-level workspace copy that materializes a new instance directory from a
source workspace and applies the mission (+ modifications for
``copy_with_modifications``).

Deliberately NARROW and safe:
- No docker, no docker.sock, no container spin-up, no subprocess, no network.
  Those steps remain the host's later concern; this slice proves the propose →
  approve/OVERRIDE → host-executes chain with a concrete, reversible artifact.
- Writes only under a caller-provided ``dest_root``; refuses to overwrite an
  existing instance dir unless explicitly allowed.
- Only runs from an approved/auto_approved status; never from a guest claim.

See BB #2141/#2144 and the doctrine in ``replication_request``.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import yaml

from ralph_portable.replication_request import (
    advance_host_execution,
    validate_replication_request,
)

_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")


def allocate_instance_id(packet: dict[str, Any]) -> str:
    """Deterministic, filesystem-safe instance id for the copy.

    Deterministic (no timestamp/random) so the same request yields the same id —
    which is also what lets ``dest_root/<id>`` act as a natural idempotency guard.
    """
    requester = str(packet.get("requester_instance_id") or "unknown").strip()
    mission = str(packet.get("mission_id") or packet.get("mission") or "mission").strip()
    raw = f"{requester}--{mission}--copy"
    return _SAFE_ID.sub("-", raw).strip("-").lower()


def execute_replication_copy(
    packet: dict[str, Any],
    *,
    status: str,
    source_workspace: Path | str,
    dest_root: Path | str,
    instance_id: str | None = None,
    allow_existing_dest: bool = False,
) -> dict[str, Any]:
    """Perform a safe host-side workspace copy for an approved request.

    Args:
        packet: the (already accepted) replication request.
        status: current status — must be ``approved`` or ``auto_approved``.
        source_workspace: existing directory to copy (the mission workspace).
        dest_root: parent directory under which the new instance dir is created.
        instance_id: override the allocated id (still sanitized by the caller's choice).
        allow_existing_dest: if False (default), refuse to overwrite an existing dir.

    Returns a host execution record. On any failure the record has
    ``status='failed'`` and an ``error`` string rather than raising for
    operational (expected) problems; programming misuse still raises.
    """
    # Guard 1: the request itself must be well-formed and host-safe.
    errors = validate_replication_request(packet)
    if errors:
        return _failed(packet, instance_id, host_action="validate", error="; ".join(errors))

    # Guard 2: only execute from an approved gate. advance_host_execution raises
    # for a bad status — that is caller misuse (skipping the gate), so let it raise.
    exec_status = advance_host_execution(status)  # -> "host_executing"

    src = Path(source_workspace)
    if not src.is_dir():
        return _failed(packet, instance_id, host_action="locate_source",
                       error=f"source_workspace is not a directory: {src}")

    inst_id = instance_id or allocate_instance_id(packet)
    dest_root_p = Path(dest_root)
    dest = dest_root_p / inst_id

    if dest.exists() and not allow_existing_dest:
        return _failed(packet, inst_id, host_action="allocate_dest",
                       error=f"dest already exists (refusing to overwrite): {dest}")

    host_actions: list[str] = ["allocate_instance_id", "provision_workspace"]
    try:
        dest_root_p.mkdir(parents=True, exist_ok=True)
        if dest.exists() and allow_existing_dest:
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        host_actions.append(f"clone_mode:{packet['mode']}")

        applied: dict[str, Any] = {}
        if packet["mode"] == "copy_with_modifications":
            applied = dict(packet.get("modifications") or {})
            _write_overrides(dest, applied)
            host_actions.append("apply_modifications")

        _write_manifest(dest, packet, inst_id, applied)
        host_actions.append("apply_mission")
    except OSError as exc:  # pragma: no cover - defensive
        return _failed(packet, inst_id, host_action=",".join(host_actions), error=str(exc))

    return {
        "ok": True,
        "executed_by": "host",
        "status": "completed",
        "prior_status": exec_status,
        "instance_id": inst_id,
        "mode": packet["mode"],
        "created_path": str(dest),
        "host_action": ",".join(host_actions),
        "applied_modifications": applied,
        "used_docker_sock": False,
    }


def _write_overrides(dest: Path, modifications: dict[str, Any]) -> None:
    """Persist mission modifications the host applied to the copy."""
    (dest / "instance_overrides.yaml").write_text(
        yaml.safe_dump(modifications, sort_keys=True)
    )


def _write_manifest(
    dest: Path, packet: dict[str, Any], inst_id: str, applied: dict[str, Any]
) -> None:
    """Record provenance so a replicated instance is traceable to its request."""
    manifest = {
        "task": 4407,
        "instance_id": inst_id,
        "mode": packet["mode"],
        "mission_id": packet.get("mission_id") or packet.get("mission"),
        "requester_instance_id": packet.get("requester_instance_id"),
        "applied_modifications": applied,
        "executed_by": "host",
        "used_docker_sock": False,
    }
    (dest / "replication_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True)
    )


def _failed(
    packet: Any, instance_id: str | None, *, host_action: str, error: str
) -> dict[str, Any]:
    return {
        "ok": False,
        "executed_by": "host",
        "status": "failed",
        "instance_id": instance_id,
        "mode": packet.get("mode") if isinstance(packet, dict) else None,
        "host_action": host_action,
        "error": error,
        "used_docker_sock": False,
    }
