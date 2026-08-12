"""Inbound work-request projection for the operator UI (#4834 comms Phase 3, design §9.1 / §10 Phase 3).

Projects the durable inbox's inbound ``work.request`` / ``work.response`` / ``receipt`` envelopes plus
their PARENT-OWNED import state into rows the operator UI badges as UNTRUSTED PROPOSALS. Read-only,
grants no capability.

The load-bearing display rule: an inbound work.request is shown as a PROPOSAL, never as authorized
work. Its ``import_state`` defaults to ``not_imported`` (the importer is default-off), so a stored
request that has never been through the flag-gated importer + local gates reads as inert — never as
queued, approved, or acted on. When imported, the row shows ``requires_human`` (always true here) so it
is visibly still awaiting local approval, not done.
"""

from __future__ import annotations

from typing import Any, Iterable

from management.api.comms_workrequest import INBOUND_WORK_KINDS

UNTRUSTED = "untrusted_claim"


def inbox_from_envelopes(
    envelopes: Iterable[dict[str, Any]],
    imports: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Project inbound work envelopes + the import table into UI rows.

    Every row is explicitly an untrusted PROPOSAL (``trust`` + ``proposal=True``). Its ``import_state``
    is the parent-owned state (default ``not_imported``); ``requires_human`` surfaces that even an
    imported request is still gated. Ordering is by envelope id (monotonic ULID) so newest sorts last.
    """
    imports = imports or {}
    rows: list[dict[str, Any]] = []
    for env in envelopes:
        kind = env.get("kind")
        if kind not in INBOUND_WORK_KINDS:
            continue
        # Only inbound directed traffic to THIS instance belongs in the inbox: a work.request the
        # replica RECEIVED (delivered/relayed), not one it authored outbound.
        target = env.get("target") or {}
        eid = env.get("id")
        payload = env.get("payload") or {}
        imp = imports.get(eid) or {}
        rows.append({
            "id": eid,
            "kind": kind,
            "origin_instance_id": env.get("origin_instance_id"),
            "principal_id": env.get("principal_id"),
            "channel": env.get("channel"),
            "target_instance_id": target.get("instance_id"),
            "correlation_id": env.get("correlation_id"),
            "in_reply_to": env.get("in_reply_to"),
            "created_at": env.get("created_at"),
            "expires_at": env.get("expires_at"),
            "delivery": env.get("delivery"),
            "trust": env.get("trust"),
            # ALWAYS an untrusted proposal in the UI: storage granted no capability; the UI must never
            # present it as authorized work.
            "proposal": True,
            "goal": payload.get("goal"),
            "priority": payload.get("priority"),
            "constraints": payload.get("constraints"),
            "cancel_of": payload.get("cancel_of"),
            # Reply/ack fields (for work.response / receipt rows).
            "state": payload.get("state"),
            "disposition": payload.get("disposition"),
            # Parent-owned import state — defaults to not_imported (the importer is default-off).
            "import_state": imp.get("state", "not_imported"),
            "work_id": imp.get("work_id"),
            "requires_human": imp.get("requires_human"),
            "gate_reason": imp.get("gate_reason"),
        })
    rows.sort(key=lambda r: (r.get("id") or ""))
    return rows
