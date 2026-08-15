"""Slice 3: the actor may REQUEST to open a gate, but can NEVER self-grant.

Design: design_authority_envelope.md Slice 3; contract-shaped toward the V2 AuthorityPolicy /
WorkContract receipt JSON (BB #2716).

WHAT THIS IS (and, honestly, what it is NOT)
--------------------------------------------
When the loop parks work because the categorical envelope (Slice 2) GATED a capability CLASS, or
because a substrate manifest (the Slice-2 substrate wall) proved a claimed class STRUCTURALLY ABSENT,
this module turns that park into a bounded, TYPED authority-change REQUEST: a proposal that says
"the plan wants capability class X; here is why, how wide, for how long, and how reversible". That is
ALL it is -- a proposal with ZERO authority.

  * It does NOT modify the envelope. The envelope is operator-owned config (instance.yaml
    ``authority_envelope:``); Slice 2 already made editing that config the SOLE mechanism that opens
    a gate. A request never touches it.
  * It does NOT change the gate decision. The work still parks for a human exactly as before; the
    request is merely the STRUCTURED ARTIFACT the human acts on, emitted ALONGSIDE the existing
    park + notify.
  * It CANNOT approve itself. Approval is an OPERATOR/HOST action, out-of-band: the operator either
    edits the envelope config to open the class (the real "open"), or a higher-authority principal
    records a disposition on the request (the review record). The actor's own process credential
    (aq_loop) has INSERT on the request table and NO write at all on the disposition table -- the
    self-grant boundary is grant-backed in schema/999, not merely a convention here.

WHERE APPROVAL AUTHORITY LIVES (read before trusting a "status"):
  approved/denied on a request is a REVIEW RECORD written by a higher-authority principal. It is NOT
  what opens the gate. OPENING THE GATE = the operator editing ``authority_envelope:`` in
  instance.yaml to mark the class ``open`` (Slice 2). A request may sit approved forever and the gate
  stays closed until that operator edit happens. This is deliberate: it keeps the one irreversible
  authority act (widening the envelope) in exactly one place the actor cannot reach.

Flag-gated by ``AQ_AUTHORITY_REQUESTS``; default OFF => no request is ever built or persisted and the
park/notify path is byte-identical to Slice-2 behaviour.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Mapping

POLICY_VERSION = "authority-request-v1"

#: Environment flag. Default OFF => this module is never consulted; nothing is persisted.
FLAG = "AQ_AUTHORITY_REQUESTS"

# -- request status (DERIVED, never the actor's to set) -----------------------------------------
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_DENIED = "denied"
DISPOSITIONS = frozenset({STATUS_APPROVED, STATUS_DENIED})

# -- why the request exists (provenance) --------------------------------------------------------
REASON_GATED_CLASS = "gated_class"            #: the envelope marks this class ``gated``.
REASON_SUBSTRATE_MISMATCH = "substrate_mismatch"  #: the step CLAIMED a class the substrate LACKS.

# -- reversibility labels -----------------------------------------------------------------------
REVERSIBLE = "reversible"
IRREVERSIBLE = "irreversible"


def enabled(env: Mapping[str, str] | None = None) -> bool:
    """True iff the authority-change-request emission is switched on. Default OFF."""
    source = env if env is not None else os.environ
    return str(source.get(FLAG, "")).strip().lower() in {"1", "true", "yes", "on"}


def action_digest(plan) -> str:
    """sha256 of the PROPOSED ACTION (the plan the envelope gated), canonicalised so the SAME plan
    parked on repeated cycles yields the SAME digest -- that stability is what makes the dedupe key
    (instance, class, digest) suppress a re-park instead of spamming a fresh request every cycle.

    Canonical form: sorted-key JSON with a str fallback for any non-JSON scalar. A non-mapping /
    missing plan digests its ``repr`` so an opaque plan still yields a stable, non-empty digest."""
    try:
        canonical = json.dumps(plan, sort_keys=True, default=str)
    except (TypeError, ValueError):
        canonical = repr(plan)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuthorityChangeRequest:
    """A bounded, typed proposal to OPEN one capability class for one proposed action.

    Immutable value object. It carries NO authority: persisting it neither opens the envelope nor
    changes the gate. ``status`` here is always the initial PENDING -- the durable, effective status
    is DERIVED from a separate operator-written disposition row (see the store), never set by the
    actor. Shaped toward the V2 AuthorityPolicy/WorkContract receipt JSON (BB #2716)."""
    instance_id: str
    capability_class: str
    proposed_action_digest: str
    rationale: str
    reversibility: str            # REVERSIBLE | IRREVERSIBLE
    requested_scope: str          # how WIDE (which class, over what surface)
    requested_bound: str          # for how long / how many (bounded, finite)
    reason_kind: str              # REASON_GATED_CLASS | REASON_SUBSTRATE_MISMATCH
    work_id: int | None = None
    status: str = STATUS_PENDING
    detail: dict = field(default_factory=dict)
    policy_version: str = POLICY_VERSION

    def to_receipt(self) -> dict:
        """V2 AuthorityPolicy/WorkContract-shaped receipt (BB #2716). ``verdict`` is fixed to
        ``requested``: this is a PROPOSAL, never an authorization."""
        return {
            "verdict": "requested",
            "capability_class": self.capability_class,
            "proposed_action_digest": self.proposed_action_digest,
            "reversible": self.reversibility == REVERSIBLE,
            "internally_authorized": False,   # an actor-emitted request is NEVER self-authorized.
            "requested_scope": self.requested_scope,
            "requested_bound": self.requested_bound,
            "reason_kind": self.reason_kind,
            "rationale": self.rationale,
            "status": self.status,
            "instance_id": self.instance_id,
            "work_id": self.work_id,
            "policy_version": self.policy_version,
        }


def build_requests_from_verdict(verdict, *, work, instance_id: str) -> list[AuthorityChangeRequest]:
    """Turn a GATED :class:`runner.authority_envelope.EnvelopeVerdict` into the bounded requests it
    justifies: ONE per capability class that forced the categorical gate.

    A class the substrate PROVED absent (``substrate_mismatch_classes``) is emitted with the stronger
    ``substrate_mismatch`` provenance and takes precedence over the ordinary ``gated_class`` provenance
    for the same class, so a class never yields two requests. Returns ``[]`` for a non-gated verdict,
    or a gate with no NAMED class (a bare must_ask_first / opaque fail-closed hold -- there is no
    specific class to request opening, so there is nothing bounded to propose)."""
    if verdict is None or not getattr(verdict, "gated", False):
        return []

    digest = action_digest(getattr(work, "plan", None) or {})
    work_id = getattr(work, "id", None)
    reversibility = REVERSIBLE if getattr(work, "reversible", True) else IRREVERSIBLE
    base_rationale = (getattr(work, "rationale", "") or "").strip()

    mismatch = tuple(getattr(verdict, "substrate_mismatch_classes", ()) or ())
    gated = tuple(getattr(verdict, "gated_classes", ()) or ())
    verdict_reasons = tuple(getattr(verdict, "reasons", ()) or ())

    requests: list[AuthorityChangeRequest] = []
    seen: set[str] = set()
    # substrate mismatch first (stronger provenance), then ordinary gated classes.
    for cls in list(mismatch) + [c for c in gated if c not in set(mismatch)]:
        if cls in seen:
            continue
        seen.add(cls)
        is_mismatch = cls in set(mismatch)
        reason_kind = REASON_SUBSTRATE_MISMATCH if is_mismatch else REASON_GATED_CLASS
        why = (
            f"the plan proposes an action in capability class {cls!r}, which the operator's "
            f"authority envelope holds GATED"
            if not is_mismatch else
            f"the plan CLAIMS capability class {cls!r} that the substrate structurally LACKS"
        )
        rationale = f"{why}. Actor's plan rationale: {base_rationale}" if base_rationale else why
        requests.append(AuthorityChangeRequest(
            instance_id=instance_id,
            capability_class=cls,
            proposed_action_digest=digest,
            rationale=rationale,
            reversibility=reversibility,
            requested_scope=(
                f"open capability class {cls!r} for this instance's envelope "
                f"(operator edit of authority_envelope:{cls})"),
            requested_bound=(
                f"single proposed action (work #{work_id}, plan digest {digest[:12]}...); "
                f"NOT a standing grant"),
            reason_kind=reason_kind,
            work_id=work_id,
            detail={
                "verdict_reasons": list(verdict_reasons),
                "classes": list(getattr(verdict, "classes", ()) or ()),
                "substrate_bound": bool(getattr(verdict, "substrate_bound", False)),
            },
        ))
    return requests
