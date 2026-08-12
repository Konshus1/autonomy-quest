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

from ralph_portable.replication_modifications import (
    allowlisted_config,
    validate_modification_packet,
)
from ralph_portable.replication_request import (
    advance_host_execution,
    validate_replication_request,
)

_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")
_DOTS = re.compile(r"\.{2,}")   # collapse runs of dots so ".." traversal cannot survive


def _safe_component(raw: Any) -> str | None:
    """A single, traversal-proof path component, or None if nothing safe remains.

    Path separators are not in the allow-set so they become ``-``; runs of dots are
    collapsed to a single dot so ``..`` can never survive; leading/trailing dots and
    dashes are stripped. Guarantees: no ``/``, ``\\``, or ``..`` in the result.
    """
    s = _SAFE_ID.sub("-", str(raw))
    s = _DOTS.sub(".", s).strip("-.").lower()
    if not s or s in {".", ".."} or ".." in s or "/" in s or "\\" in s:
        return None
    return s


def allocate_instance_id(packet: dict[str, Any]) -> str:
    """Deterministic, filesystem-safe instance id for the copy.

    Deterministic (no timestamp/random) so the same request yields the same id —
    which is also what lets ``dest_root/<id>`` act as a natural idempotency guard.
    """
    requester = str(packet.get("requester_instance_id") or "unknown").strip()
    mission = str(packet.get("mission_id") or packet.get("mission") or "mission").strip()
    raw = f"{requester}--{mission}--copy"
    return _safe_component(raw) or "instance"


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

    # Guard 1b: BOUNDED modification policy (#4834 Step 2) — re-validated HOST-SIDE, never trusting
    # the stored packet. A forbidden config/code delta fails the copy before any file is written.
    if packet.get("mode") == "copy_with_modifications":
        mod_errors = validate_modification_packet(packet.get("modifications"))
        if mod_errors:
            return _failed(packet, instance_id, host_action="modification_policy",
                           error="; ".join(mod_errors))

    # Guard 2: only execute from an approved gate. advance_host_execution raises
    # for a bad status — that is caller misuse (skipping the gate), so let it raise.
    exec_status = advance_host_execution(status)  # -> "host_executing"

    src = Path(source_workspace)
    if not src.is_dir():
        return _failed(packet, instance_id, host_action="locate_source",
                       error=f"source_workspace is not a directory: {src}")

    # Sanitize the instance id whether it was allocated OR passed in by the caller — an
    # explicit override must not be a traversal vector.
    inst_id = _safe_component(instance_id) if instance_id is not None else allocate_instance_id(packet)
    if not inst_id:
        return _failed(packet, instance_id, host_action="allocate_dest",
                       error=f"unsafe instance_id (path traversal rejected): {instance_id!r}")

    dest_root_p = Path(dest_root)
    dest = dest_root_p / inst_id

    # CONTAINMENT: the resolved dest MUST live strictly under the resolved dest_root.
    # This is the last line of defense before any mkdir/rmtree/copytree — it stops a
    # crafted id from making us write to, or delete, anything outside dest_root.
    dest_root_real = dest_root_p.resolve()
    dest_real = dest.resolve()
    if dest_real == dest_root_real or not dest_real.is_relative_to(dest_root_real):
        return _failed(packet, inst_id, host_action="allocate_dest",
                       error=f"dest escapes dest_root (refused): {dest_real} not under {dest_root_real}")

    if dest.exists() and not allow_existing_dest:
        return _failed(packet, inst_id, host_action="allocate_dest",
                       error=f"dest already exists (refusing to overwrite): {dest}")

    host_actions: list[str] = ["allocate_instance_id", "provision_workspace"]
    try:
        dest_root_p.mkdir(parents=True, exist_ok=True)
        if dest.exists() and allow_existing_dest:
            shutil.rmtree(dest)
        # symlinks=True copies links AS links — never dereferences them — so a source
        # symlink pointing outside the workspace cannot exfiltrate host file CONTENTS
        # into the replica.
        shutil.copytree(src, dest, symlinks=True)
        # ...but a PRESERVED symlink that escapes dest_root is a write-follow vector: a
        # source link named e.g. instance_overrides.yaml -> /outside would make the
        # provenance writes below land OUTSIDE dest_root. Strip every escaping symlink
        # before we write anything into the replica.
        _strip_escaping_symlinks(dest, dest_root_real)
        host_actions.append(f"clone_mode:{packet['mode']}")

        applied: dict[str, Any] = {}
        if packet["mode"] == "copy_with_modifications":
            # Guard 1 already re-validated the modification policy (validate_replication_request
            # now enforces it). Apply ONLY the allowlisted config — never the raw stored dict — so
            # a key outside the bound can never land in the replica's overrides. Defense in depth.
            applied = allowlisted_config(packet.get("modifications"))
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


def _strip_escaping_symlinks(root: Path, boundary: Path) -> None:
    """Remove every symlink under ``root`` whose target is not strictly inside ``boundary``.

    Preserves contained (relative, in-tree) symlinks; deletes escaping or broken ones so
    no later write can follow a link out of dest_root. Depth-first so a link to a dir is
    removed before we descend into a (now-gone) child.
    """
    for p in sorted(root.rglob("*"), key=lambda q: len(q.parts), reverse=True):
        if p.is_symlink():
            try:
                contained = p.resolve().is_relative_to(boundary)
            except OSError:
                contained = False
            if not contained:
                p.unlink()


def _fresh_write(path: Path, text: str) -> None:
    """Write a fresh REGULAR file, never through a pre-existing symlink at ``path``."""
    if path.is_symlink() or path.exists():
        path.unlink()
    path.write_text(text)


def _write_overrides(dest: Path, modifications: dict[str, Any]) -> None:
    """Persist mission modifications the host applied to the copy."""
    _fresh_write(dest / "instance_overrides.yaml", yaml.safe_dump(modifications, sort_keys=True))


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
    _fresh_write(dest / "replication_manifest.json", json.dumps(manifest, indent=2, sort_keys=True))


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
