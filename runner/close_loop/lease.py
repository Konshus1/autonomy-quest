"""Shared, fenced dispatch leases used by both AQ and Ralph selectors.

The in-memory store is a reference implementation of the database protocol.  It is
thread safe, uses opaque tokens, and never reuses a fencing generation for a source
identity, including after release.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Protocol


class LeaseDecision(str, Enum):
    ACQUIRED = "acquired"
    HELD = "held"
    INELIGIBLE = "ineligible"


@dataclass(frozen=True)
class LeaseRequest:
    source_system: str
    source_task_id: str
    owner_system: str
    owner_instance: str
    source_intent_hash: str
    ttl_seconds: int = 300
    eligible: bool = True
    ineligible_reason: str | None = None

    def __post_init__(self) -> None:
        values = (self.source_system, self.source_task_id, self.owner_system,
                  self.owner_instance, self.source_intent_hash)
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError("lease identity and intent hash must be non-empty strings")
        if isinstance(self.ttl_seconds, bool) or not 1 <= int(self.ttl_seconds) <= 3600:
            raise ValueError("lease ttl_seconds must be between 1 and 3600")


@dataclass(frozen=True)
class LeaseGrant:
    decision: LeaseDecision
    reason: str
    source_system: str
    source_task_id: str
    owner_system: str | None = None
    owner_instance: str | None = None
    token: str | None = None
    generation: int | None = None
    lease_until: datetime | None = None
    source_intent_hash: str | None = None

    @property
    def acquired(self) -> bool:
        return self.decision is LeaseDecision.ACQUIRED


class LeaseStore(Protocol):
    def try_claim(self, request: LeaseRequest) -> LeaseGrant: ...
    def assert_authority(self, grant: LeaseGrant) -> None: ...
    def release(self, grant: LeaseGrant) -> bool: ...


class LeaseAuthorityError(RuntimeError):
    """A missing, expired, released, or fenced lease attempted a side effect."""


class SharedLeaseStore:
    """Thread-safe shared store with opaque-token and monotonic-generation fencing."""

    def __init__(self, *, clock=None, token_factory=None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._token_factory = token_factory or (lambda: str(uuid.uuid4()))
        self._lock = threading.RLock()
        self._rows: dict[tuple[str, str], LeaseGrant] = {}
        self._generations: dict[tuple[str, str], int] = {}

    def try_claim(self, request: LeaseRequest) -> LeaseGrant:
        now = self._clock()
        key = (request.source_system, request.source_task_id)
        if not request.eligible:
            # Cancellation/readiness is authority, not selection advice.  Once either
            # real selector observes a failing gate, an older capability must stop
            # dispatching.  Removing the row fences it while the generation counter is
            # retained, so a later re-admission cannot revive the old token.
            with self._lock:
                self._rows.pop(key, None)
            return LeaseGrant(LeaseDecision.INELIGIBLE,
                              request.ineligible_reason or "source_ineligible",
                              request.source_system, request.source_task_id)
        with self._lock:
            current = self._rows.get(key)
            if current is not None and current.lease_until is not None and current.lease_until > now:
                same_claimant = (
                    current.owner_system == request.owner_system
                    and current.owner_instance == request.owner_instance
                    and current.source_intent_hash == request.source_intent_hash
                )
                if same_claimant:
                    return current
                return LeaseGrant(
                    LeaseDecision.HELD, "lease_held", request.source_system,
                    request.source_task_id, current.owner_system, current.owner_instance,
                    None, current.generation, current.lease_until,
                    current.source_intent_hash,
                )
            generation = self._generations.get(key, 0) + 1
            self._generations[key] = generation
            grant = LeaseGrant(
                LeaseDecision.ACQUIRED,
                "lease_acquired" if current is None else "expired_lease_reclaimed",
                request.source_system, request.source_task_id, request.owner_system,
                request.owner_instance, self._token_factory(), generation,
                now + timedelta(seconds=int(request.ttl_seconds)),
                request.source_intent_hash,
            )
            self._rows[key] = grant
            return grant

    def assert_authority(self, grant: LeaseGrant) -> None:
        now = self._clock()
        key = (grant.source_system, grant.source_task_id)
        with self._lock:
            current = self._rows.get(key)
            valid = bool(
                grant.acquired and grant.token and current
                and current.token == grant.token
                and current.generation == grant.generation
                and current.owner_system == grant.owner_system
                and current.owner_instance == grant.owner_instance
                and current.source_intent_hash == grant.source_intent_hash
                and current.lease_until is not None and current.lease_until > now
            )
        if not valid:
            raise LeaseAuthorityError("dispatch lease is absent, expired, released, or fenced")

    def release(self, grant: LeaseGrant) -> bool:
        key = (grant.source_system, grant.source_task_id)
        with self._lock:
            current = self._rows.get(key)
            if (current is None or current.token != grant.token
                    or current.generation != grant.generation):
                return False
            del self._rows[key]
            return True

    def expire_for_test(self, grant: LeaseGrant) -> None:
        key = (grant.source_system, grant.source_task_id)
        with self._lock:
            current = self._rows[key]
            self._rows[key] = LeaseGrant(
                current.decision, current.reason, current.source_system,
                current.source_task_id, current.owner_system, current.owner_instance,
                current.token, current.generation,
                self._clock() - timedelta(seconds=1), current.source_intent_hash,
            )

    def count(self) -> int:
        with self._lock:
            return len(self._rows)


# Compatibility name retained for callers written before the shared-selector name
# became part of the public contract.
InMemoryLeaseStore = SharedLeaseStore


class _Selector:
    owner_system: str

    def __init__(self, store: LeaseStore, *, owner_instance: str,
                 ttl_seconds: int = 300) -> None:
        if not owner_instance.strip():
            raise ValueError("selector owner_instance is required")
        self.store = store
        self.owner_instance = owner_instance
        self.ttl_seconds = ttl_seconds

    def try_claim(self, snapshot: Any) -> LeaseGrant:
        eligible, reason = snapshot.launch_eligibility()
        return self.store.try_claim(LeaseRequest(
            source_system=snapshot.source_system,
            source_task_id=str(snapshot.source_task_id),
            owner_system=self.owner_system,
            owner_instance=self.owner_instance,
            source_intent_hash=snapshot.intent_hash,
            ttl_seconds=self.ttl_seconds,
            eligible=eligible,
            ineligible_reason=reason,
        ))

    # ``select`` is intentionally only an alias for the one shared claim path.
    select = try_claim


class AQSelector(_Selector):
    owner_system = "aq"


class RalphSelector(_Selector):
    owner_system = "ralph"


class PostgresLeaseStore:
    """DB-API adapter for checked functions in ``schema/025_close_loop.sql``."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    @staticmethod
    def _grant(row: Any, columns: list[str] | None = None) -> LeaseGrant:
        if row is None:
            raise RuntimeError("shared lease function returned no row")
        if isinstance(row, dict):
            data = row
        elif hasattr(row, "keys"):
            data = dict(row)
        elif columns:
            data = dict(zip(columns, row))
        else:
            raise RuntimeError("lease cursor did not expose result column names")
        return LeaseGrant(
            LeaseDecision(data["decision"]), str(data["reason_code"]),
            str(data["source_system"]), str(data["source_task_id"]),
            data.get("owner_system"), data.get("owner_instance"),
            str(data["lease_token"]) if data.get("lease_token") else None,
            int(data["generation"]) if data.get("generation") is not None else None,
            data.get("lease_until"), data.get("source_intent_hash"),
        )

    def try_claim(self, request: LeaseRequest) -> LeaseGrant:
        if not request.eligible:
            return LeaseGrant(LeaseDecision.INELIGIBLE,
                              request.ineligible_reason or "source_ineligible",
                              request.source_system, request.source_task_id)
        with self.connection.cursor() as cur:
            cur.execute("SELECT * FROM aq_try_claim_queue_dispatch_lease(%s,%s,%s,%s,%s,%s)",
                        (request.source_system, request.source_task_id,
                         request.owner_system, request.owner_instance,
                         request.source_intent_hash, request.ttl_seconds))
            row = cur.fetchone()
            columns = [x.name if hasattr(x, "name") else x[0] for x in cur.description]
        return self._grant(row, columns)

    def assert_authority(self, grant: LeaseGrant) -> None:
        if not grant.acquired or not grant.token or grant.generation is None:
            raise LeaseAuthorityError("dispatch requires an acquired lease")
        with self.connection.cursor() as cur:
            cur.execute("SELECT aq_assert_queue_dispatch_lease(%s,%s,%s,%s,%s,%s)",
                        (grant.source_system, grant.source_task_id,
                         grant.owner_system, grant.owner_instance,
                         grant.token, grant.generation))
            valid = bool(cur.fetchone()[0])
        if not valid:
            raise LeaseAuthorityError("dispatch lease is absent, expired, cancelled, or fenced")

    def release(self, grant: LeaseGrant) -> bool:
        if not grant.token or grant.generation is None:
            return False
        with self.connection.cursor() as cur:
            cur.execute("SELECT aq_release_queue_dispatch_lease(%s,%s,%s,%s,%s,%s)",
                        (grant.source_system, grant.source_task_id,
                         grant.owner_system, grant.owner_instance,
                         grant.token, grant.generation))
            return bool(cur.fetchone()[0])
