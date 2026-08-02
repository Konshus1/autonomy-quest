"""Arm an edge with a formal executor (FORMAL_LAYER_SPEC §2a). PURE: no I/O beyond the registry's
digest verification, no side effects on the store. Attaching sets the executor + arms the dials
(directness=script, strictness=hard) and marks it UNVERIFIED — it does NOT touch formality, identity,
or support_count. So arming a fuzzy edge leaves it fuzzy; only a passing oracle proof (verify) then
an operator/CI promotion can advance formality.
"""
from __future__ import annotations

from typing import Any

from ralph_portable.formal.executor_registry import ExecutorRegistry


def attach_executor(edge: dict[str, Any], registry_key: str, registry: ExecutorRegistry) -> dict[str, Any]:
    """Return a NEW armed edge dict. Raises if the encoding's digest fails or its `binds` does not
    match this edge's (cause, effect) — an encoding can never be stapled onto an unrelated edge.

    Invariants preserved: identity (cause, effect, scope) unchanged; formality unchanged;
    support_count/evidence untouched; executor_verified forced False (a fresh executor is unproven).
    """
    asset = registry.verify_payload(registry_key)  # digest-verified; raises RegistryError on mismatch
    binds = asset["binds"]
    if str(binds.get("cause")) != str(edge.get("cause")) or str(binds.get("effect")) != str(edge.get("effect")):
        raise ValueError(
            f"encoding {registry_key!r} binds (cause={binds.get('cause')!r}, effect={binds.get('effect')!r}) "
            f"does not match edge (cause={edge.get('cause')!r}, effect={edge.get('effect')!r})")

    armed = dict(edge)
    armed["executor"] = {"kind": asset["kind"], "ref": registry_key, "lang": asset["lang"]}
    armed["directness"] = "script"   # ARMED dials (spec §1): a guaranteed action needs a runnable ref
    armed["strictness"] = "hard"
    armed["executor_verified"] = False
    armed["executor_provenance"] = {
        "registry_key": registry_key,
        "digest": asset["digest"],
        "oracle_digest": asset["oracle_digest"],
    }
    # formality, cause, effect, scope, support_count, evidence — all deliberately UNCHANGED.
    return armed
