"""Replication authority-envelope INHERITANCE — the compounding-autonomy safeguard (Slice 4).

Design: design_authority_envelope.md Slice 4. Kevin's rule (2026-08-15):

    The replication REQUEST states exactly what the child gets, INCLUDING whether it may widen
    its own envelope. Default: child envelope <= parent envelope. A wider grant (or the right to
    self-widen) must be EXPLICITLY stated in the request AND authorized by a higher authority
    (host review). "Full authority" = full within the stated finite grant; the one thing not
    silently delegable is the authority to widen the grant.

THE INVARIANT PROVED HERE
-------------------------
A replicated child can NEVER silently have a WIDER authority envelope than its parent. The
child's EFFECTIVE envelope is the intersection of (parent envelope) and (requested child
envelope) — a capability class is OPEN for the child only if it is open in BOTH — UNLESS the
request carries an authorization that the HOST (not the guest) validates. Fail-CLOSED: a
missing/garbage envelope in the request yields the fully-gated default, never something wider.

WHERE THE AUTHORIZATION BOUNDARY IS (honest — read before trusting a widen)
---------------------------------------------------------------------------
This module is PURE (no host effects) and therefore guest-reachable — the in-container propose
endpoint imports it to SHAPE-validate a request and the host importer echoes the effective
grant. So this module must never be the thing that AUTHORIZES a widen from data alone: the widen
decision (:func:`authorize_widen`) compares a request-carried marker against a token that lives
ONLY in the HOST's environment (``AQ_REPLICATION_WIDEN_TOKEN`` — an ``AQ_REPLICATION_*`` key the
modification allowlist forbids a guest from ever setting on itself). A guest calling
``authorize_widen`` passes its OWN env, which does not contain the host token, so it can never
self-authorize. This mirrors the existing host-executes / guest-never discipline (``override_enabled``
in ``replication_request``): the function is importable everywhere; only the host holds the secret.

The ground-truth PARENT envelope is resolved HOST-SIDE from the source instance.yaml (see
``host_replica_stack._parent_envelope``), never from the guest's packet — a guest cannot lie about
its parent's envelope to inflate the intersection.

Flag-gated by ``AQ_REPLICATION_AUTHORITY_ENVELOPE``; default OFF => the host never computes or
applies a child envelope and the packet's optional field is ignored (byte-identical standup).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Mapping

from runner.authority_envelope import (
    ALL_CLASSES,
    DISPOSITION_GATED,
    DISPOSITION_OPEN,
    HIGH_CONSEQUENCE_CLASSES,
    AuthorityEnvelope,
    DEFAULT_ENVELOPE,
    EnvelopeConfigError,
    from_config,
)

#: Host flag. Default OFF => the whole Slice-4 path is inert (packet field ignored, no overlay).
FLAG = "AQ_REPLICATION_AUTHORITY_ENVELOPE"

#: HOST-ONLY widen-authorization secret. An ``AQ_REPLICATION_*`` key: the modification allowlist's
#: forbidden-prefix rule means a guest can NEVER set this on itself, so a guest cannot mint the
#: authorization that opens a class beyond its parent. The host sets it out-of-band (operator/host
#: review). Empty/unset => NO widen is ever authorized (fail-closed).
WIDEN_TOKEN_ENV = "AQ_REPLICATION_WIDEN_TOKEN"

#: HOST-WRITTEN effective grant carried into the child (JSON of class -> open|gated). The guest may
#: NOT set this directly (it is in the modification denylist) — the host computes it from the
#: intersection/authorized-widen and writes it into the child's compose overlay. The child's
#: config loader reads it as the operative envelope.
GRANT_ENV = "AQ_AUTHORITY_ENVELOPE_GRANT"
#: HOST-WRITTEN effective may-self-widen flag carried into the child ("1"/"0"). Guest-un-settable.
SELF_WIDEN_ENV = "AQ_AUTHORITY_MAY_SELF_WIDEN"


class ChildEnvelopeRequestError(ValueError):
    """The packet's ``authority_envelope`` request is malformed (bad class / disposition / type)."""


def enabled(env: Mapping[str, str] | None = None) -> bool:
    """True iff the Slice-4 replication-envelope path is switched on. Default OFF."""
    source = env if env is not None else os.environ
    return str(source.get(FLAG, "")).strip().lower() in {"1", "true", "yes", "on"}


# -- request shape ------------------------------------------------------------------------------
def validate_child_envelope_request(req: Any) -> list[str]:
    """SHAPE validation of the packet's optional ``authority_envelope`` request. Empty list == OK.

    Accepted shape (every key optional)::

        {"dispositions": {<capability_class>: "open"|"gated", ...},
         "may_self_widen": <bool>,
         "widen_authorization": "<opaque marker validated HOST-side>"}

    This validates STRUCTURE only. It never decides whether a widen is authorized — that is a
    host-side decision (:func:`authorize_widen`) against a host-only secret.
    """
    if req is None:
        return []
    if not isinstance(req, Mapping):
        return ["authority_envelope must be a mapping"]
    errors: list[str] = []
    unknown = set(req) - {"dispositions", "may_self_widen", "widen_authorization"}
    if unknown:
        errors.append(
            f"authority_envelope has unknown key(s) {sorted(unknown)}; allowed: "
            "'dispositions', 'may_self_widen', 'widen_authorization'")
    disp = req.get("dispositions")
    if disp is not None:
        if not isinstance(disp, Mapping):
            errors.append("authority_envelope.dispositions must be a mapping of class -> open|gated")
        else:
            for name, value in disp.items():
                cls = str(name).strip()
                if cls not in ALL_CLASSES:
                    errors.append(
                        f"authority_envelope.dispositions names unknown class {name!r}; "
                        f"known: {sorted(ALL_CLASSES)}")
                    continue
                if str(value).strip().lower() not in {DISPOSITION_OPEN, DISPOSITION_GATED}:
                    errors.append(
                        f"authority_envelope.dispositions[{name!r}] must be 'open' or 'gated', "
                        f"got {value!r}")
    if "may_self_widen" in req and not isinstance(req.get("may_self_widen"), bool):
        errors.append("authority_envelope.may_self_widen must be a boolean")
    tok = req.get("widen_authorization")
    if tok is not None and not isinstance(tok, str):
        errors.append("authority_envelope.widen_authorization must be a string marker")
    return errors


def _requested_envelope(req: Any) -> AuthorityEnvelope:
    """Parse the requested child dispositions into an envelope. FAIL-CLOSED: a missing/garbage
    request yields the fully-gated DEFAULT envelope (never something wider)."""
    if not isinstance(req, Mapping):
        return DEFAULT_ENVELOPE
    disp = req.get("dispositions")
    if disp is None:
        return DEFAULT_ENVELOPE
    try:
        return from_config(disp)
    except EnvelopeConfigError:
        return DEFAULT_ENVELOPE


# -- inheritance algebra ------------------------------------------------------------------------
def intersect(parent: AuthorityEnvelope, requested: AuthorityEnvelope) -> AuthorityEnvelope:
    """The MORE-RESTRICTIVE envelope: a class is OPEN only if open in BOTH parent AND requested.

    The result is <= parent AND <= requested on every class. This is the default child envelope."""
    disp = {
        cls: (DISPOSITION_OPEN
              if parent.disposition(cls) == DISPOSITION_OPEN
              and requested.disposition(cls) == DISPOSITION_OPEN
              else DISPOSITION_GATED)
        for cls in ALL_CLASSES
    }
    return from_config(disp)


def widen_classes(parent: AuthorityEnvelope, requested: AuthorityEnvelope) -> tuple[str, ...]:
    """The classes the request tries to OPEN beyond the parent (open in request, gated in parent).

    These are exactly the openings that require higher-authority authorization."""
    return tuple(
        cls for cls in sorted(ALL_CLASSES)
        if requested.disposition(cls) == DISPOSITION_OPEN
        and parent.disposition(cls) != DISPOSITION_OPEN
    )


def subset_of(child: AuthorityEnvelope, parent: AuthorityEnvelope) -> bool:
    """True iff every class OPEN in ``child`` is also OPEN in ``parent`` (child <= parent)."""
    return all(
        parent.disposition(cls) == DISPOSITION_OPEN
        for cls in ALL_CLASSES
        if child.disposition(cls) == DISPOSITION_OPEN
    )


def authorize_widen(marker: Any, host_env: Mapping[str, str] | None = None) -> bool:
    """HOST-side authorization of a widen. True iff the host holds a non-empty
    ``AQ_REPLICATION_WIDEN_TOKEN`` and the request-carried ``marker`` matches it EXACTLY.

    PURE + host-only-by-secret: the function is importable everywhere, but a guest passing its own
    env (which cannot contain the host token — the key is guest-forbidden) can never make this
    return True. Empty/unset host token => never authorized (fail-closed)."""
    source = host_env if host_env is not None else os.environ
    host_token = str(source.get(WIDEN_TOKEN_ENV, "")).strip()
    if not host_token:
        return False
    return isinstance(marker, str) and marker.strip() == host_token


@dataclass(frozen=True)
class InheritanceResult:
    """The host-computed effective child envelope + provenance (shaped toward V2 AuthorityPolicy)."""
    effective: AuthorityEnvelope
    may_self_widen: bool
    requested_widen: tuple[str, ...]     # classes the request tried to open beyond the parent
    granted_widen: tuple[str, ...]       # widen classes an authorized request DID open
    stripped_widen: tuple[str, ...]      # widen classes stripped for lack of authorization
    self_widen_stripped: bool            # a requested may_self_widen was stripped (unauthorized)
    authorized: bool                     # a valid host authorization accompanied a widen request
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "effective_envelope": self.effective.as_dict(),
            "may_self_widen": self.may_self_widen,
            "requested_widen": list(self.requested_widen),
            "granted_widen": list(self.granted_widen),
            "stripped_widen": list(self.stripped_widen),
            "self_widen_stripped": self.self_widen_stripped,
            "authorized": self.authorized,
            "reasons": list(self.reasons),
        }


def resolve_child_envelope(
    parent: AuthorityEnvelope, request: Any, *, host_env: Mapping[str, str] | None = None,
) -> InheritanceResult:
    """Compute the child's EFFECTIVE envelope from the ground-truth ``parent`` and the packet's
    ``request`` (its ``authority_envelope`` field), applying host-validated widening.

    - Default: effective = intersection(parent, requested) — child <= parent on every class.
    - A request that OPENS a class beyond the parent, or asks ``may_self_widen``, is a WIDEN. A
      widen is honored ONLY if the request carries a marker the HOST authorizes
      (:func:`authorize_widen`). Otherwise the widen is STRIPPED and the child falls back to the
      restrictive intersection; ``may_self_widen`` is forced False.
    - FAIL-CLOSED: a missing/garbage request => requested is the fully-gated default => the child
      is at most the intersection (never wider than parent).
    """
    requested = _requested_envelope(request)
    req_map = request if isinstance(request, Mapping) else {}
    wants_self_widen = bool(req_map.get("may_self_widen")) if isinstance(
        req_map.get("may_self_widen"), bool) else False
    marker = req_map.get("widen_authorization")

    req_widen = widen_classes(parent, requested)
    needs_auth = bool(req_widen) or wants_self_widen
    authorized = authorize_widen(marker, host_env) if needs_auth else False

    reasons: list[str] = []
    if needs_auth and authorized:
        # Higher authority validated the widen: honor the requested envelope as stated.
        effective = requested
        eff_self_widen = wants_self_widen
        granted, stripped, self_stripped = req_widen, (), False
        if req_widen:
            reasons.append(f"host-authorized widen opened class(es): {', '.join(req_widen)}")
        if wants_self_widen:
            reasons.append("host-authorized may_self_widen granted")
    elif needs_auth and not authorized:
        # Widen requested WITHOUT valid host authorization: strip it, fall back to intersection.
        effective = intersect(parent, requested)
        eff_self_widen = False
        granted, stripped = (), req_widen
        self_stripped = wants_self_widen
        if req_widen:
            reasons.append(
                f"UNAUTHORIZED widen stripped (class(es) gated in parent): {', '.join(req_widen)} "
                "-> child stays at parent∩request")
        if wants_self_widen:
            reasons.append("UNAUTHORIZED may_self_widen stripped -> child may NOT self-widen")
    else:
        # No widen requested: the child is the intersection (== request, since request <= parent).
        effective = intersect(parent, requested)
        eff_self_widen = False
        granted, stripped, self_stripped = (), (), False
        reasons.append("child envelope = parent ∩ request (no widen requested)")

    # Invariant belt-and-braces: without an authorized widen, effective MUST be <= parent.
    if not (needs_auth and authorized) and not subset_of(effective, parent):
        # Should be unreachable (intersection is always <= parent); fail HARD-closed if ever not.
        effective = intersect(parent, DEFAULT_ENVELOPE)
        reasons.append("fail-closed: effective was not <= parent; forced to parent ∩ default")

    return InheritanceResult(
        effective=effective, may_self_widen=eff_self_widen, requested_widen=req_widen,
        granted_widen=granted, stripped_widen=stripped, self_widen_stripped=self_stripped,
        authorized=(needs_auth and authorized), reasons=tuple(reasons))


# -- child materialization ----------------------------------------------------------------------
def grant_env(result: InheritanceResult) -> dict[str, str]:
    """The HOST-written env the child inherits: the effective grant (JSON) + the self-widen flag.

    These keys are guest-un-settable (see the modification denylist) — only the host writes them,
    carrying the *computed* effective envelope (never the guest's raw request)."""
    import json
    return {
        GRANT_ENV: json.dumps(result.effective.as_dict(), sort_keys=True),
        SELF_WIDEN_ENV: "1" if result.may_self_widen else "0",
    }


def render_authority_overlay(result: InheritanceResult, *, service: str = "app") -> str:
    """A compose overlay injecting the host-computed effective envelope into the child's service.

    Deliberately SEPARATE from ``render_config_overlay``: the effective envelope is host-authority,
    not a guest config delta, so it does NOT travel through the guest CONFIG_ALLOWLIST (which a
    guest could set directly). Rendered via yaml.safe_dump so no value can inject compose structure.
    """
    import yaml
    env_block = grant_env(result)
    doc = {"services": {service: {"environment": dict(env_block)}}}
    header = (
        "# GENERATED for a replica stack — HOST-computed authority envelope (Slice 4).\n"
        "# Effective = parent ∩ request (+ host-authorized widen only). Guest-un-settable.\n"
    )
    return header + yaml.safe_dump(doc, sort_keys=True)
