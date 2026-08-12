"""Typed parent/operator -> replica WORK REQUEST schemas + the import field allowlist
(#4834 comms Phase 3, design §4.1 / §8.3 / §8.4).

This is the guest-safe schema layer for the FIRST parent->replica command direction. It imports
NOTHING host-only and grants NO capability. It is the guard that must hold at BOTH Phase-3 ingestion
points (carry the Phase-2 lesson: a trust boundary holds at EVERY door, not just the front one):

  1. the replica INBOX endpoint (``validate_inbound_payload``) — shape/size/kind on arrival, and
  2. the flag-gated IMPORTER (``select_importable_fields``) — a deny-by-default ALLOWLIST that maps
     ONLY a bounded goal + priority hint (+ optional constraints / cancel_of) into the local planning
     queue, DROPPING every actuation / gate / replication / credential / measured-success field.

Design §8.4 "safe command intake": there is deliberately NO generic ``command`` / ``shell`` /
executable payload. A ``work.request`` carries a *goal*, not authority. Even an authenticated parent
request is untrusted input: it becomes an ordinary proposal that the local scheduler validates against
mission boundaries, budget, blast-radius, approval, and capability policy. It can never pre-satisfy,
skip, or weaken any of those gates.

THE LOAD-BEARING RULE lives here as the allowlist: whatever an attacker stuffs into a work.request —
``run_shell``, ``replicate``, ``AQ_REPLICATION_MAX_REPLICAS``, ``grant_capability``, ``auto_approve``,
``measured_success``, ``docker`` — is NOT in ``IMPORTABLE_FIELDS`` and is dropped on the way into the
queue. The importer never reads it. Storage of it (in the inert inbox) grants nothing; it is inert data.
"""

from __future__ import annotations

from typing import Any

from management.api.comms_envelope import (
    EnvelopeError,
    MAX_PAYLOAD_BYTES,
    MAX_TEXT_BYTES,
    _payload_size,
)

# Payload schema versions (bumped independently of the envelope version).
WORK_REQUEST_VERSION = 1
WORK_RESPONSE_VERSION = 1
RECEIPT_VERSION = 1

# The three parent<->replica command-direction kinds this phase adds handling for. ``work.request``
# and its ``cancel`` variant flow parent->replica (the INBOX). ``work.response`` / ``receipt`` flow
# replica->parent as reply/ack (emitted to the replica outbox, carried back by the Phase-2 relay).
WORKREQUEST_KINDS = frozenset({"work.request", "work.response", "receipt"})
INBOUND_WORK_KINDS = frozenset({"work.request", "work.response", "receipt"})

# Reply/ack kinds a REPLICA may emit back toward the parent (design §4: work.response / receipt).
WORK_REPLY_KINDS = frozenset({"work.response", "receipt"})

# Bounded priority hints — a suggestion only; it never sets a gate level or a budget.
PRIORITY_HINTS = frozenset({"low", "normal", "high"})

# Receipt / response dispositions (transport-level acks + the replica's own claimed handling state).
# NONE of these is evidence the requested work was DONE or SUCCEEDED (design §4.1 / §8.3).
RECEIPT_DISPOSITIONS = frozenset({"received", "stored", "imported", "rejected", "expired", "duplicate"})
RESPONSE_STATES = frozenset({"accepted_as_proposal", "queued_for_approval", "declined", "cancel_requested"})

# ---------------------------------------------------------------------------
# THE IMPORT FIELD ALLOWLIST (deny-by-default). The importer maps ONLY these keys of a stored
# work.request into a local planning-queue proposal. EVERYTHING ELSE IS DROPPED.
#
#   goal        -- a bounded free-text description of the desired work (required).
#   priority    -- an optional low/normal/high HINT (never a gate level / budget).
#   constraints -- optional bounded free-text guidance, informational only.
#   cancel_of   -- optional correlation_id this request asks to CANCEL (cancel-as-request, never a
#                  process signal / kill — the importer enqueues a gated cancellation proposal).
#
# Deliberately ABSENT (so a request can never actuate): expected_expense_usd, blast_radius,
# steps, approval / auto_approve / gate-bypass flags, capability / credential grants, AQ_REPLICATION_*,
# docker / socket / shell / command, replicate / merge / promote / adopt directives, measured_success /
# mission-truth fields. Absence is the point: the gate then sees NO expense/blast facts and defaults to
# maximum restriction (requires-human), so an imported request is always a fully-gated proposal.
IMPORTABLE_FIELDS = frozenset({"goal", "priority", "constraints", "cancel_of"})

MAX_GOAL_BYTES = 8 * 1024
MAX_CONSTRAINTS_BYTES = 8 * 1024


class WorkRequestError(EnvelopeError):
    """A work.request/response/receipt payload failed its per-kind schema. Subclasses EnvelopeError so
    the API maps it to the same 400 as any other malformed envelope."""


def _bounded_text(value: Any, *, field: str, required: bool, limit: int) -> str | None:
    if value is None:
        if required:
            raise WorkRequestError(f"{field} is required")
        return None
    if not isinstance(value, str):
        raise WorkRequestError(f"{field} must be a string")
    if not value.strip() and required:
        raise WorkRequestError(f"{field} must be non-empty")
    if len(value.encode("utf-8")) > limit:
        raise WorkRequestError(f"{field} exceeds {limit} bytes")
    return value


def _bounded_id(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise WorkRequestError(f"{field} must be a string")
    if len(value.encode("utf-8")) > MAX_TEXT_BYTES:
        raise WorkRequestError(f"{field} exceeds {MAX_TEXT_BYTES} bytes")
    return value


def _priority(value: Any) -> str | None:
    if value is None:
        return None
    if value not in PRIORITY_HINTS:
        raise WorkRequestError(f"priority must be one of {sorted(PRIORITY_HINTS)} (a hint, not a gate)")
    return value


def _finalize(payload: dict[str, Any]) -> dict[str, Any]:
    size = _payload_size(payload)
    if size > MAX_PAYLOAD_BYTES:
        raise WorkRequestError(f"payload {size} bytes exceeds limit {MAX_PAYLOAD_BYTES}")
    return payload


# --- work.request (parent -> replica) ---------------------------------------
def build_work_request(
    *, goal: str, priority: str | None = None, constraints: str | None = None,
    cancel_of: str | None = None,
) -> dict[str, Any]:
    """A bounded ``work.request`` payload. ``goal`` is the only required field; ``priority`` is a hint.

    This constructs the CLEAN payload shape (schema-versioned). It carries NO expense/blast/approval/
    capability/replication field — those are not part of the schema, so a request can never smuggle a
    gate decision. ``cancel_of`` makes cancellation a REQUEST (a correlation id to cancel), never a kill.
    """
    payload: dict[str, Any] = {
        "schema_version": WORK_REQUEST_VERSION,
        "goal": _bounded_text(goal, field="goal", required=True, limit=MAX_GOAL_BYTES),
    }
    prio = _priority(priority)
    if prio is not None:
        payload["priority"] = prio
    con = _bounded_text(constraints, field="constraints", required=False, limit=MAX_CONSTRAINTS_BYTES)
    if con is not None:
        payload["constraints"] = con
    cancel = _bounded_id(cancel_of, field="cancel_of")
    if cancel is not None:
        payload["cancel_of"] = cancel
    return _finalize(payload)


def build_work_response(
    *, state: str, text: str | None = None, correlation_id: str | None = None,
    in_reply_to: str | None = None,
) -> dict[str, Any]:
    """A replica's reply to a work.request (replica -> parent). ``state`` is the replica's CLAIMED
    handling disposition (accepted_as_proposal / queued_for_approval / declined / cancel_requested) —
    an untrusted claim, never evidence the work was executed or succeeded."""
    if state not in RESPONSE_STATES:
        raise WorkRequestError(f"work.response state must be one of {sorted(RESPONSE_STATES)}")
    payload: dict[str, Any] = {"schema_version": WORK_RESPONSE_VERSION, "state": state}
    t = _bounded_text(text, field="text", required=False, limit=MAX_TEXT_BYTES)
    if t is not None:
        payload["text"] = t
    cid = _bounded_id(correlation_id, field="correlation_id")
    if cid is not None:
        payload["correlation_id"] = cid
    irt = _bounded_id(in_reply_to, field="in_reply_to")
    if irt is not None:
        payload["in_reply_to"] = irt
    return _finalize(payload)


def build_receipt(
    *, disposition: str, correlation_id: str | None = None, in_reply_to: str | None = None,
    text: str | None = None,
) -> dict[str, Any]:
    """A transport-level ack for a work.request (replica -> parent). ``disposition`` is received/stored/
    imported/rejected/expired/duplicate — a DELIVERY fact, never evidence of execution/success."""
    if disposition not in RECEIPT_DISPOSITIONS:
        raise WorkRequestError(f"receipt disposition must be one of {sorted(RECEIPT_DISPOSITIONS)}")
    payload: dict[str, Any] = {"schema_version": RECEIPT_VERSION, "disposition": disposition}
    cid = _bounded_id(correlation_id, field="correlation_id")
    if cid is not None:
        payload["correlation_id"] = cid
    irt = _bounded_id(in_reply_to, field="in_reply_to")
    if irt is not None:
        payload["in_reply_to"] = irt
    t = _bounded_text(text, field="text", required=False, limit=MAX_TEXT_BYTES)
    if t is not None:
        payload["text"] = t
    return _finalize(payload)


# --- INGESTION GUARD #1: the inbox endpoint --------------------------------
def validate_inbound_payload(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate an inbound parent->replica payload on ARRIVAL at the replica inbox (guard #1).

    Enforces shape/size and (for work.request) that a bounded ``goal`` is present. It returns a
    NORMALIZED copy: the clean schema fields at the top level, plus any EXTRA caller keys quarantined
    under ``_untrusted_extra`` as INERT display data. The importer (guard #2) reads ONLY the top-level
    allowlisted fields and never ``_untrusted_extra`` — so an attacker's ``run_shell`` / ``replicate`` /
    ``AQ_REPLICATION_*`` field is stored inert and neutralized by the allowlist, exactly as required.

    A non-dict payload, an oversize payload, or a work.request with no goal is REJECTED (raises
    WorkRequestError -> 400). Rejection at the door + the importer's allowlist are the two guards.
    """
    if not isinstance(payload, dict):
        raise WorkRequestError("payload must be a JSON object")
    if kind == "work.request":
        clean = build_work_request(
            goal=payload.get("goal"),
            priority=payload.get("priority"),
            constraints=payload.get("constraints"),
            cancel_of=payload.get("cancel_of"),
        )
        known = {"schema_version", "goal", "priority", "constraints", "cancel_of"}
    elif kind == "work.response":
        clean = build_work_response(
            state=payload.get("state"),
            text=payload.get("text"),
            correlation_id=payload.get("correlation_id"),
            in_reply_to=payload.get("in_reply_to"),
        )
        known = {"schema_version", "state", "text", "correlation_id", "in_reply_to"}
    elif kind == "receipt":
        clean = build_receipt(
            disposition=payload.get("disposition"),
            correlation_id=payload.get("correlation_id"),
            in_reply_to=payload.get("in_reply_to"),
            text=payload.get("text"),
        )
        known = {"schema_version", "disposition", "correlation_id", "in_reply_to", "text"}
    else:
        raise WorkRequestError(f"kind {kind!r} is not an inbound work kind {sorted(INBOUND_WORK_KINDS)}")

    # Quarantine any extra caller-supplied keys as INERT data (never promoted to the top level, never
    # read by the importer). Bounded so it cannot exhaust storage.
    extra = {k: v for k, v in payload.items() if k not in known}
    if extra:
        # Size-bound the quarantine; if it would blow the payload cap, drop it entirely (it is inert
        # display data — losing it never loses a capability).
        candidate = dict(clean)
        candidate["_untrusted_extra"] = extra
        try:
            return _finalize(candidate)
        except WorkRequestError:
            return clean
    return clean


# --- INGESTION GUARD #2: the importer field allowlist -----------------------
def select_importable_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Map a stored work.request payload to ONLY its importable fields (guard #2, deny-by-default).

    THE load-bearing neutralization: reads ONLY ``IMPORTABLE_FIELDS`` from the TOP level, re-validates
    each, and drops everything else — including anything hiding in ``_untrusted_extra``. The result is
    a bounded ``{goal, priority?, constraints?, cancel_of?}`` and NOTHING that could actuate. It never
    reads expense/blast/approval/capability/replication/shell/measured-success even if present.
    """
    if not isinstance(payload, dict):
        raise WorkRequestError("stored work.request payload is not an object")
    goal = _bounded_text(payload.get("goal"), field="goal", required=True, limit=MAX_GOAL_BYTES)
    out: dict[str, Any] = {"goal": goal}
    prio = _priority(payload.get("priority"))
    if prio is not None:
        out["priority"] = prio
    con = _bounded_text(payload.get("constraints"), field="constraints", required=False,
                        limit=MAX_CONSTRAINTS_BYTES)
    if con is not None:
        out["constraints"] = con
    cancel = _bounded_id(payload.get("cancel_of"), field="cancel_of")
    if cancel is not None:
        out["cancel_of"] = cancel
    # Structural proof: the mapped proposal fields are a subset of the allowlist, always.
    assert set(out) <= IMPORTABLE_FIELDS, "importer must only ever emit allowlisted fields"
    return out


def work_import_enabled(env: dict[str, str] | None = None) -> bool:
    """True only when the operator has EXPLICITLY enabled the work importer (default OFF).

    Land-inert contract: unset / "0" / "false" / "" => the importer never runs, so a default checkout
    (and the live loop) never acts on a single stored work.request. Only an explicit truthy value flips
    it on, and even then every local gate still runs on the imported item.
    """
    source = env if env is not None else __import__("os").environ
    raw = (source.get("AQ_COMMS_WORK_IMPORT") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}
