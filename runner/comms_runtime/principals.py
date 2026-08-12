"""Scoped per-role principals for the intra-instance runtime (design §8.2).

Every role agent gets its OWN principal — ``instance:<id>/agent:<role>/session:<sid>`` — scoped to
its run's channels. Identity is SERVER-DERIVED from a minted credential token, never from anything a
role puts in a message body. So:

  * a coder CANNOT impersonate the reviewer, parent, host, or operator: it never holds their token,
    and a forged/absent token authenticates as nobody;
  * a role can address only its OWN run's role channels (``run:<rid>/role:<r>/inbox``) — the
    parent / host / operator channels are not in any role principal's scope at all;
  * SESSION CREDENTIALS EXPIRE (``expires_at``); teardown revokes them regardless.

This mirrors ``management/api/comms_auth.py`` (the network principal model) but for the strictly
intra-instance bus. It is deliberately separate: a role principal is minted locally per run and
never leaves the instance.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field


class RolePrincipalError(Exception):
    """Base for a role-principal authN/authZ failure."""


class Unauthenticated(RolePrincipalError):
    """Absent / unrecognized / expired / revoked credential."""


class Forbidden(RolePrincipalError):
    """Authenticated, but not permitted to touch this channel (impersonation / out-of-scope)."""


@dataclass(frozen=True)
class RolePrincipal:
    """A server-derived intra-instance identity. Nothing here comes from a message body."""

    instance_id: str
    run_id: str
    role: str
    session_id: str

    @property
    def principal_id(self) -> str:
        return f"instance:{self.instance_id}/agent:{self.role}/session:{self.session_id}"

    def inbox_channel(self) -> str:
        return f"run:{self.run_id}/role:{self.role}/inbox"

    @staticmethod
    def channel_for(run_id: str, role: str) -> str:
        return f"run:{run_id}/role:{role}/inbox"


@dataclass
class _Entry:
    principal: RolePrincipal
    expires_at: float
    revoked: bool = False


@dataclass
class RolePrincipalRegistry:
    """Mints, verifies, and expires per-role session credentials for ONE run.

    A credential is an opaque random token bound server-side to a ``RolePrincipal``. The registry is
    the sole authority on identity: given a token it returns the bound principal (or refuses). There
    is no way to name a role in a payload and be treated as that role.
    """

    instance_id: str
    run_id: str
    session_ttl_s: int = 900
    _by_token: dict[str, _Entry] = field(default_factory=dict)
    _now: "callable" = time.time  # injectable clock for expiry tests

    def mint(self, role: str) -> tuple[str, RolePrincipal]:
        """Mint a fresh scoped credential for ``role``. Returns ``(token, principal)``."""
        session_id = secrets.token_hex(8)
        principal = RolePrincipal(
            instance_id=self.instance_id, run_id=self.run_id, role=role, session_id=session_id)
        token = secrets.token_urlsafe(24)
        self._by_token[token] = _Entry(
            principal=principal, expires_at=self._now() + self.session_ttl_s)
        return token, principal

    def verify(self, token: str) -> RolePrincipal:
        """Return the bound principal for a live token, else raise ``Unauthenticated``.

        A token that is unknown, revoked, or past its TTL authenticates as NOBODY — the caller can
        neither read nor write any channel. Expiry is checked on every use.
        """
        entry = self._by_token.get(token or "")
        if entry is None:
            raise Unauthenticated("role credential not recognized")
        if entry.revoked:
            raise Unauthenticated(f"role credential for {entry.principal.role!r} was revoked")
        if self._now() >= entry.expires_at:
            raise Unauthenticated(
                f"role credential for {entry.principal.role!r} expired "
                f"(session credentials are time-bounded)")
        return entry.principal

    def authorize_channel(self, principal: RolePrincipal, channel: str) -> None:
        """Raise ``Forbidden`` unless ``principal`` may address ``channel``.

        A role principal may address ONLY the role-inbox channels of ITS OWN run
        (``run:<this run>/role:<any declared role>/inbox``) — that is how planner hands to coder and
        coder hands to reviewer. It may NOT address another run's channels, and there is simply no
        parent/host/operator channel in this namespace for it to reach. This is the structural
        no-impersonation / no-privilege-escalation boundary.
        """
        prefix = f"run:{principal.run_id}/role:"
        if not channel.startswith(prefix) or not channel.endswith("/inbox"):
            raise Forbidden(
                f"principal {principal.principal_id!r} may not address channel {channel!r}: "
                f"role principals are scoped to their own run's role inboxes only")

    def revoke_all(self) -> int:
        """Revoke every credential (teardown). Returns how many were live."""
        live = 0
        for entry in self._by_token.values():
            if not entry.revoked:
                entry.revoked = True
                live += 1
        return live
