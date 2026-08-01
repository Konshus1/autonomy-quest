"""Portable Phase-A surprise-resolution contract.

This module intentionally uses only the Python standard library. It validates a
``ralph_surprise_packet_v0``, re-resolves SHA-pinned artifact evidence, and
builds a deterministic held investigation intent plus a zero-write receipt.
Database evidence and apply behavior belong to the backend adapter.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


PACKET_TYPE = "ralph_surprise_packet_v0"
PACKET_SCHEMA_VERSION = "0.1.0"
PHASE_A_GATE_ENV = "RLM_SURPRISE_RESOLUTION_LOCAL_APPLY"
SUPPORTED_SURPRISE_TYPES = {
    "completion_evaluator_surprise",
    "queue_control_surprise",
    "planning_prediction_surprise",
}
SEVERITIES = {"info", "low", "medium", "high", "critical"}
SUPPORTED_ACTUATION_SEVERITIES = {"medium", "high", "critical"}


class SurpriseResolutionError(RuntimeError):
    """Raised when a fail-closed Phase-A invariant is not satisfied."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


@dataclass(frozen=True)
class EvidenceResolution:
    locator: str
    status: str
    detail: str
    observed_sha256: str | None = None
    expected_sha256: str | None = None


@dataclass(frozen=True)
class PortableActionDirective:
    kind: str
    title: str
    description: str
    items: list[dict[str, Any]]
    parent_task_id: int
    dedup_key: str


@dataclass(frozen=True)
class SurpriseResolutionIntent:
    intent_type: str
    schema_version: str
    source_packet_type: str
    source_packet_sha256: str
    task_id: int
    parent_task_id: int
    context_id: str
    severity: str
    surprise_type: str
    source_surface: str
    dedup_key: str
    canonical_gap_sha256: str
    evidence: tuple[EvidenceResolution, ...]
    directive: Any
    likely_missing_variable: Any
    likely_causal_hypotheses: tuple[Any, ...]
    proposed_model_update: Any
    authority: Mapping[str, bool]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = [asdict(item) for item in self.evidence]
        payload["directive"] = asdict(self.directive)
        return payload


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def portable_core_identity() -> dict[str, Any]:
    path = Path(__file__).resolve()
    return {
        "status": "valid",
        "mode": "portable_stdlib_core",
        "module": "ralph_portable.surprise_resolution",
        "module_sha256": sha256_file(path),
        "backend_app_import_required": False,
        "database_required": False,
    }


def _json_compatible(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, Mapping):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_compatible(item) for item in value]
    if isinstance(value, tuple):
        return [_json_compatible(item) for item in value]
    if isinstance(value, set):
        return [_json_compatible(item) for item in sorted(value, key=str)]
    return value


def _diagnostic(
    code: str,
    path: str,
    message: str,
    *,
    severity: str = "error",
    **details: Any,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "path": path,
        "message": message,
        "details": _json_compatible(details),
    }


def validate_surprise_packet_v0(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Return loud structured validation diagnostics for a surprise packet."""

    data = dict(packet or {})
    issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    required_fields = (
        "packet_type",
        "schema_version",
        "task_id",
        "source_surface",
        "surprise_type",
        "expected_outcome",
        "actual_outcome",
        "impact_on_goals",
        "evidence_refs",
        "severity",
        "likely_causal_hypotheses",
        "recommended_follow_up",
        "likely_missing_variable",
        "proposed_model_update",
        "outer_loop_learning_fields",
        "spawn_recommended",
        "spawn_gate_reason",
    )

    for field in required_fields:
        if field not in data:
            issues.append(
                _diagnostic(
                    "missing_required_field",
                    field,
                    "Required packet field is absent.",
                )
            )

    if data.get("packet_type") != PACKET_TYPE:
        issues.append(
            _diagnostic(
                "invalid_packet_type",
                "packet_type",
                "packet_type must be ralph_surprise_packet_v0.",
            )
        )
    if data.get("surprise_type") not in SUPPORTED_SURPRISE_TYPES:
        issues.append(
            _diagnostic(
                "unsupported_surprise_type",
                "surprise_type",
                "surprise_type is not supported by v0.",
                allowed=sorted(SUPPORTED_SURPRISE_TYPES),
            )
        )
    if data.get("severity") not in SEVERITIES:
        issues.append(
            _diagnostic(
                "invalid_severity",
                "severity",
                "severity must be one supported literal.",
                allowed=sorted(SEVERITIES),
            )
        )
    if not str(data.get("source_surface") or "").strip():
        issues.append(
            _diagnostic(
                "missing_source_surface",
                "source_surface",
                "source_surface must be non-empty.",
            )
        )
    if not isinstance(data.get("expected_outcome"), Mapping):
        issues.append(
            _diagnostic(
                "invalid_expected_outcome",
                "expected_outcome",
                "expected_outcome must be an object.",
            )
        )
    if not isinstance(data.get("actual_outcome"), Mapping):
        issues.append(
            _diagnostic(
                "invalid_actual_outcome",
                "actual_outcome",
                "actual_outcome must be an object.",
            )
        )
    if not isinstance(data.get("impact_on_goals"), Mapping):
        issues.append(
            _diagnostic(
                "invalid_impact_on_goals",
                "impact_on_goals",
                "impact_on_goals must be an object.",
            )
        )
    evidence_refs = data.get("evidence_refs")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        issues.append(
            _diagnostic(
                "missing_evidence_refs",
                "evidence_refs",
                "At least one evidence ref is required.",
            )
        )
    elif not all(
        isinstance(ref, Mapping) and str(ref.get("locator") or "").strip()
        for ref in evidence_refs
    ):
        issues.append(
            _diagnostic(
                "invalid_evidence_refs",
                "evidence_refs",
                "Each evidence ref needs a locator.",
            )
        )
    if not isinstance(data.get("outer_loop_learning_fields"), Mapping):
        issues.append(
            _diagnostic(
                "invalid_outer_loop_learning_fields",
                "outer_loop_learning_fields",
                "outer_loop_learning_fields must be an object.",
            )
        )
    hypotheses = data.get("likely_causal_hypotheses")
    if not isinstance(hypotheses, list) or not hypotheses:
        issues.append(
            _diagnostic(
                "missing_likely_causal_hypotheses",
                "likely_causal_hypotheses",
                "At least one causal hypothesis is required, even if it is explicitly low confidence.",
            )
        )
    elif not all(
        isinstance(item, Mapping) and str(item.get("hypothesis") or "").strip()
        for item in hypotheses
    ):
        issues.append(
            _diagnostic(
                "invalid_likely_causal_hypotheses",
                "likely_causal_hypotheses",
                "Each causal hypothesis needs a hypothesis string.",
            )
        )
    if not isinstance(data.get("recommended_follow_up"), Mapping):
        issues.append(
            _diagnostic(
                "invalid_recommended_follow_up",
                "recommended_follow_up",
                "recommended_follow_up must be an object.",
            )
        )
    if not isinstance(data.get("spawn_recommended"), bool):
        issues.append(
            _diagnostic(
                "invalid_spawn_recommended",
                "spawn_recommended",
                "spawn_recommended must be boolean.",
            )
        )
    if not str(data.get("spawn_gate_reason") or "").strip():
        issues.append(
            _diagnostic(
                "missing_spawn_gate_reason",
                "spawn_gate_reason",
                "spawn_gate_reason is required.",
            )
        )
    if data.get("spawn_recommended") is True:
        warnings.append(
            _diagnostic(
                "spawn_recommended_not_default",
                "spawn_recommended",
                "v0 records should preserve learning fields without auto-spawn authority.",
                severity="warning",
            )
        )

    status = "valid" if not issues else "invalid"
    return {
        "status": status,
        "loud_failure": bool(issues),
        "issue_count": len(issues),
        "issues": issues,
        "warning_count": len(warnings),
        "warnings": warnings,
        "diagnostic_codes": [item["code"] for item in issues + warnings],
    }


def canonical_gap_identity(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Return the stable observed-gap identity; volatile prose is excluded."""

    locators = sorted(
        str(item.get("locator") or "").strip()
        for item in packet.get("evidence_refs") or []
        if isinstance(item, Mapping) and str(item.get("locator") or "").strip()
    )
    return {
        "task_id": packet.get("task_id"),
        "parent_task_id": packet.get("parent_task_id"),
        "source_surface": packet.get("source_surface"),
        "surprise_type": packet.get("surprise_type"),
        "expected_outcome": packet.get("expected_outcome"),
        "actual_outcome": packet.get("actual_outcome"),
        "evidence_locators": locators,
    }


def surprise_resolution_dedup_key(packet: Mapping[str, Any]) -> tuple[str, str]:
    identity_sha = sha256_bytes(
        stable_json(canonical_gap_identity(packet)).encode("ascii")
    )
    return (
        f"surprise_resolution:v0:{packet.get('task_id')}:{packet.get('surprise_type')}:{identity_sha}",
        identity_sha,
    )


def _parse_positive_task_id(value: Any, *, field: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SurpriseResolutionError(
            "invalid_task_identity",
            f"{field} must be a positive integer.",
            details={field: value},
        ) from exc
    if parsed <= 0:
        raise SurpriseResolutionError(
            "invalid_task_identity",
            f"{field} must be a positive integer.",
            details={field: value},
        )
    return parsed


def resolve_artifact_evidence(
    ref: Mapping[str, Any],
    *,
    repo_root: Path,
) -> EvidenceResolution:
    locator = str(ref.get("locator") or "")
    relative = locator.removeprefix("artifact:")
    expected_sha = str(ref.get("sha256") or "").strip().lower() or None
    if not expected_sha:
        return EvidenceResolution(
            locator=locator,
            status="weak_unpinned_local_artifact",
            detail="Local artifact evidence requires an expected sha256.",
        )
    candidate = (repo_root / relative).resolve()
    try:
        candidate.relative_to(repo_root)
    except ValueError:
        return EvidenceResolution(
            locator=locator,
            status="dead_or_unresolvable_evidence",
            detail="Artifact locator escapes the repository root.",
            expected_sha256=expected_sha,
        )
    if not candidate.is_file():
        return EvidenceResolution(
            locator=locator,
            status="dead_or_unresolvable_evidence",
            detail="Artifact file does not exist.",
            expected_sha256=expected_sha,
        )
    observed_sha = sha256_file(candidate)
    status = (
        "resolved"
        if observed_sha == expected_sha
        else "dead_or_unresolvable_evidence"
    )
    detail = (
        "Artifact content hash matches."
        if status == "resolved"
        else "Artifact content hash mismatch."
    )
    return EvidenceResolution(
        locator=locator,
        status=status,
        detail=detail,
        observed_sha256=observed_sha,
        expected_sha256=expected_sha,
    )


def resolve_portable_evidence_refs(
    packet: Mapping[str, Any],
    *,
    repo_root: str | Path,
    connection: Any = None,
) -> tuple[EvidenceResolution, ...]:
    """Resolve artifact evidence without importing or requiring a backend."""

    del connection
    root = Path(repo_root).resolve()
    resolutions: list[EvidenceResolution] = []
    for ref in packet.get("evidence_refs") or []:
        if not isinstance(ref, Mapping):
            resolutions.append(
                EvidenceResolution(
                    locator="",
                    status="dead_or_unresolvable_evidence",
                    detail="Evidence reference is not an object.",
                )
            )
            continue
        locator = str(ref.get("locator") or "").strip()
        if locator.startswith("artifact:"):
            resolutions.append(resolve_artifact_evidence(ref, repo_root=root))
        else:
            resolutions.append(
                EvidenceResolution(
                    locator=locator,
                    status="backend_evidence_adapter_required",
                    detail=(
                        "Portable dry-run resolves artifact evidence only; "
                        "database evidence requires a backend adapter."
                    ),
                )
            )
    return tuple(resolutions)


def build_portable_surprise_resolution_intent(
    packet: Mapping[str, Any],
    *,
    repo_root: str | Path,
    connection: Any = None,
    evidence_resolver: Callable[..., Sequence[EvidenceResolution]] = (
        resolve_portable_evidence_refs
    ),
    directive_factory: Callable[..., Any] = PortableActionDirective,
) -> SurpriseResolutionIntent:
    """Validate current packet/evidence truth and build one bounded intent."""

    packet_data = dict(packet or {})
    diagnostics = validate_surprise_packet_v0(packet_data)
    if diagnostics["status"] != "valid":
        raise SurpriseResolutionError(
            "invalid_surprise_packet",
            "Surprise packet failed decision-time schema validation.",
            details={"validation_diagnostics": diagnostics},
        )
    if stable_json(packet_data.get("expected_outcome")) == stable_json(
        packet_data.get("actual_outcome")
    ):
        raise SurpriseResolutionError(
            "expected_equals_actual",
            "No surprise exists because expected and actual outcomes are equal.",
        )
    severity = str(packet_data.get("severity") or "").lower()
    if severity not in SUPPORTED_ACTUATION_SEVERITIES:
        raise SurpriseResolutionError(
            "severity_below_gate",
            "Phase A acts only on medium, high, or critical surprise packets.",
            details={"severity": severity},
        )
    evidence = tuple(
        evidence_resolver(
            packet_data,
            repo_root=repo_root,
            connection=connection,
        )
    )
    unresolved = [item for item in evidence if item.status != "resolved"]
    if not evidence or unresolved:
        raise SurpriseResolutionError(
            unresolved[0].status if unresolved else "dead_or_unresolvable_evidence",
            "Every evidence locator must resolve at decision time.",
            details={"evidence": [asdict(item) for item in evidence]},
        )

    task_id = _parse_positive_task_id(packet_data.get("task_id"), field="task_id")
    parent_task_id = _parse_positive_task_id(
        packet_data.get("parent_task_id") or task_id,
        field="parent_task_id",
    )
    dedup_key, identity_sha = surprise_resolution_dedup_key(packet_data)
    packet_sha = sha256_bytes(stable_json(packet_data).encode("ascii"))
    surprise_type = str(packet_data["surprise_type"])
    source_surface = str(packet_data["source_surface"])
    title = f"[surprise] Investigate {surprise_type} for task #{task_id}"[:250]
    description = (
        f"Phase-A held investigation generated from a validated {PACKET_TYPE}.\n\n"
        f"Source task: #{task_id}\n"
        f"Source surface: {source_surface}\n"
        f"Severity: {severity}\n"
        f"Expected: {stable_json(packet_data['expected_outcome'])}\n"
        f"Actual: {stable_json(packet_data['actual_outcome'])}\n\n"
        "This task is awaiting human review and is not worker-launchable. "
        "Hypotheses and proposed model updates remain unaccepted proposals."
    )
    directive = directive_factory(
        kind="investigate_surprise",
        title=title,
        description=description,
        items=[
            {
                "expected_outcome": packet_data["expected_outcome"],
                "actual_outcome": packet_data["actual_outcome"],
                "evidence_locators": [item.locator for item in evidence],
            }
        ],
        parent_task_id=parent_task_id,
        dedup_key=dedup_key,
    )
    return SurpriseResolutionIntent(
        intent_type="investigate_surprise",
        schema_version=PACKET_SCHEMA_VERSION,
        source_packet_type=PACKET_TYPE,
        source_packet_sha256=packet_sha,
        task_id=task_id,
        parent_task_id=parent_task_id,
        context_id=str(
            packet_data.get("context_id") or f"task:{task_id}:surprise_resolution"
        ),
        severity=severity,
        surprise_type=surprise_type,
        source_surface=source_surface,
        dedup_key=dedup_key,
        canonical_gap_sha256=identity_sha,
        evidence=evidence,
        directive=directive,
        likely_missing_variable=packet_data.get("likely_missing_variable"),
        likely_causal_hypotheses=tuple(
            packet_data.get("likely_causal_hypotheses") or []
        ),
        proposed_model_update=packet_data.get("proposed_model_update"),
        authority={
            "may_dispatch_workers": False,
            "may_call_providers": False,
            "may_mutate_scheduler": False,
            "may_mutate_production": False,
            "may_accept_model_update": False,
            "requires_manager_review": True,
        },
    )


def dry_run_receipt(intent: SurpriseResolutionIntent) -> dict[str, Any]:
    return {
        "result": "dry_run_eligible",
        "apply_attempted": False,
        "write_count": 0,
        "intent": intent.to_dict(),
    }


def failure_receipt(error: SurpriseResolutionError) -> dict[str, Any]:
    return {
        "result": error.code,
        "status": "held_or_failed",
        "apply_attempted": bool(error.details.get("apply_attempted", False)),
        "write_count": int(error.details.get("write_count", 0)),
        "message": str(error),
        "details": error.details,
    }


def write_immutable_receipt(
    path: str | Path,
    receipt: Mapping[str, Any],
) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(dict(receipt), indent=2, sort_keys=True) + "\n"
    with destination.open("x", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    return destination
