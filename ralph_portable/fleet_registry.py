"""Container/fleet registry, discovery & host-brokered comms (CONTAINER_FLEET_REGISTRY_SPEC).

Kevin's requirement: each AQ docker container REGISTERS with the host + is IDENTIFIED, so
containers/fleets discover + message each other. Same doctrine as replication (Slice 4): the HOST is
authoritative and mediates everything; the GUEST proposes; the guest never gets docker.sock.

- Identity is HOST-MINTED, never self-asserted: `member_id` is derived host-side (traversal-safe,
  reusing the replication sanitizer) and bound to a host-signed HMAC token. A container cannot forge
  another member's identity — the token needs the host secret + that member's server-held nonce.
- Registration rejects the same escape fields replication does, plus any self-asserted identity.
- Discovery + messaging are FLEET-SCOPED and host-brokered: no container enumerates the whole host,
  and no container opens a direct channel to another — the host is the only broker (mirrors "host
  executes, guest never touches docker.sock").

Stdlib-only + injectable clock (like the rest of ralph_portable), so it is deterministic + testable.
This is INFRA the container/operator use — it is NOT on the mission loop's hot path.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass, field
from typing import Any, Callable

from ralph_portable.host_replication_executor import _safe_component

_MAX_PAYLOAD = 16 * 1024  # inter-fleet message payloads are bounded (untrusted-content discipline)
_DEFAULT_STALE_TTL = 90   # seconds without a heartbeat -> member marked stale (labeled, not deleted)


# ---- host-signed identity ------------------------------------------------------------------
def mint_identity(member_id: str, fleet_id: str, nonce: str, host_secret: str) -> str:
    """The container's credential: HMAC-SHA256(host_secret, member_id|fleet_id|nonce). The secret +
    nonce are HOST-HELD, so a container cannot compute another member's token — no identity forgery."""
    msg = f"{member_id}|{fleet_id}|{nonce}".encode()
    return hmac.new(host_secret.encode(), msg, hashlib.sha256).hexdigest()


def verify_token(member_id: str, fleet_id: str, nonce: str, host_secret: str, presented: str) -> bool:
    expected = mint_identity(member_id, fleet_id, nonce, host_secret)
    return hmac.compare_digest(expected, str(presented or ""))


# ---- registration validation (forbidden-field guard, mirrors replication) ------------------
def validate_registration(packet: Any) -> list[str]:
    """A guest registration packet. Rejects docker.sock/guest-executes AND any SELF-ASSERTED identity
    (member_id / host_token / fleet assignment) — the host mints those, the guest may only REQUEST."""
    if not isinstance(packet, dict):
        return ["registration must be a mapping"]
    errors: list[str] = []
    if not str(packet.get("requester_instance_id") or "").strip():
        errors.append("requester_instance_id is required")
    if packet.get("requires_docker_sock") is True:
        errors.append("requires_docker_sock is forbidden for guest registration")
    if packet.get("guest_executes") is True:
        errors.append("guest_executes must not be true — the host mediates the fleet")
    for forged in ("member_id", "host_token", "fleet_assigned"):
        if forged in packet:
            errors.append(f"{forged} is host-minted and must not be self-asserted by the guest")
    return errors


@dataclass
class _Member:
    member_id: str
    fleet_id: str
    instance_id: str
    capabilities: list[str]
    nonce: str
    registered_at: float
    last_heartbeat: float
    status: str = "active"
    inbox: list[dict] = field(default_factory=list)


class FleetRegistryStore:
    """Host-side registry. All mutating ops are the HOST acting on a validated guest proposal."""

    def __init__(self, host_secret: str, *, now: Callable[[], float] | None = None,
                 stale_ttl: float = _DEFAULT_STALE_TTL) -> None:
        if not host_secret:
            raise ValueError("host_secret is required (operator-provisioned at deploy)")
        self._secret = host_secret
        self._now = now or (lambda: __import__("time").time())
        self._stale_ttl = stale_ttl
        self._members: dict[str, _Member] = {}
        # cross-fleet allow-list: set of (from_fleet, to_fleet) edges an operator explicitly grants
        self._cross_fleet: set[tuple[str, str]] = set()

    # -- registration + identity ----------------------------------------------------------
    def register(self, packet: dict[str, Any]) -> dict[str, Any]:
        """Host mints identity for a validated guest proposal. Returns the container's credential
        (member_id + token). Deterministic member_id (requester+fleet) doubles as an idempotency key:
        re-registering the same requester+fleet returns a FRESH token but the same member_id."""
        errors = validate_registration(packet)
        if errors:
            raise ValueError("; ".join(errors))
        requester = _safe_component(packet.get("requester_instance_id")) or "unknown"
        fleet_id = _safe_component(packet.get("fleet_id_requested") or "default") or "default"
        member_id = _safe_component(f"{requester}--{fleet_id}") or "member"
        nonce = os.urandom(16).hex()  # host-held; rotates on every (re)registration
        t = self._now()
        caps = [str(c) for c in (packet.get("capabilities") or [])]
        self._members[member_id] = _Member(
            member_id=member_id, fleet_id=fleet_id, instance_id=str(packet.get("requester_instance_id")),
            capabilities=caps, nonce=nonce, registered_at=t, last_heartbeat=t, status="active",
            inbox=self._members[member_id].inbox if member_id in self._members else [])
        token = mint_identity(member_id, fleet_id, nonce, self._secret)
        return {"member_id": member_id, "fleet_id": fleet_id, "token": token}

    def _authed(self, member_id: str, token: str) -> _Member:
        m = self._members.get(member_id)
        if m is None:
            raise KeyError(f"no such member {member_id!r}")
        if not verify_token(m.member_id, m.fleet_id, m.nonce, self._secret, token):
            raise PermissionError("identity token verification failed")
        return m

    def heartbeat(self, member_id: str, token: str) -> None:
        m = self._authed(member_id, token)
        m.last_heartbeat = self._now()
        if m.status == "stale":
            m.status = "active"

    def mark_stale(self) -> list[str]:
        """Label (never delete) members past the heartbeat TTL. Honest liveness — a dead container is
        never reported active."""
        cutoff = self._now() - self._stale_ttl
        stale = []
        for m in self._members.values():
            if m.status == "active" and m.last_heartbeat < cutoff:
                m.status = "stale"
                stale.append(m.member_id)
        return stale

    # -- discovery (fleet-scoped) ---------------------------------------------------------
    def members(self, member_id: str, token: str) -> list[dict[str, Any]]:
        me = self._authed(member_id, token)
        return [{"member_id": m.member_id, "capabilities": m.capabilities, "status": m.status,
                 "last_heartbeat": m.last_heartbeat}
                for m in self._members.values() if m.fleet_id == me.fleet_id]

    def grant_cross_fleet(self, from_fleet: str, to_fleet: str) -> None:
        """Operator/CI-only: allow-list a directed cross-fleet messaging edge (default is deny)."""
        self._cross_fleet.add((from_fleet, to_fleet))

    # -- host-brokered comms --------------------------------------------------------------
    def send(self, from_member: str, token: str, to_member: str, payload: Any) -> dict[str, Any]:
        sender = self._authed(from_member, token)
        recipient = self._members.get(to_member)
        if recipient is None:
            raise KeyError(f"no such recipient {to_member!r}")
        if sender.fleet_id != recipient.fleet_id and \
                (sender.fleet_id, recipient.fleet_id) not in self._cross_fleet:
            raise PermissionError("cross-fleet messaging not allow-listed")
        body = str(payload)[:_MAX_PAYLOAD]  # bounded, untrusted
        recipient.inbox.append({"from": sender.member_id, "payload": body, "at": self._now()})
        return {"delivered": True, "to": to_member}

    def inbox(self, member_id: str, token: str) -> list[dict[str, Any]]:
        m = self._authed(member_id, token)
        drained, m.inbox = m.inbox, []
        return drained
