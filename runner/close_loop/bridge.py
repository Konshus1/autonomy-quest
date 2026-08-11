"""Strict queue bridge boundaries for observation, materialization, and dispatch.

No mode is a feature flag for another mode.  Each public mutator checks its exact
mode, and the reference ledger makes the permitted write set inspectable in tests.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping

from .hashing import SourceHashes
from .lease import LeaseAuthorityError, LeaseGrant, LeaseStore, _Selector

LAUNCH_READY_STATUS = "launch_ready_bounded_dev_safe_slice"
LAUNCH_READY_STATUSES = frozenset({LAUNCH_READY_STATUS})


class BridgeMode(str, Enum):
    OBSERVE = "observe"
    MATERIALIZE = "materialize"
    DISPATCH = "dispatch"

    @classmethod
    def parse(cls, value: "BridgeMode | str") -> "BridgeMode":
        """Parse only the three literal wire values.

        Configuration at an authority boundary must not silently trim, fold case, or
        coerce arbitrary objects.  A typo therefore fails at construction rather than
        broadening the bridge's write set.
        """
        if isinstance(value, cls):
            return value
        if type(value) is not str or value not in {member.value for member in cls}:
            raise ValueError(f"invalid bridge mode: {value!r}")
        return cls(value)


class BridgeModeError(PermissionError):
    """A caller attempted a mutation outside the bridge's exact configured mode."""


class BridgeRefusal(RuntimeError):
    """Fail-closed bridge refusal with a stable reason code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.reason_code = reason


@dataclass(frozen=True)
class SourceSnapshot:
    """One internally consistent read of task and Ralph readiness state.

    ``status`` is deliberately exact rather than a prefix match, ``readiness`` must
    be the boolean singleton ``True``, and cancellation wins over both.  If an
    admitted hash is supplied it must agree with the freshly computed intent hash.
    """

    source_system: str
    source_task_id: str
    status: str
    readiness: bool
    cancel: bool
    hashes: SourceHashes
    admitted_intent_hash: str | None = None
    mission_hash: str | None = None
    admitted_mission_hash: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.source_system, str) or not self.source_system.strip():
            raise ValueError("source_system is required")
        if not str(self.source_task_id).strip():
            raise ValueError("source_task_id is required")
        if not isinstance(self.status, str):
            raise TypeError("status must be a string")
        if not isinstance(self.hashes, SourceHashes):
            raise TypeError("hashes must be SourceHashes")

    @property
    def intent_hash(self) -> str:
        return self.hashes.intent_hash

    @property
    def observation_hash(self) -> str:
        return self.hashes.observation_hash

    @property
    def ready_for_worker_launch(self) -> bool:
        return self.readiness

    @property
    def cancel_requested(self) -> bool:
        return self.cancel

    def launch_eligibility(self) -> tuple[bool, str | None]:
        if self.cancel is True:
            return False, "source_cancelled"
        if self.cancel is not False:
            return False, "cancel_state_not_boolean"
        if self.status not in LAUNCH_READY_STATUSES:
            return False, "status_not_exactly_launch_ready"
        if self.readiness is not True:
            return False, "ready_for_worker_launch_not_true"
        if (self.admitted_intent_hash is not None
                and self.admitted_intent_hash != self.intent_hash):
            return False, "stale_intent_hash_disagreement"
        if (self.admitted_mission_hash is not None
                and self.admitted_mission_hash != self.mission_hash):
            return False, "mission_boundary_changed"
        return True, None

    @property
    def launch_ready(self) -> bool:
        return self.launch_eligibility()[0]


@dataclass(frozen=True)
class BridgeObservation:
    source_system: str
    source_task_id: str
    status: str
    readiness: bool
    cancel: bool
    intent_hash: str
    observation_hash: str


@dataclass(frozen=True)
class WorkerReviewerWork:
    id: int
    summary: str
    rationale: str
    execution_path: str = "worker_reviewer"
    status: str = "pending"


@dataclass(frozen=True)
class TaskWorkLink:
    source_system: str
    source_task_id: str
    source_intent_hash: str
    source_observation_hash: str
    mission_hash: str | None
    work_id: int
    lease_token: str
    lease_generation: int
    state: str = "materialized"


@dataclass(frozen=True)
class Materialization:
    lease: LeaseGrant
    link: TaskWorkLink
    work: WorkerReviewerWork
    capability: "DispatchCapability"


@dataclass(frozen=True)
class DispatchCapability:
    """Fenced authority minted from a successful materialization."""

    grant: LeaseGrant
    work_id: int
    _seal: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class DispatchReceipt:
    work_id: int
    owner_system: str
    lease_generation: int
    result: Any = None


class InMemoryBridgeLedger:
    """Inspectable atomic ledger used by the bridge contract tests.

    It intentionally has no session, worktree, branch, run, or candidate mutator.
    Materialize mode can create only a link and a ``worker_reviewer`` work row.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.observations: list[BridgeObservation] = []
        self.links: dict[tuple[str, str], TaskWorkLink] = {}
        self.works: dict[int, WorkerReviewerWork] = {}
        self.dispatches: list[DispatchReceipt] = []
        self._next_work_id = 1

        # Explicit empty surfaces make negative side-effect assertions unambiguous.
        self.sessions: list[Any] = []
        self.worktrees: list[Any] = []
        self.branches: list[Any] = []
        self.runs: list[Any] = []
        self.candidates: list[Any] = []

    @staticmethod
    def _require(actual: BridgeMode, expected: BridgeMode) -> None:
        if actual is not expected:
            raise BridgeModeError(f"{expected.value} mutation forbidden in {actual.value} mode")

    def record_observation(self, mode: BridgeMode,
                           snapshot: SourceSnapshot) -> BridgeObservation:
        self._require(mode, BridgeMode.OBSERVE)
        row = BridgeObservation(
            snapshot.source_system, str(snapshot.source_task_id), snapshot.status,
            snapshot.readiness, snapshot.cancel, snapshot.intent_hash,
            snapshot.observation_hash,
        )
        with self._lock:
            self.observations.append(row)
        return row

    def materialize(self, mode: BridgeMode, snapshot: SourceSnapshot,
                    grant: LeaseGrant) -> tuple[TaskWorkLink, WorkerReviewerWork]:
        self._require(mode, BridgeMode.MATERIALIZE)
        if not grant.acquired or not grant.token or grant.generation is None:
            raise LeaseAuthorityError("materialization requires an acquired fenced lease")
        key = (snapshot.source_system, str(snapshot.source_task_id))
        with self._lock:
            existing = self.links.get(key)
            if existing is not None:
                if existing.source_intent_hash != snapshot.intent_hash:
                    raise BridgeRefusal("source_intent_changed_after_materialization")
                return existing, self.works[existing.work_id]
            work_id = self._next_work_id
            work = WorkerReviewerWork(
                work_id,
                summary=str(snapshot.payload.get("title") or f"Imported task {snapshot.source_task_id}"),
                rationale="Materialized from an exact launch-ready shared queue snapshot",
            )
            link = TaskWorkLink(
                snapshot.source_system, str(snapshot.source_task_id),
                snapshot.intent_hash, snapshot.observation_hash, snapshot.mission_hash, work_id,
                grant.token, grant.generation,
            )
            # Both rows become visible under the same lock: never link-only or work-only.
            self.works[work_id] = work
            self.links[key] = link
            self._next_work_id += 1
            return link, work

    def record_dispatch(self, mode: BridgeMode,
                        receipt: DispatchReceipt) -> DispatchReceipt:
        self._require(mode, BridgeMode.DISPATCH)
        with self._lock:
            self.dispatches.append(receipt)
        return receipt


class QueueBridge:
    """One exact-mode bridge endpoint.

    Construct separate instances for observe, materialize, and dispatch.  Sharing the
    ledger and lease store is expected; sharing a permissive bridge instance is not.
    """

    def __init__(self, mode: BridgeMode | str, *, ledger: InMemoryBridgeLedger,
                 lease_store: LeaseStore, selector: _Selector | None = None,
                 dispatcher: Callable[[int], Any] | None = None) -> None:
        self.mode = BridgeMode.parse(mode)
        self.ledger = ledger
        self.lease_store = lease_store
        self.selector = selector
        self.dispatcher = dispatcher
        self._seal = object()

    def _require(self, expected: BridgeMode) -> None:
        if self.mode is not expected:
            raise BridgeModeError(f"{expected.value} operation forbidden in {self.mode.value} mode")

    def observe(self, snapshot: SourceSnapshot) -> BridgeObservation:
        self._require(BridgeMode.OBSERVE)
        return self.ledger.record_observation(self.mode, snapshot)

    def materialize(self, snapshot: SourceSnapshot) -> Materialization:
        self._require(BridgeMode.MATERIALIZE)
        if self.selector is None:
            raise BridgeRefusal("materialize_selector_missing")
        grant = self.selector.try_claim(snapshot)
        if not grant.acquired:
            raise BridgeRefusal(grant.reason)
        # Reassert immediately before materializing, rejecting expired/fenced grants.
        self.lease_store.assert_authority(grant)
        link, work = self.ledger.materialize(self.mode, snapshot, grant)
        return Materialization(grant, link, work,
                               DispatchCapability(grant, work.id, self._seal))

    def dispatch(self, capability: DispatchCapability) -> DispatchReceipt:
        self._require(BridgeMode.DISPATCH)
        if not isinstance(capability, DispatchCapability):
            raise LeaseAuthorityError("dispatch capability is required")

        # A valid lease alone does not authorize an arbitrary work id.  Bind the
        # capability back to the atomically materialized link before invoking code
        # supplied by the runtime.  This also prevents callers from constructing a
        # look-alike DispatchCapability around somebody else's live grant.
        grant = capability.grant
        key = (grant.source_system, str(grant.source_task_id))
        link = self.ledger.links.get(key)
        if not (
            link
            and link.work_id == capability.work_id
            and link.source_intent_hash == grant.source_intent_hash
            and link.lease_token == grant.token
            and link.lease_generation == grant.generation
            and capability.work_id in self.ledger.works
            and self.ledger.works[capability.work_id].execution_path == "worker_reviewer"
        ):
            raise LeaseAuthorityError("dispatch capability is not bound to materialized work")

        self.lease_store.assert_authority(grant)
        result = self.dispatcher(capability.work_id) if self.dispatcher else None
        receipt = DispatchReceipt(
            capability.work_id, str(grant.owner_system), int(grant.generation), result,
        )
        return self.ledger.record_dispatch(self.mode, receipt)

    def tick(self, *, snapshot: SourceSnapshot | None = None,
             capability: DispatchCapability | None = None) -> Any:
        """Perform exactly the one operation selected by this bridge's mode.

        ``tick`` is intentionally a thin router over the public methods, so direct
        calls and scheduler calls pass through identical mode and fencing interlocks.
        """
        if self.mode is BridgeMode.OBSERVE:
            if snapshot is None or capability is not None:
                raise TypeError("observe tick requires snapshot only")
            return self.observe(snapshot)
        if self.mode is BridgeMode.MATERIALIZE:
            if snapshot is None or capability is not None:
                raise TypeError("materialize tick requires snapshot only")
            return self.materialize(snapshot)
        if capability is None or snapshot is not None:
            raise TypeError("dispatch tick requires capability only")
        return self.dispatch(capability)


# The shorter public name is the scheduler-facing contract.  QueueBridge remains
# available because it describes the adapter's role more explicitly.
Bridge = QueueBridge
