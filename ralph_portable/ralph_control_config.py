"""Validate optional ralph_control: block for Autonomy Quest instance.yaml.

Task #4407 — schema gate for the Ralph-control interview pack.
Stdlib + PyYAML (already used by AQ UI/runner).
"""

from __future__ import annotations

from typing import Any

ALLOWED_BUS = frozenset({"http_bus", "chat", "local_mailbox"})
ALLOWED_UI = frozenset({"stdlib_status", "react_fastapi_management", "cli_only"})
ALLOWED_MERGE = frozenset({"operator_gated", "propose_only"})
ALLOWED_APPROVAL = frozenset({"operator_required", "host_override_skip"})
DEFAULT_LADDER = ("ui", "api", "unit")


def validate_ralph_control(block: Any) -> list[str]:
    """Return a list of validation errors (empty means OK).

    Missing / null block is valid (pack off).
    """
    if block is None:
        return []
    if not isinstance(block, dict):
        return ["ralph_control must be a mapping"]

    errors: list[str] = []
    enabled = bool(block.get("enabled", False))

    if not enabled:
        return errors

    roles = block.get("roles") or {}
    if not isinstance(roles, dict):
        errors.append("ralph_control.roles must be a mapping")
    else:
        manager = str(roles.get("manager_handle") or "").strip()
        if not manager:
            errors.append("ralph_control.roles.manager_handle is required when enabled")

    bus = block.get("bus") or {}
    if not isinstance(bus, dict):
        errors.append("ralph_control.bus must be a mapping")
    else:
        kind = str(bus.get("kind") or "").strip()
        if kind not in ALLOWED_BUS:
            errors.append(f"ralph_control.bus.kind must be one of {sorted(ALLOWED_BUS)}")

    ui = block.get("ui") or {}
    if not isinstance(ui, dict):
        errors.append("ralph_control.ui must be a mapping")
    else:
        profile = str(ui.get("profile") or "").strip()
        if profile not in ALLOWED_UI:
            errors.append(f"ralph_control.ui.profile must be one of {sorted(ALLOWED_UI)}")

    validation = block.get("validation") or {}
    if not isinstance(validation, dict):
        errors.append("ralph_control.validation must be a mapping")
    else:
        ladder = validation.get("ladder")
        if ladder is not None:
            if not isinstance(ladder, list) or [str(x) for x in ladder] != list(DEFAULT_LADDER):
                errors.append(
                    "ralph_control.validation.ladder must be [ui, api, unit] in that order"
                )
        merge = str(validation.get("merge_to_main") or "operator_gated").strip()
        if merge not in ALLOWED_MERGE:
            errors.append(
                f"ralph_control.validation.merge_to_main must be one of {sorted(ALLOWED_MERGE)}"
            )
        try:
            attempts = int(validation.get("max_rework_attempts", 3))
            if attempts < 1:
                errors.append("ralph_control.validation.max_rework_attempts must be >= 1")
        except (TypeError, ValueError):
            errors.append("ralph_control.validation.max_rework_attempts must be an integer")

    replication = block.get("replication")
    if replication is not None:
        if not isinstance(replication, dict):
            errors.append("ralph_control.replication must be a mapping")
        elif replication.get("enabled"):
            approval = str(replication.get("approval") or "operator_required").strip()
            if approval not in ALLOWED_APPROVAL:
                errors.append(
                    "ralph_control.replication.approval must be one of "
                    f"{sorted(ALLOWED_APPROVAL)}"
                )

    return errors
