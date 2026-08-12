"""Replica-authored results projection for the operator UI (#4834 comms Phase 2, design §9.4/§10 Phase 2).

Projects the parent journal's replica-authored ``status.report`` / ``experiment.progress`` /
``experiment.result`` envelopes — all ``trust=untrusted_claim`` — into result rows the operator UI
shows with a TRUST BADGE and a PARENT-OWNED verification state (unverified/verified/rejected).

The load-bearing property lives here as a projection rule: a replica claim is displayed as a CLAIM.
Its verification state comes ONLY from the separate parent-owned verification table (never from the
envelope the replica authored), and the default is ``unverified`` — a result that has never been
independently checked reads as unverified, never as adopted or successful. This module is read-only
and grants no capability.
"""

from __future__ import annotations

from typing import Any, Iterable

RESULT_KINDS = frozenset({"status.report", "experiment.progress", "experiment.result"})
UNTRUSTED = "untrusted_claim"


def results_from_envelopes(
    envelopes: Iterable[dict[str, Any]],
    verifications: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Project replica-authored result envelopes + the parent verification table into UI rows.

    Every row is explicitly a replica CLAIM (``trust`` + ``claim=True``); its ``verification`` is the
    parent-owned state, defaulting to ``unverified``. Ordering is by envelope id (monotonic ULID) so
    the newest claim sorts last, matching the journal.
    """
    verifications = verifications or {}
    rows: list[dict[str, Any]] = []
    for env in envelopes:
        kind = env.get("kind")
        if kind not in RESULT_KINDS:
            continue
        # Only replica CLAIMS are results; a host_observed envelope is fleet ground truth, not a claim.
        if env.get("trust") != UNTRUSTED:
            continue
        eid = env.get("id")
        payload = env.get("payload") or {}
        target = env.get("target") or {}
        ver = verifications.get(eid) or {}
        rows.append({
            "id": eid,
            "kind": kind,
            "origin_instance_id": env.get("origin_instance_id"),
            "principal_id": env.get("principal_id"),
            "channel": env.get("channel"),
            "target_instance_id": target.get("instance_id"),
            "correlation_id": env.get("correlation_id"),
            "created_at": env.get("created_at"),
            "relayed_at": env.get("relay", {}).get("relayed_at") if isinstance(env.get("relay"), dict) else None,
            "trust": env.get("trust"),
            "claim": True,  # ALWAYS a claim: never treated as measured/adopted by this projection.
            "summary": payload.get("summary") or payload.get("text"),
            "outcome_claimed": payload.get("outcome_claimed"),
            "artifact_refs": payload.get("artifact_refs") or [],
            "metrics": payload.get("metrics") or {},
            # Parent-owned verification — defaults to unverified when the parent has not checked it.
            "verification": ver.get("state", "unverified"),
            "verified_by": ver.get("verifier"),
            "verification_reason": ver.get("reason"),
            "verified_at": ver.get("verified_at"),
        })
    rows.sort(key=lambda r: (r.get("id") or ""))
    return rows
