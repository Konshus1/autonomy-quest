"""Host-mediated replication request protocol (Task #4407).

Doctrine:
- Guest proposes only (straight_copy | copy_with_modifications).
- Default: operator approval required.
- Host OVERRIDE env AQ_REPLICATION_AUTO_APPROVE=1|true|yes skips operator wait.
- Host (not guest) performs the actual replication work.
"""

from __future__ import annotations

import os
from typing import Any

ALLOWED_MODES = frozenset({"straight_copy", "copy_with_modifications"})
ALLOWED_STATUS = frozenset(
    {
        "proposed",
        "awaiting_operator",
        "auto_approved",
        "approved",
        "rejected",
        "host_executing",
        "completed",
        "failed",
    }
)
OVERRIDE_ENV = "AQ_REPLICATION_AUTO_APPROVE"


def override_enabled(env: dict[str, str] | None = None) -> bool:
    """True when host OVERRIDE allows auto-approval."""
    source = env if env is not None else os.environ
    raw = str(source.get(OVERRIDE_ENV, "")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def validate_replication_request(packet: Any) -> list[str]:
    """Validate a guest→host replication request packet."""
    if not isinstance(packet, dict):
        return ["replication_request must be a mapping"]

    errors: list[str] = []
    mode = str(packet.get("mode") or "").strip()
    if mode not in ALLOWED_MODES:
        errors.append(f"mode must be one of {sorted(ALLOWED_MODES)}")

    mission = str(packet.get("mission_id") or packet.get("mission") or "").strip()
    if not mission:
        errors.append("mission or mission_id is required (identified mission for the copy)")

    requester = str(packet.get("requester_instance_id") or "").strip()
    if not requester:
        errors.append("requester_instance_id is required")

    if mode == "copy_with_modifications":
        mods = packet.get("modifications")
        if not isinstance(mods, dict) or not mods:
            errors.append(
                "modifications mapping is required for copy_with_modifications"
            )
        else:
            # BOUNDED modification policy (#4834 Step 2): deny-by-default over WHAT a guest may
            # modify. Enforced HERE so every caller — the guest propose endpoint AND both host
            # apply sites (the filesystem copy executor and the docker stand-up), which each
            # re-run this validator — reject a forbidden delta. Re-validated at apply: the host
            # never trusts the stored packet. Imported lazily to keep this module import-cheap.
            from ralph_portable.replication_modifications import (
                validate_modification_packet,
            )

            errors.extend(validate_modification_packet(mods))

    # Guest must never claim it will execute replication itself.
    if packet.get("guest_executes") is True:
        errors.append(
            "guest_executes must not be true — host performs replication"
        )
    if packet.get("requires_docker_sock") is True:
        errors.append("requires_docker_sock is forbidden for guest requests")

    return errors


def initial_status_for_host(env: dict[str, str] | None = None) -> str:
    """Status after a valid request is accepted by the host broker."""
    if override_enabled(env):
        return "auto_approved"
    return "awaiting_operator"


def advance_host_execution(status: str) -> str:
    """Host-only state advance after approval/auto-approval."""
    if status in {"approved", "auto_approved"}:
        return "host_executing"
    raise ValueError(
        f"host cannot execute from status={status!r}; need approved or auto_approved"
    )


def validate_host_execution_record(packet: Any) -> list[str]:
    """Validate the host's execution record (who did the work)."""
    if not isinstance(packet, dict):
        return ["host_execution_record must be a mapping"]

    errors: list[str] = []
    executor = str(packet.get("executed_by") or "").strip()
    if executor != "host":
        errors.append("executed_by must be 'host'")

    status = str(packet.get("status") or "").strip()
    if status not in ALLOWED_STATUS:
        errors.append(f"status must be one of {sorted(ALLOWED_STATUS)}")

    if status in {"completed", "failed", "host_executing"}:
        if not str(packet.get("host_action") or "").strip():
            errors.append("host_action is required once host is executing")

    return errors
