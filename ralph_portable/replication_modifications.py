"""Bounded guest-requested modification policy for self-replication Step 2.

Doctrine (self-replication #4834, Step 2 — ``copy_with_modifications``):
- A guest may PROPOSE that its replica be stood up with modifications. It never applies
  them itself; the HOST applies validated deltas into the replica stand-up.
- This module is the CENTERPIECE safety gate: it BOUNDS *what* a guest may modify, so a
  guest requesting changes can never (a) weaken its replica's OWN replication safety gates
  (cap / memory / opt-in / host-executes), (b) grant the replica or its guest docker.sock,
  or (c) set the replica to auto-replicate. A modified replica stays fully sandboxed and
  bounded EXACTLY like a straight_copy.

Design: DENY-BY-DEFAULT.
- CONFIG deltas: an ALLOWLIST of settable env-var keys (mission/instance config, the
  workflow selector, already-guest-safe labels). A key passes ONLY if it is in the
  allowlist. On top of that, a set of hard-forbidden key patterns is asserted to never
  intersect the allowlist (belt-and-suspenders: even a future edit that mistakenly adds a
  dangerous key to the allowlist is caught by a self-consistency test). The denylist names
  the host-security-relevant surfaces explicitly so the rejection message is specific.
- CODE deltas: Step 2 ships CONFIG-DELTAS-ONLY. Code deltas are DEFERRED to Step 3 (the
  interpretable-workflow step). This module still CLASSIFIES a requested code path so the
  hard safety boundary is real and tested today: a code delta touching the safety / gate /
  host code (the replica stand-up, the replication broker/executor, the import firewall,
  container/compose/schema-grant files) is rejected as a SECURITY violation; a delta under
  the designated safe prefixes is recognized as such but still deferred (not applied). We
  never ship an unbounded code-delta path.

This module is PURE (no docker, no subprocess, no network) and therefore safe to be reached
from guest code (the in-container management API imports the allowlist to echo applied
instance config on /health/config). It must NEVER import the host-only docker step.
"""

from __future__ import annotations

from typing import Any

import yaml

# --- CONFIG allowlist (deny-by-default) --------------------------------------
# The ONLY env-var keys a guest may set on its replica. Each is instance/mission config or a
# guest-safe label — nothing that touches a safety gate, a credential, a port/binding, docker,
# or the replica's own replication controls.
CONFIG_ALLOWLIST: frozenset[str] = frozenset(
    {
        "AQ_WORKFLOW_SELECTOR",   # which workflow the replica runs (the headline Step-2/3 use case)
        "AQ_MISSION_LABEL",       # human label for the copied mission
        "AQ_MISSION_NOTE",        # free-text note carried into the replica's instance config
        "AQ_INSTANCE_LABEL",      # display label for the replica instance
        "AQ_MISSION_CADENCE",     # mission cadence hint (e.g. "hourly") — bounded mission config
        "AQ_MISSION_MAX_ATTEMPTS",  # bounded mission retry budget
    }
)

# Hard-forbidden key PATTERNS. A key matching any of these is host-security-relevant and must
# NEVER be guest-settable. Deny-by-default already blocks anything not in the allowlist; this is
# defense-in-depth (a specific reason string, and — via assert_allowlist_is_self_consistent — a
# guard that no allowlisted key can ever match one of these).
_FORBIDDEN_KEY_PREFIXES: tuple[str, ...] = (
    "AQ_REPLICATION_",   # cap / memory gate / auto-approve / daemon-enable / lock — a replica must
    #                      NOT set its OWN replication controls, or it could disable its safety.
)
_FORBIDDEN_KEY_SUBSTRINGS: tuple[str, ...] = (
    "DOCKER", "SOCK",                       # docker / socket anything
    "PASSWORD", "SECRET", "TOKEN", "CREDENTIAL",  # credentials / secrets
    "DB_URL", "DB_OWNER",                   # database owner / connection strings
    "BIND", "PORT",                          # host binding + published ports
)
_FORBIDDEN_KEYS_EXACT: frozenset[str] = frozenset(
    {
        "AQ_INSTANCE_ID",             # host-assigned identity — not guest-forgeable
        "AQ_GOVERNANCE_ADJUDICATOR",  # who adjudicates governance
        "AQ_GOVERNANCE_URL",
        "AQ_EVALUATOR_URL",
        "AQ_DB_URL",
        "AQ_GOVERNED_FEEDBACK",       # governance feedback flag — safety-relevant
        "AQ_LOOP_AUTORESTART",        # loop liveness control
        "CODEX_HOME", "AQ_ACT_WORKSPACE", "AQ_STATE_DIR",
        "COMPOSE_PROJECT_NAME", "AQ_COMPOSE_SECRET_FILE", "GIT_SHA", "PATH",
    }
)

# Config VALUE bounds: config is flat scalars only, rendered into a compose overlay via
# yaml.safe_dump (no interpolation/injection). Reject nested structures and oversized/odd values.
_ALLOWED_VALUE_TYPES = (str, int, float, bool)
_MAX_VALUE_LEN = 512

# --- CODE deltas (classification; Step-2 defers applying them) ---------------
# The ONLY prefixes a code delta may ever target (Step 3 will apply these; Step 2 defers).
CODE_ALLOWED_PREFIXES: tuple[str, ...] = ("workflows/", "plugins/")
# Markers of safety / gate / host code a code delta must NEVER touch. Substring match on the
# normalized (lowercased, forward-slash) path — deliberately broad so near-misses are caught.
_CODE_FORBIDDEN_MARKERS: tuple[str, ...] = (
    "host_replica_stack",       # the docker replica stand-up/teardown
    "host_replication",         # the replication executor / broker / daemon
    "replication_request",      # the request/gate protocol
    "replication_modifications",  # THIS policy module
    "import_firewall",          # the guest/host import wall
    "container/",               # container build assets
    "docker/",
    "dockerfile",
    "docker-compose",
    "compose-with-secrets",
    "schema/999",               # DB grant / owner schema
    ".github/",                 # CI
    "scripts/host_",            # host-only broker/daemon/teardown scripts
)


def forbidden_key_reason(key: str) -> str | None:
    """Return a specific reason a config key is host-security-relevant, or None if not.

    This is the denylist. It is INDEPENDENT of the allowlist: it names *why* a key is
    dangerous. ``assert_allowlist_is_self_consistent`` proves no allowlisted key trips it.
    """
    k = str(key)
    up = k.upper()
    for pref in _FORBIDDEN_KEY_PREFIXES:
        if up.startswith(pref):
            return (
                f"{k!r} is a replication-control key ({pref}*): a replica must not set its own "
                "cap/memory-gate/auto-approve/daemon — that would let it disable its safety gates"
            )
    if k in _FORBIDDEN_KEYS_EXACT:
        return f"{k!r} is a host-security/identity key and is not guest-settable"
    for sub in _FORBIDDEN_KEY_SUBSTRINGS:
        if sub in up:
            return (
                f"{k!r} matches forbidden pattern {sub!r} (docker/socket, credential/secret, "
                "db-owner, or port/host-binding) — not guest-settable"
            )
    return None


def _validate_config_value(key: str, value: Any) -> str | None:
    if isinstance(value, bool):  # bool is an int subclass — accept explicitly
        return None
    if not isinstance(value, _ALLOWED_VALUE_TYPES):
        return f"config {key!r} value must be a scalar (str/int/float/bool), got {type(value).__name__}"
    if isinstance(value, str):
        if len(value) > _MAX_VALUE_LEN:
            return f"config {key!r} value too long ({len(value)} > {_MAX_VALUE_LEN})"
        if any(ord(c) < 32 for c in value):  # no control chars / newlines
            return f"config {key!r} value contains control characters"
    return None


def validate_config_deltas(config: Any) -> list[str]:
    """Deny-by-default validation of the config section. Empty list == accepted."""
    if not isinstance(config, dict):
        return ["config section must be a mapping of env-var key -> scalar value"]
    errors: list[str] = []
    for key, value in config.items():
        if not isinstance(key, str) or not key.strip():
            errors.append(f"config key must be a non-empty string, got {key!r}")
            continue
        reason = forbidden_key_reason(key)
        if reason is not None:
            errors.append("forbidden config key: " + reason)
            continue
        if key not in CONFIG_ALLOWLIST:
            errors.append(
                f"config key {key!r} is not in the allowlist (deny-by-default); "
                f"allowed: {sorted(CONFIG_ALLOWLIST)}"
            )
            continue
        v_err = _validate_config_value(key, value)
        if v_err is not None:
            errors.append(v_err)
    return errors


def classify_code_path(path: Any) -> str:
    """Classify a requested code-delta path: 'forbidden' | 'allowed_prefix' | 'out_of_scope'.

    'forbidden' wins over everything: a path that both sits under an allowed prefix AND trips a
    safety marker is FORBIDDEN (a would-be workflows/ path that smuggles a docker/compose marker).
    """
    p = str(path).strip().replace("\\", "/").lstrip("/").lower()
    if not p:
        return "forbidden"
    if ".." in p.split("/"):  # traversal is always forbidden
        return "forbidden"
    for marker in _CODE_FORBIDDEN_MARKERS:
        if marker in p:
            return "forbidden"
    for pref in CODE_ALLOWED_PREFIXES:
        if p.startswith(pref):
            return "allowed_prefix"
    return "out_of_scope"


def validate_code_deltas(code: Any) -> list[str]:
    """Validate the code section.

    Step 2 is CONFIG-DELTAS-ONLY: code deltas are DEFERRED to Step 3. This still enforces the
    hard safety boundary today — a code delta targeting safety/gate/host code is rejected as a
    SECURITY violation; a delta under a safe prefix is recognized but deferred (not applied).
    We never ship an unbounded code-delta path.
    """
    if code is None:
        return []
    if not isinstance(code, list):
        return ["code section must be a list of {path: ...} deltas"]
    errors: list[str] = []
    for i, delta in enumerate(code):
        if isinstance(delta, str):
            path = delta
        elif isinstance(delta, dict):
            path = delta.get("path")
        else:
            errors.append(f"code[{i}] must be a path string or a mapping with a 'path'")
            continue
        if not isinstance(path, str) or not path.strip():
            errors.append(f"code[{i}] is missing a non-empty 'path'")
            continue
        kind = classify_code_path(path)
        if kind == "forbidden":
            errors.append(
                f"forbidden code path {path!r}: code deltas may NEVER touch safety/gate/host "
                "code (replica stand-up, replication broker/executor/daemon, import firewall, "
                "container/compose/schema-grant files)"
            )
        else:  # allowed_prefix or out_of_scope — not a security breach, but not applied in Step 2
            errors.append(
                f"code delta {path!r} is deferred to Step 3 (Step 2 is config-deltas-only; "
                f"allowed prefixes for Step 3: {list(CODE_ALLOWED_PREFIXES)})"
            )
    return errors


def parse_modifications(mods: Any) -> tuple[dict[str, Any], Any]:
    """Split a modifications packet into (config_dict, code_section).

    Structured form: ``{"config": {..}, "code": [..]}``. Legacy flat form (a bare mapping of
    config keys) is treated wholly as config, so existing callers keep working under the new
    policy (their keys must now be allowlisted).
    """
    if not isinstance(mods, dict):
        return {}, None
    if "config" in mods or "code" in mods:
        config = mods.get("config") or {}
        return (config if isinstance(config, dict) else config), mods.get("code")
    return mods, None  # legacy: whole dict is config


def validate_modification_packet(mods: Any) -> list[str]:
    """Full modification-policy validation. Empty list == accepted (safe to apply).

    Called at BOTH guest propose-time (reject early) AND host apply-time (re-validated against
    the stored packet; the host never trusts what was stored). This is the single source of
    truth for "is this modification bounded and safe".
    """
    if not isinstance(mods, dict) or not mods:
        return ["modifications must be a non-empty mapping"]
    unknown = set(mods) - {"config", "code"}
    structured = "config" in mods or "code" in mods
    errors: list[str] = []
    if structured and unknown:
        errors.append(
            f"unknown modification section(s) {sorted(unknown)}; only 'config' and 'code' are supported"
        )
    config, code = parse_modifications(mods)
    if not config and not code:
        errors.append("modifications must contain at least one config or code delta")
    errors.extend(validate_config_deltas(config))
    errors.extend(validate_code_deltas(code))
    return errors


def allowlisted_config(mods: Any) -> dict[str, Any]:
    """The validated config dict to APPLY. Returns only allowlisted, well-formed entries.

    Callers MUST run ``validate_modification_packet`` first and refuse on any error; this is a
    final belt-and-suspenders filter at the apply point so nothing outside the allowlist can
    ever reach the replica's generated config even if a caller skipped validation.
    """
    config, _code = parse_modifications(mods)
    if not isinstance(config, dict):
        return {}
    clean: dict[str, Any] = {}
    for key, value in config.items():
        if (
            isinstance(key, str)
            and key in CONFIG_ALLOWLIST
            and forbidden_key_reason(key) is None
            and _validate_config_value(key, value) is None
        ):
            clean[key] = value
    return clean


def render_config_overlay(config: dict[str, Any], *, service: str = "app") -> str:
    """A compose overlay that injects allowlisted config env vars into the replica's service.

    Only allowlisted keys are emitted (final filter). Values are rendered via yaml.safe_dump so
    no value can inject compose/YAML structure. Merged after the base compose file + ports
    overlay so the replica container sees the guest's bounded config.
    """
    clean = {k: v for k, v in (config or {}).items()
             if k in CONFIG_ALLOWLIST and forbidden_key_reason(k) is None}
    env_block = {str(k): ("" if v is None else (v if isinstance(v, (int, float, bool)) else str(v)))
                 for k, v in clean.items()}
    doc = {"services": {service: {"environment": env_block}}}
    header = (
        "# GENERATED for a replica stack — bounded guest config deltas (Step 2).\n"
        "# Only allowlisted keys; safe to delete when the replica is torn down.\n"
    )
    return header + yaml.safe_dump(doc, sort_keys=True)


def assert_allowlist_is_self_consistent() -> None:
    """Prove no allowlisted config key can ever be host-security-relevant.

    If this raises, a dangerous key slipped into CONFIG_ALLOWLIST — the deny-by-default gate
    would still block unknown keys, but a self-contradicting allowlist is a bug that must fail
    loud in tests, not ship.
    """
    bad = {k: forbidden_key_reason(k) for k in CONFIG_ALLOWLIST if forbidden_key_reason(k)}
    if bad:
        raise AssertionError(f"allowlisted keys collide with the denylist: {bad}")
