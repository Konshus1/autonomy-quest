"""Canonical hashes for queue intent and mutable orchestration evidence.

The intent digest is deliberately narrower than a task-row version digest.  Worker,
review, readiness and receipt writes are expected to change while a candidate is being
processed; none of those writes may silently rewrite the contract that was admitted.
"""
from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Mapping

INTENT_DOMAIN = "aq-close-loop/task-intent/v1"
OBSERVATION_DOMAIN = "aq-close-loop/source-observation/v1"
MISSION_DOMAIN = "aq-close-loop/mission-boundary/v1"

INTENT_REQUIRED_FIELDS = frozenset(
    {
        "repo_id",
        "target_ref",
        "scope",
        "definition_of_done",
        "stop_condition",
        "verifier_manifest",
        "authority",
    }
)
INTENT_OPTIONAL_FIELDS = frozenset(
    {
        "dependencies",
        "expected_changed_paths",
        "protected_paths",
        "risk_class",
    }
)


class IntentContractError(ValueError):
    """The admitted immutable-intent envelope is missing or ambiguous."""


@dataclass(frozen=True)
class SourceHashes:
    intent_hash: str
    observation_hash: str


def _normalise(value: Any) -> Any:
    """Return a deterministic, JSON-safe value or fail instead of guessing."""
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite numbers are not canonical JSON")
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non-finite decimals are not canonical JSON")
        return str(value)
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, datetime):
        stamp = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        stamp = stamp.astimezone(timezone.utc)
        return stamp.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("canonical JSON object keys must be strings")
            normalised_key = unicodedata.normalize("NFC", key)
            if normalised_key in out:
                raise ValueError("canonical JSON has duplicate normalised keys")
            out[normalised_key] = _normalise(item)
        return out
    if isinstance(value, (list, tuple)):
        return [_normalise(item) for item in value]
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Encode canonical JSON with stable keys, Unicode and numeric handling."""
    return json.dumps(
        _normalise(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_digest(domain: str, value: Any) -> str:
    """Domain-separated SHA-256 over canonical UTF-8 JSON."""
    if not domain or "\x00" in domain:
        raise ValueError("hash domain must be non-empty and contain no NUL")
    material = domain.encode("utf-8") + b"\x00" + canonical_json(value).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _nonempty(value: Any, field: str) -> None:
    if value is None or value == "" or value == [] or value == {}:
        raise IntentContractError(f"immutable intent field {field!r} must be non-empty")


def task_intent_payload(task: Mapping[str, Any]) -> dict[str, Any]:
    """Extract the strict, immutable semantic contract from a source task.

    Mutable evidence belongs beside, never inside, ``details.aq_phase1.intent``.
    Unknown keys fail closed so a newly introduced semantic field cannot be ignored by
    an older gate while still influencing a worker.
    """
    details = task.get("details")
    if not isinstance(details, Mapping):
        raise IntentContractError("task.details must be an object")
    phase = details.get("aq_phase1")
    if not isinstance(phase, Mapping):
        raise IntentContractError("task.details.aq_phase1 must be an object")
    intent = phase.get("intent")
    if not isinstance(intent, Mapping):
        raise IntentContractError("task.details.aq_phase1.intent must be an object")

    keys = set(intent)
    missing = sorted(INTENT_REQUIRED_FIELDS - keys)
    unknown = sorted(keys - INTENT_REQUIRED_FIELDS - INTENT_OPTIONAL_FIELDS)
    if missing:
        raise IntentContractError("immutable intent is missing: " + ", ".join(missing))
    if unknown:
        raise IntentContractError("immutable intent has unknown fields: " + ", ".join(unknown))

    title = task.get("title")
    _nonempty(title, "title")
    for field in INTENT_REQUIRED_FIELDS:
        _nonempty(intent.get(field), field)

    return {
        "schema": "aq_phase1.intent/v1",
        "title": title,
        "description": task.get("description"),
        "parent_task_id": task.get("parent_task_id"),
        "intent": dict(intent),
    }


def task_intent_hash(task: Mapping[str, Any]) -> str:
    return canonical_digest(INTENT_DOMAIN, task_intent_payload(task))


def source_observation_hash(
    task: Mapping[str, Any], readiness: Mapping[str, Any] | None = None
) -> str:
    """Hash mutable source/readiness evidence for audit and replay diagnostics."""
    return canonical_digest(
        OBSERVATION_DOMAIN,
        {"task": dict(task), "readiness": dict(readiness) if readiness is not None else None},
    )


def source_hashes(
    task: Mapping[str, Any], readiness: Mapping[str, Any] | None = None
) -> SourceHashes:
    return SourceHashes(task_intent_hash(task), source_observation_hash(task, readiness))


def mission_boundary_payload(
    mission: Mapping[str, Any], repo_policy: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Return the exact mission and repository policy that constrain authority."""
    required = {"objective", "measure", "horizon", "boundaries"}
    missing = sorted(field for field in required if field not in mission)
    if missing:
        raise IntentContractError("mission boundary is missing: " + ", ".join(missing))
    for field in required:
        _nonempty(mission.get(field), f"mission.{field}")
    return {
        "schema": "aq-mission-boundary/v1",
        "mission": {field: mission[field] for field in sorted(required)},
        "repo_policy": dict(repo_policy or {}),
    }


def mission_boundary_hash(
    mission: Mapping[str, Any], repo_policy: Mapping[str, Any] | None = None
) -> str:
    return canonical_digest(MISSION_DOMAIN, mission_boundary_payload(mission, repo_policy))
