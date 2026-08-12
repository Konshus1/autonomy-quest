"""Bounded role scheduler + session health/cleanup (design §6 steps 1-4+8, §10 Phase 4).

The scheduler stands up ONE session per declared role with:

  * BOUNDED CONCURRENCY — never more than ``fanout_cap`` live sessions. A request to exceed the cap
    is refused (no unbounded spawn / fork-bomb).
  * SCOPED CREDENTIALS — each session gets a per-role principal from ``RolePrincipalRegistry``,
    scoped to this run's channels; a coder can never hold the reviewer/parent/host/operator token.
  * DURABLE-FIRST DISPATCH — a handoff is written to the durable mailbox BEFORE the wake nudge.
  * HEALTH + CLEANUP — session health is tracked; ``teardown`` revokes every credential, closes the
    bus, and releases delivery so NO orphan session/pane/credential survives a run.

It carries NO host / docker / replication capability. It moves conversation turns between role
inboxes and nothing else.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .mailbox_bus import MailboxBus, Message
from .principals import RolePrincipal, RolePrincipalRegistry
from .wake_delivery import WakeDelivery


class SchedulerError(Exception):
    """Base for a scheduler rejection (cap exceeded, unknown session, etc.)."""


class FanoutExceeded(SchedulerError):
    """A request would push live sessions past the bounded fan-out cap."""


@dataclass
class _Session:
    role: str
    token: str
    principal: RolePrincipal
    cursor: int = 0
    healthy: bool = True


@dataclass
class RoleScheduler:
    """Owns the run's principals, mailbox, delivery, and bounded session set."""

    instance_id: str
    run_id: str
    delivery: WakeDelivery
    fanout_cap: int
    session_ttl_s: int = 900
    db_path: str | None = None
    _registry: RolePrincipalRegistry = field(init=False)
    _bus: MailboxBus = field(init=False)
    _sessions: dict[str, _Session] = field(init=False, default_factory=dict)
    _torn_down: bool = field(init=False, default=False)
    _tmpdir: tempfile.TemporaryDirectory | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        self._registry = RolePrincipalRegistry(
            instance_id=self.instance_id, run_id=self.run_id, session_ttl_s=self.session_ttl_s)
        if self.db_path is None:
            self._tmpdir = tempfile.TemporaryDirectory(prefix="aq-comms-run-")
            self.db_path = str(Path(self._tmpdir.name) / "mailbox.sqlite3")
        self._bus = MailboxBus(self.db_path)

    # -- lifecycle -----------------------------------------------------------
    def start_role(self, role: str) -> str:
        """Mint a scoped session for ``role`` and register it for wake. Returns its credential token.

        Enforces the BOUNDED fan-out cap: the (cap+1)-th live session is refused.
        """
        if self._torn_down:
            raise SchedulerError("scheduler already torn down")
        live = sum(1 for s in self._sessions.values() if s.healthy)
        if live >= self.fanout_cap:
            raise FanoutExceeded(
                f"fan-out cap {self.fanout_cap} reached; refusing to spawn role {role!r} "
                f"(bounded concurrency — no unbounded spawn)")
        token, principal = self._registry.mint(role)
        self._sessions[role] = _Session(role=role, token=token, principal=principal)
        self.delivery.register(principal.session_id)
        return token

    def token_for(self, role: str) -> str:
        return self._sessions[role].token

    # -- messaging (durable-first) ------------------------------------------
    def dispatch(self, *, sender_token: str, to_role: str, kind: str, payload: dict) -> int:
        """Authenticate the sender from its token, then durably publish to ``to_role``'s inbox.

        Identity is SERVER-DERIVED: the sender is whoever the token maps to, never a role named in
        ``payload``. The message is committed to the durable mailbox BEFORE the wake nudge, so a lost
        wake never loses it. Returns the durable seq.
        """
        sender = self._registry.verify(sender_token)          # raises if forged/expired/revoked
        channel = RolePrincipal.channel_for(self.run_id, to_role)
        self._registry.authorize_channel(sender, channel)     # raises if out of scope
        seq = self._bus.publish(
            channel=channel, kind=kind, sender_role=sender.role, payload=payload)  # DURABLE first
        # Best-effort wake AFTER the durable commit.
        target = self._sessions.get(to_role)
        if target is not None:
            self.delivery.wake(target.principal.session_id)
        return seq

    def receive(self, *, role_token: str) -> list[Message]:
        """Poll the caller's OWN inbox from its stored cursor (cursor-poll recovery)."""
        principal = self._registry.verify(role_token)
        session = self._sessions[principal.role]
        msgs, session.cursor = self._bus.poll(
            channel=principal.inbox_channel(), cursor=session.cursor)
        return msgs

    # -- health / cleanup ----------------------------------------------------
    def mark_unhealthy(self, role: str) -> None:
        if role in self._sessions:
            self._sessions[role].healthy = False

    def health(self) -> dict[str, bool]:
        return {role: s.healthy for role, s in self._sessions.items()}

    def teardown(self) -> dict[str, int]:
        """Revoke all credentials, close the bus, release delivery. Idempotent; leaves no orphans."""
        if self._torn_down:
            return {"revoked": 0, "sessions": 0}
        revoked = self._registry.revoke_all()
        for s in self._sessions.values():
            self.delivery.teardown(s.principal.session_id)
        self._bus.close()
        if self._tmpdir is not None:
            self._tmpdir.cleanup()
        n = len(self._sessions)
        self._torn_down = True
        return {"revoked": revoked, "sessions": n}

    def __enter__(self) -> "RoleScheduler":
        return self

    def __exit__(self, *exc) -> None:
        self.teardown()
