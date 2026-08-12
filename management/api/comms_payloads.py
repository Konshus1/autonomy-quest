"""Versioned, size-capped payload schemas for replica-authored comms (#4834 comms Phase 2, design §4.1/§8.5).

Phase 2 lets a REPLICA author three bounded, untrusted-claim kinds to its own local outbox:
``status.report``, ``experiment.progress``, ``experiment.result``. This module is the guest-safe
schema layer for their PAYLOADS — it validates shape, versions each payload, clamps sizes, and (for
``experiment.result``) enforces the load-bearing rule that artifact references are IMMUTABLE DIGESTS
plus metadata, never host paths or auto-fetchable URLs (design §4.1 / §8.3 / §8.5 SSRF boundary).

It grants NO capability and imports NOTHING host-only: it is reachable from the guest container and
from the loop. Identity/trust are NOT set here — the API derives them from the credential and stamps
``trust=untrusted_claim`` (a replica can never mint ``host_observed``). A validated payload is still
just a CLAIM: ``outcome_claimed`` and ``metrics`` are what the replica asserts, never measured truth.
The parent independently verifies (see the parent-owned verification state) before anything is adopted.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from management.api.comms_envelope import (
    EnvelopeError,
    MAX_PAYLOAD_BYTES,
    MAX_TEXT_BYTES,
    _payload_size,
)

# Payload schema versions (bumped independently of the envelope version).
STATUS_REPORT_VERSION = 1
EXPERIMENT_PROGRESS_VERSION = 1
EXPERIMENT_RESULT_VERSION = 1

# The three replica-authored kinds Phase 2 emits. (The full envelope enum is broader; these are the
# ones a replica loop may EMIT and the host may RELAY as untrusted claims.)
REPLICA_EMIT_KINDS = frozenset({"status.report", "experiment.progress", "experiment.result"})

# An ``outcome_claimed`` is a claim, not a verdict: it never satisfies a gate.
OUTCOME_CLAIMS = frozenset({"success", "failure", "partial", "inconclusive", "running", "unknown"})

# Artifact references are digests + metadata ONLY (design §4.1). The exact allowed key set — anything
# else (a ``path``, ``url``, ``uri``, ``location``, ``fetch``) is rejected so a replica can never smuggle
# a host path or an auto-fetchable URL through a "reference" (that would be an SSRF / path-traversal lever).
ALLOWED_ARTIFACT_KEYS = frozenset({"digest", "media_type", "size_bytes", "name"})
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

# Bounds (design §8.5). Small, strict maxima so one message cannot exhaust storage or the UI.
MAX_ARTIFACT_REFS = 32
MAX_METRICS = 64
MAX_METRIC_NAME_LEN = 128
MAX_NAME_LEN = 256
MAX_MEDIA_TYPE_LEN = 128
MAX_ARTIFACT_SIZE_BYTES = 1 << 40  # 1 TiB declared-size ceiling (a sanity clamp, not a fetch budget)


class PayloadError(EnvelopeError):
    """A replica-authored payload failed its per-kind schema (bad shape, oversize, or a forbidden
    artifact reference such as a host path / URL). Subclasses ``EnvelopeError`` so the API maps it to
    the same 400 as any other malformed envelope."""


# --- digests -----------------------------------------------------------------
def compute_digest(content: bytes) -> str:
    """The canonical ``sha256:<hex>`` digest of some bytes — the one the parent recomputes from the
    replica's REAL artifact to check a claimed reference (the digest-mismatch verification)."""
    return "sha256:" + hashlib.sha256(content).hexdigest()


def artifact_digest_matches(ref: dict[str, Any], content: bytes) -> bool:
    """True iff ``content`` hashes to the digest the reference CLAIMS. The parent uses this to verify
    an artifact reference against ground truth; a mismatch is flagged/rejected, never adopted."""
    claimed = (ref or {}).get("digest")
    if not isinstance(claimed, str) or not DIGEST_RE.match(claimed):
        return False
    return compute_digest(content) == claimed


def _validate_artifact_ref(ref: Any, *, index: int) -> dict[str, Any]:
    if not isinstance(ref, dict):
        raise PayloadError(f"artifact_refs[{index}] must be an object")
    extra = set(ref) - ALLOWED_ARTIFACT_KEYS
    if extra:
        # The most important rejection in the file: no path/url/uri/location/fetch keys, ever.
        raise PayloadError(
            f"artifact_refs[{index}] has forbidden key(s) {sorted(extra)}; artifact references are "
            f"digests + metadata only (no host paths, no auto-fetch URLs — design §4.1/§8.5)")
    digest = ref.get("digest")
    if not isinstance(digest, str) or not DIGEST_RE.match(digest):
        raise PayloadError(
            f"artifact_refs[{index}].digest must be 'sha256:<64-hex>' (immutable digest, not a path/URL)")
    out: dict[str, Any] = {"digest": digest}
    media = ref.get("media_type")
    if media is not None:
        if not isinstance(media, str) or len(media) > MAX_MEDIA_TYPE_LEN:
            raise PayloadError(f"artifact_refs[{index}].media_type must be a short string")
        # A media_type is a label, not a locator: reject anything that looks like a URL/scheme.
        if "://" in media or media.startswith(("http", "file", "/")):
            raise PayloadError(f"artifact_refs[{index}].media_type must not look like a URL/path")
        out["media_type"] = media
    name = ref.get("name")
    if name is not None:
        if not isinstance(name, str) or len(name) > MAX_NAME_LEN:
            raise PayloadError(f"artifact_refs[{index}].name must be a short string")
        # A display name, not a path: no separators that could be read as a filesystem location.
        if "/" in name or "\\" in name or "://" in name:
            raise PayloadError(f"artifact_refs[{index}].name must be a bare label, not a path/URL")
        out["name"] = name
    size = ref.get("size_bytes")
    if size is not None:
        if not isinstance(size, int) or isinstance(size, bool) or size < 0 or size > MAX_ARTIFACT_SIZE_BYTES:
            raise PayloadError(f"artifact_refs[{index}].size_bytes must be a non-negative bounded int")
        out["size_bytes"] = size
    return out


def _validate_text(value: Any, *, field: str, required: bool) -> str | None:
    if value is None:
        if required:
            raise PayloadError(f"{field} is required")
        return None
    if not isinstance(value, str):
        raise PayloadError(f"{field} must be a string")
    if len(value.encode("utf-8")) > MAX_TEXT_BYTES:
        raise PayloadError(f"{field} exceeds {MAX_TEXT_BYTES} bytes")
    return value


def _validate_metrics(value: Any) -> dict[str, float]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise PayloadError("metrics must be an object of name -> number")
    if len(value) > MAX_METRICS:
        raise PayloadError(f"metrics has more than {MAX_METRICS} entries")
    out: dict[str, float] = {}
    for k, v in value.items():
        if not isinstance(k, str) or len(k) > MAX_METRIC_NAME_LEN:
            raise PayloadError("each metric name must be a short string")
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise PayloadError(f"metric {k!r} must be a number")
        out[k] = v
    return out


def _finalize(payload: dict[str, Any]) -> dict[str, Any]:
    """Last guard: the assembled payload must still fit the global envelope payload cap."""
    size = _payload_size(payload)
    if size > MAX_PAYLOAD_BYTES:
        raise PayloadError(f"payload {size} bytes exceeds limit {MAX_PAYLOAD_BYTES}")
    return payload


# --- per-kind builders/validators --------------------------------------------
def build_status_report(*, text: str, state: str | None = None,
                        extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": STATUS_REPORT_VERSION,
        "text": _validate_text(text, field="text", required=True),
    }
    if state is not None:
        if not isinstance(state, str) or len(state) > MAX_METRIC_NAME_LEN:
            raise PayloadError("state must be a short string")
        payload["state"] = state
    if extra:
        _merge_scalar_extra(payload, extra)
    return _finalize(payload)


def build_experiment_progress(*, text: str, progress: float | None = None,
                              correlation_id: str | None = None,
                              extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": EXPERIMENT_PROGRESS_VERSION,
        "text": _validate_text(text, field="text", required=True),
    }
    if progress is not None:
        if isinstance(progress, bool) or not isinstance(progress, (int, float)) or not (0.0 <= progress <= 1.0):
            raise PayloadError("progress must be a number in [0,1]")
        payload["progress"] = float(progress)
    if correlation_id is not None:
        payload["correlation_id"] = _validate_text(correlation_id, field="correlation_id", required=False)
    if extra:
        _merge_scalar_extra(payload, extra)
    return _finalize(payload)


def build_experiment_result(*, summary: str, outcome_claimed: str = "unknown",
                            artifact_refs: list[dict[str, Any]] | None = None,
                            metrics: dict[str, Any] | None = None,
                            extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """A bounded ``experiment.result`` payload: a SUMMARY + a CLAIMED outcome + artifact DIGEST refs.

    Everything here is an untrusted claim. ``outcome_claimed`` never satisfies a gate; ``artifact_refs``
    are immutable ``sha256:`` digests + metadata the parent can independently re-verify — NEVER host
    paths or URLs the host would fetch.
    """
    if outcome_claimed not in OUTCOME_CLAIMS:
        raise PayloadError(f"outcome_claimed must be one of {sorted(OUTCOME_CLAIMS)}")
    refs_in = artifact_refs or []
    if not isinstance(refs_in, list):
        raise PayloadError("artifact_refs must be a list")
    if len(refs_in) > MAX_ARTIFACT_REFS:
        raise PayloadError(f"artifact_refs has more than {MAX_ARTIFACT_REFS} entries")
    refs = [_validate_artifact_ref(r, index=i) for i, r in enumerate(refs_in)]
    payload: dict[str, Any] = {
        "schema_version": EXPERIMENT_RESULT_VERSION,
        "summary": _validate_text(summary, field="summary", required=True),
        # Explicitly labelled: this is what the replica CLAIMS, awaiting independent verification.
        "outcome_claimed": outcome_claimed,
        "verification_required": True,
        "artifact_refs": refs,
        "metrics": _validate_metrics(metrics),
    }
    if extra:
        _merge_scalar_extra(payload, extra)
    return _finalize(payload)


def _merge_scalar_extra(payload: dict[str, Any], extra: dict[str, Any]) -> None:
    """Allow a small set of caller-supplied SCALAR annotations (e.g. cycle_count) without letting a
    caller inject a nested structure that dodges the size/shape bounds. Reserved keys are protected."""
    reserved = {"schema_version", "artifact_refs", "verification_required"}
    for k, v in extra.items():
        if not isinstance(k, str):
            raise PayloadError("extra keys must be strings")
        if k in reserved:
            raise PayloadError(f"extra may not override reserved key {k!r}")
        if isinstance(v, (dict, list)):
            raise PayloadError(f"extra[{k!r}] must be a scalar (no nested structures)")
        if isinstance(v, str) and len(v.encode("utf-8")) > MAX_TEXT_BYTES:
            raise PayloadError(f"extra[{k!r}] string too large")
        payload[k] = v


def validate_replica_payload(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate an already-shaped replica payload for one of the three emit kinds and return the
    normalized copy. Used by the API to HARDEN what a replica publishes (defense in depth on top of
    the envelope's generic size cap). Non-emit kinds pass through unchanged (their producers own them).
    """
    if kind == "status.report":
        return build_status_report(
            text=payload.get("text", ""),
            state=payload.get("state"),
            extra={k: v for k, v in payload.items()
                   if k not in {"schema_version", "text", "state"}} or None,
        )
    if kind == "experiment.progress":
        return build_experiment_progress(
            text=payload.get("text", ""),
            progress=payload.get("progress"),
            correlation_id=payload.get("correlation_id"),
            extra={k: v for k, v in payload.items()
                   if k not in {"schema_version", "text", "progress", "correlation_id"}} or None,
        )
    if kind == "experiment.result":
        return build_experiment_result(
            summary=payload.get("summary", ""),
            outcome_claimed=payload.get("outcome_claimed", "unknown"),
            artifact_refs=payload.get("artifact_refs"),
            metrics=payload.get("metrics"),
            extra={k: v for k, v in payload.items()
                   if k not in {"schema_version", "summary", "outcome_claimed",
                                "artifact_refs", "metrics", "verification_required"}} or None,
        )
    return payload
