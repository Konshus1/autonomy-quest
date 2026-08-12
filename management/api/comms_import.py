"""Flag-gated importer: a stored work.request -> the EXISTING local planning queue (#4834 comms
Phase 3, design §8.4 / §10 Phase 3). DEFAULT-OFF.

This is the SECOND (and last) parent->replica ingestion point, and it treats the stored work.request
as fully untrusted input all over again (carry the Phase-2 lesson). It:

  1. runs ONLY when ``AQ_COMMS_WORK_IMPORT`` is explicitly enabled (default off => the live loop and a
     default checkout never import a single work.request);
  2. maps ONLY the deny-by-default field allowlist (goal + priority hint [+ constraints / cancel_of])
     into a planning-queue proposal — dropping every actuation / gate / replication / credential /
     measured-success field (``select_importable_fields``);
  3. builds the proposal with NO expense/blast facts (they are not importable), then runs the REAL
     ``assess_plan_gate`` — which, on absent facts, returns the maximally-restrictive decision
     (requires-human). So an imported item is ALWAYS a fully-gated proposal awaiting local approval;
     the work.request can never pre-satisfy, skip, or weaken the gate;
  4. enqueues via the injected local queue's ``create_work`` (the same surface the mission loop uses),
     with ``requires_human`` from the gate — it NEVER actuates, replicates, grants a capability, runs a
     shell, mutates AQ_REPLICATION_*, or records a measured success;
  5. records a parent-owned import record keyed on the envelope id so re-import is idempotent and a
     crash between store and import leaves NO partial actuation (storage alone is inert).

It imports NOTHING host-only (no fleet_registry, no docker step, no relay) — it runs guest-side on the
replica. It is NOT called by ``runner/loop.py`` (the loop stays byte-identical); it is a separate,
explicitly-invoked step.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any, Callable, Protocol

from management.api.comms_workrequest import (
    WorkRequestError,
    select_importable_fields,
    work_import_enabled,
)

# The kind stamped on the local proposal so the operator can see it originated from an (untrusted)
# inbound work.request and is NOT native mission work.
IMPORTED_WORK_KIND = "imported_work_request"
IMPORTED_CANCEL_KIND = "imported_work_cancel_request"


class PlanningQueue(Protocol):
    """The minimal slice of the local planning queue the importer uses — the SAME ``create_work``
    surface ``runner.db`` exposes to the mission loop. Injected so the importer never has to import the
    mission DB directly (and tests pass a recording fake)."""

    def create_work(self, kind: str, summary: str, rationale: str, *, requires_human: bool,
                    plan: dict[str, Any] | None = ..., expected_expense_usd: Any = ...,
                    blast_radius_level: int | None = ..., gate_policy_version: str | None = ...,
                    gate_reason: str | None = ...) -> int: ...


def _proposal_plan(importable: dict[str, Any]) -> dict[str, Any]:
    """Build the plan dict fed to the REAL gate. Critically it carries NO caller-supplied expense/blast
    facts (they are not importable) — so ``assess_plan_gate`` computes the max-restriction decision.

    A single opaque step with NO ``blast_radius`` facts makes ``assess_blast_radius`` return level 3
    (missing/unknown facts => maximum), and ``expected_expense_usd`` defaults to 0 only for the money
    axis; the blast axis alone already forces requires-human at the default gate level 3."""
    goal = importable["goal"]
    return {
        "expected_expense_usd": 0,   # the request cannot claim a budget; money axis starts at zero
        "steps": [
            {
                "step_id": "imported-1",
                "action": f"(untrusted inbound proposal) {goal}",
                # No blast_radius facts on purpose: absent facts => level 3 (design: honest absence =>
                # most-restrictive). The request cannot assert a low blast radius to skip approval.
            }
        ],
    }


def map_work_request_to_proposal(
    envelope: dict[str, Any], *,
    per_plan_approval_usd: Decimal = Decimal("3"),
    blast_gate_level: int = 3,
) -> dict[str, Any]:
    """Pure mapping (no side effects): stored work.request envelope -> a GATED proposal descriptor.

    Applies the field allowlist, then runs the real consequence gate. Returns a descriptor the caller
    hands to ``create_work``. Raises ``WorkRequestError`` if the stored payload has no importable goal.
    """
    from runner.consequence_gate import assess_plan_gate  # guest-side gate; lazy for a lean import

    payload = envelope.get("payload") or {}
    importable = select_importable_fields(payload)
    is_cancel = "cancel_of" in importable
    plan = _proposal_plan(importable)
    # Finding 3 (defense in depth): CLAMP the blast gate level to <= 3 INSIDE the importer. The gate
    # only forces requires_human when max_blast >= blast_gate_level; a caller passing a higher level
    # (e.g. 4) would silently flip imported items to requires_human=False. The importer must never let
    # its own gate be weakened, so it clamps here regardless of what a caller passes. An imported item
    # is therefore ALWAYS requires_human=True (absent facts => blast 3 >= clamped gate 3).
    effective_gate_level = min(int(blast_gate_level), 3)
    gate = assess_plan_gate(plan, per_plan_approval_usd=per_plan_approval_usd,
                            blast_gate_level=effective_gate_level)
    assert gate.requires_human, (
        "importer invariant violated: an imported work.request must always require human approval "
        f"(gate returned requires_human={gate.requires_human}, blast={gate.blast_radius_level})")

    priority = importable.get("priority", "normal")
    goal = importable["goal"]
    constraints = importable.get("constraints")
    rationale_bits = [
        f"Imported from inbound work.request {envelope.get('id')!r} "
        f"(origin {envelope.get('origin_instance_id')!r}, priority hint {priority}).",
        "UNTRUSTED PROPOSAL: enters the local planning queue subject to every normal gate; it grants "
        "no capability and actuates nothing on its own.",
    ]
    if constraints:
        rationale_bits.append(f"Stated constraints (advisory only): {constraints}")
    if is_cancel:
        rationale_bits.append(
            f"This is a CANCELLATION REQUEST for correlation {importable['cancel_of']!r} — honored as "
            "a gated proposal, never a process signal / kill.")

    return {
        "kind": IMPORTED_CANCEL_KIND if is_cancel else IMPORTED_WORK_KIND,
        "summary": (f"[cancel-request] {goal}" if is_cancel else goal)[:2000],
        "rationale": " ".join(rationale_bits),
        # requires_human comes straight from the gate — always True here because the request carries no
        # blast facts. It is NEVER forced False and the request can never set it.
        "requires_human": bool(gate.requires_human),
        "plan": plan,
        "expected_expense_usd": gate.expected_expense_usd,
        "blast_radius_level": gate.blast_radius_level,
        "gate_policy_version": gate.policy_version,
        "gate_reason": gate.reason,
        # Echoed for the audit/import record; NOT a queue column.
        "_source_envelope_id": envelope.get("id"),
        "_importable_fields": importable,
    }


def import_pending_work_requests(
    envelopes: list[dict[str, Any]],
    imports: dict[str, dict[str, Any]],
    queue: PlanningQueue,
    *,
    env: dict[str, str] | None = None,
    parent_instance_id: str | None = None,
    record_import: Callable[[str, dict[str, Any]], Any] | None = None,
    per_plan_approval_usd: Decimal = Decimal("3"),
    blast_gate_level: int = 3,
) -> dict[str, Any]:
    """Import every not-yet-imported inbound ``work.request`` into ``queue`` — ONLY when enabled.

    ``envelopes`` is the replica's durable inbox (its stored envelopes). ``imports`` is the parent-owned
    import-state map (envelope_id -> record); an envelope already present there is SKIPPED (idempotent
    re-import). ``record_import(envelope_id, record)`` persists the import record.

    Finding 1 (defense in depth — guard EVERY ingestion point): the importer imports ONLY work.requests
    that arrived via the PARENT->REPLICA delivery path — ``delivery == "delivered"`` (only the inbox
    endpoint stamps that; the publish endpoint stamps accepted/relayed) AND ``origin_instance_id`` equal
    to this replica's configured parent. This neutralizes a SELF-AUTHORED or ambient work.request stored
    via ``POST /api/agent-comms`` (which bypasses the inbox's guard #1 payload quarantine): even with the
    importer enabled, such a request is SKIPPED with a clear reason, never imported. It is a SECOND
    independent guard on top of the field allowlist, not a replacement. ``parent_instance_id`` defaults
    to ``AQ_COMMS_PARENT_INSTANCE_ID`` from ``env``; with no configured parent, NOTHING is importable
    (fail-closed).

    Fail-safe / land-inert: with the flag OFF this returns ``{"enabled": False, "imported": 0, ...}``
    and touches nothing — no ``create_work`` call, no record. That is the default-off, byte-identical
    guarantee.
    """
    if not work_import_enabled(env):
        return {"enabled": False, "imported": 0, "skipped": 0, "failed": 0, "results": []}

    source = env if env is not None else os.environ
    parent = (parent_instance_id
              or (source.get("AQ_COMMS_PARENT_INSTANCE_ID") or "").strip() or None)

    imported = skipped = failed = 0
    results: list[dict[str, Any]] = []
    for env_row in envelopes:
        if env_row.get("kind") != "work.request":
            continue
        eid = env_row.get("id")
        if not eid:
            continue
        if eid in imports:
            skipped += 1
            continue
        # SECOND INDEPENDENT GUARD (Finding 1): only import a request delivered by the parent path.
        if env_row.get("delivery") != "delivered":
            skipped += 1
            rec = {"envelope_id": eid, "state": "skipped",
                   "reason": f"not delivered via the parent inbox path (delivery="
                             f"{env_row.get('delivery')!r}); self-authored/ambient work.requests are "
                             "never imported"}
            if record_import is not None:
                record_import(eid, rec)
            results.append(rec)
            continue
        origin = env_row.get("origin_instance_id")
        if not parent or origin != parent:
            skipped += 1
            rec = {"envelope_id": eid, "state": "skipped",
                   "reason": f"origin {origin!r} is not this replica's configured parent {parent!r}; "
                             "only the true parent lineage's delivered work.requests are imported"}
            if record_import is not None:
                record_import(eid, rec)
            results.append(rec)
            continue
        try:
            proposal = map_work_request_to_proposal(
                env_row, per_plan_approval_usd=per_plan_approval_usd, blast_gate_level=blast_gate_level)
        except WorkRequestError as exc:
            failed += 1
            rec = {"envelope_id": eid, "state": "rejected", "reason": str(exc)}
            if record_import is not None:
                record_import(eid, rec)
            results.append(rec)
            continue

        work_id = queue.create_work(
            proposal["kind"], proposal["summary"], proposal["rationale"],
            requires_human=proposal["requires_human"], plan=proposal["plan"],
            expected_expense_usd=proposal["expected_expense_usd"],
            blast_radius_level=proposal["blast_radius_level"],
            gate_policy_version=proposal["gate_policy_version"],
            gate_reason=proposal["gate_reason"],
        )
        rec = {
            "envelope_id": eid,
            "state": "imported",
            "work_id": work_id,
            "requires_human": proposal["requires_human"],
            "gate_reason": proposal["gate_reason"],
            "blast_radius_level": proposal["blast_radius_level"],
            "importable_fields": proposal["_importable_fields"],
        }
        if record_import is not None:
            record_import(eid, rec)
        imported += 1
        results.append(rec)

    return {"enabled": True, "imported": imported, "skipped": skipped, "failed": failed,
            "results": results}
