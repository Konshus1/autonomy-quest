"""Agent-comms authentication, principal derivation, and ACLs (#4834 comms Phase 0, design §8).

Foundational rule (design §8.1): every message is *untrusted input authenticated to a principal*.
Authentication establishes WHO sent the bytes; it never makes the content true, safe, or authorized.

This module is guest-reachable and grants NO capability. It only:
  1. verifies a presented bearer credential in constant time,
  2. DERIVES the sender principal (origin instance id + principal id + lineage) from that
     credential — never from a caller-supplied ``from_handle`` / ``sender_name`` / payload
     ``AQ_INSTANCE_ID`` (design §8.2), and
  3. enforces a channel/kind ACL: a principal may publish only to channels its lineage allows.

Credentials are configured server-side, distinct from any capability. Two shapes:
  * per-instance relay credential — bound to the host-assigned instance id + lineage + a kind
    allowlist; may publish to its own ``instance/<id>/*`` channels and to ``lineage/<x>/*`` channels
    for any ``x`` in its lineage (itself + ancestors).
  * operator/UI credential — may publish ``operator.message`` on any channel (the human operator
    is trusted to address the fleet), and nothing else.

A replica is NEVER given the host bus-admin token, the parent DB DSN, another instance's
credential, or a replication override (design §8.2).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from hmac import compare_digest
from typing import Any

from management.api.comms_envelope import ALLOWED_KINDS


class AuthError(Exception):
    """Base for an authentication/authorization failure. ``status`` is the HTTP code to emit."""

    status = 400

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class Unauthenticated(AuthError):
    """No credential, or an unrecognized/mismatched credential (design §8.2). -> 401."""

    status = 401


class AclDenied(AuthError):
    """Authenticated, but the principal may not publish this kind to this channel (design §8.3). -> 403."""

    status = 403


@dataclass(frozen=True)
class Principal:
    """A server-derived sender identity. Nothing here comes from the message body."""

    principal_id: str
    origin_instance_id: str
    # Instance ids this principal's lineage authorizes it to address (itself + ancestors).
    lineage: tuple[str, ...] = ()
    # Message kinds this principal may publish. Empty tuple => none.
    kinds: tuple[str, ...] = ()
    is_operator: bool = False
    # Scoped read-only outbox capability (design §4.3: "/outbox uses separate scoped credentials").
    # The host relay presents this to PULL a replica's outbox; it grants NO publish capability.
    can_read_outbox: bool = False

    def may_kind(self, kind: str) -> bool:
        if self.is_operator:
            return kind == "operator.message"
        return kind in self.kinds

    def may_read_outbox(self) -> bool:
        # Operator (UI/debug) and a scoped relay reader may pull the outbox; a bare publishing
        # instance principal may also read ITS OWN outbox (it is that instance's local store).
        return self.is_operator or self.can_read_outbox


def _channel_lineage_ids(channel: str) -> tuple[str | None, str | None]:
    """Return ``(instance_scope, lineage_scope)`` parsed from a channel string.

    Channels (design §4.2):
      ``instance/<id>/...``   -> instance_scope=<id>
      ``lineage/<id>/...``    -> lineage_scope=<id>
      ``fleet/<name>``        -> neither (operator/explicit-membership only in Phase 0)
    """
    parts = channel.split("/")
    if len(parts) >= 2 and parts[0] == "instance":
        return parts[1], None
    if len(parts) >= 2 and parts[0] == "lineage":
        return None, parts[1]
    return None, None


@dataclass
class CommsAuthenticator:
    """Verifies credentials and enforces the publish ACL. Built from a credentials map.

    ``credentials`` maps a bearer token -> a principal descriptor::

        {"principal_id": "...", "origin_instance_id": "urn:uuid:...",
         "lineage": ["urn:uuid:parent", ...], "kinds": ["status.report", ...],
         "operator": false}
    """

    credentials: dict[str, dict[str, Any]] = field(default_factory=dict)

    # -- construction ---------------------------------------------------------
    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "CommsAuthenticator":
        """Build from environment. Sources, all optional (absent => that credential unusable):

        * ``AQ_COMMS_INSTANCE_TOKEN`` + ``AQ_INSTANCE_ID`` (+ optional ``AQ_COMMS_PARENT_INSTANCE_ID``,
          ``AQ_COMMS_ALLOWED_KINDS`` csv) -> this instance's own relay credential.
        * ``AQ_COMMS_OPERATOR_TOKEN`` -> the operator/UI credential.
        * ``AQ_COMMS_CREDENTIALS`` (JSON ``{token: descriptor}``) -> additional principals (used by
          the host relay / tests). Merged last.
        """
        source = env if env is not None else os.environ
        creds: dict[str, dict[str, Any]] = {}

        instance_token = (source.get("AQ_COMMS_INSTANCE_TOKEN") or "").strip()
        instance_id = (source.get("AQ_INSTANCE_ID") or "").strip()
        if instance_token and instance_id:
            parent = (source.get("AQ_COMMS_PARENT_INSTANCE_ID") or "").strip()
            lineage = [instance_id] + ([parent] if parent else [])
            kinds_raw = (source.get("AQ_COMMS_ALLOWED_KINDS") or "").strip()
            kinds = (
                [k.strip() for k in kinds_raw.split(",") if k.strip()]
                if kinds_raw
                else sorted(ALLOWED_KINDS - {"operator.message"})
            )
            creds[instance_token] = {
                "principal_id": f"instance:{instance_id}",
                "origin_instance_id": instance_id,
                "lineage": lineage,
                "kinds": kinds,
                "operator": False,
            }

        # Scoped host-relay OUTBOX-READER credential (design §4.3). Read-only: it may PULL this
        # instance's outbox for the host relay but publishes NOTHING (kinds empty, not operator).
        relay_read_token = (source.get("AQ_COMMS_OUTBOX_READ_TOKEN") or "").strip()
        if relay_read_token:
            creds[relay_read_token] = {
                "principal_id": "host:outbox-relay",
                "origin_instance_id": instance_id or "host",
                "lineage": [],
                "kinds": [],
                "operator": False,
                "can_read_outbox": True,
            }

        operator_token = (source.get("AQ_COMMS_OPERATOR_TOKEN") or "").strip()
        if operator_token:
            creds[operator_token] = {
                "principal_id": "operator:local",
                "origin_instance_id": instance_id or "operator",
                "lineage": [],
                "kinds": ["operator.message"],
                "operator": True,
            }

        extra = (source.get("AQ_COMMS_CREDENTIALS") or "").strip()
        if extra:
            try:
                parsed = json.loads(extra)
                if isinstance(parsed, dict):
                    creds.update(parsed)
            except ValueError:
                pass
        return cls(credentials=creds)

    # -- authentication -------------------------------------------------------
    @staticmethod
    def extract_token(headers: Any) -> str | None:
        """Pull a bearer token from ``Authorization: Bearer <t>`` or ``X-AQ-Comms-Token: <t>``."""
        get = headers.get
        auth = (get("authorization") or get("Authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            return auth[7:].strip() or None
        direct = (get("x-aq-comms-token") or get("X-AQ-Comms-Token") or "").strip()
        return direct or None

    def authenticate(self, headers: Any) -> Principal:
        """Return the derived ``Principal`` for a valid credential, else raise ``Unauthenticated``.

        Constant-time compare against every configured token so a caller cannot learn which
        tokens exist by timing. Identity comes ONLY from the matched descriptor.
        """
        token = self.extract_token(headers)
        if not token:
            raise Unauthenticated("comms credential required (Authorization: Bearer <token>)")
        matched: dict[str, Any] | None = None
        # Iterate ALL entries with a constant-time compare each; do not early-return on match so
        # the work is independent of which token (if any) matches.
        for configured, descriptor in self.credentials.items():
            if compare_digest(token, configured):
                matched = descriptor
        if matched is None:
            raise Unauthenticated("comms credential not recognized")
        return Principal(
            principal_id=str(matched.get("principal_id") or "unknown"),
            origin_instance_id=str(matched.get("origin_instance_id") or "unknown"),
            lineage=tuple(matched.get("lineage") or ()),
            kinds=tuple(matched.get("kinds") or ()),
            is_operator=bool(matched.get("operator")),
            can_read_outbox=bool(matched.get("can_read_outbox")),
        )

    def authorize_outbox_read(self, principal: Principal) -> None:
        """Raise ``AclDenied`` unless ``principal`` may pull the local outbox (host relay / operator).

        The outbox is read-only evidence egress: this authorizes READ only and grants no publish or
        actuation capability. A principal with no outbox scope (a plain replica credential presented
        cross-instance) is denied — it can only PUBLISH to its own lineage, never siphon an outbox.
        """
        if not principal.may_read_outbox():
            raise AclDenied(
                f"principal {principal.principal_id!r} may not read the outbox "
                f"(requires the scoped relay/operator credential)")

    # -- authorization (ACL) --------------------------------------------------
    def authorize_publish(
        self, principal: Principal, *, channel: str, kind: str,
        target_instance_id: str | None = None,
    ) -> None:
        """Raise ``AclDenied`` unless ``principal`` may publish ``kind`` to ``channel``/``target``.

        Rules (design §4.2/§8.3):
          * kind must be in the principal's allowlist (operator: only ``operator.message``).
          * operator may address any channel/target.
          * an instance principal may address ``instance/<self>/...`` and ``lineage/<x>/...`` for
            any ``x`` in its lineage; a direct ``target_instance_id`` must be in its lineage. It may
            NOT publish to another instance's private channel merely by naming it (no fuzzy prefix
            trust) — that is the cross-instance ACL denial.
        """
        if not principal.may_kind(kind):
            raise AclDenied(
                f"principal {principal.principal_id!r} may not publish kind {kind!r}"
            )
        if principal.is_operator:
            return

        instance_scope, lineage_scope = _channel_lineage_ids(channel)
        lineage = set(principal.lineage) | {principal.origin_instance_id}

        if instance_scope is not None:
            if instance_scope not in lineage:
                raise AclDenied(
                    f"principal {principal.origin_instance_id!r} may not publish to another "
                    f"instance's channel {channel!r}"
                )
        elif lineage_scope is not None:
            if lineage_scope not in lineage:
                raise AclDenied(
                    f"principal {principal.origin_instance_id!r} may not publish to lineage "
                    f"channel {channel!r} outside its own lineage"
                )
        else:
            # fleet/<name> or an unscoped channel: not open to a bare instance principal in Phase 0
            # (explicit membership/ACL creation is host/parent policy, design §4.2).
            raise AclDenied(
                f"channel {channel!r} requires explicit membership; not open to instance principals"
            )

        if target_instance_id is not None and target_instance_id not in lineage:
            raise AclDenied(
                f"principal {principal.origin_instance_id!r} may not directly target instance "
                f"{target_instance_id!r} (outside its lineage)"
            )
