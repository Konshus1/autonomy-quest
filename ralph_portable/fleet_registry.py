"""Host-owned replica topology registry (#4834 comms Phase 0, design §5/§9.2).

DANGER — HOST-ONLY. This is the authoritative parent record of the fleet: the full per-replica
port map, lineage, workflow/SHA, lifecycle state, and comms-credential handle. It is written ONLY
by host lifecycle code (``host_replica_stack`` stand-up / teardown and the host daemon) and must
NEVER be importable from a guest-reachable module — a guest/replica cannot be allowed to write the
registry or inject an endpoint/port (that would defeat the whole host-authoritative topology). The
import firewall (``tests/test_import_firewall.py``) enforces this structurally: ``FleetRegistryStore``
and this module are in the forbidden set for the guest import closure.

Why it exists (design §2.1 gap): the daemon ledger keeps only project/instance_id/health_url and
DROPS the port map; the stand-up record + ``replica.json`` carry the ports but were never unified
into one host-owned record. This registry is that single source of truth. On host/broker restart,
``reconcile`` rebuilds it from Docker labels (``com.docker.compose.project=aq-replica-*``) + each
replica's ``replica.json`` so truth survives a restart.

Storage is a single JSON file with atomic (temp-file + ``os.replace``) writes under an advisory
host flock, mirroring the daemon ledger's durability discipline. It holds no secret values — only a
credential *handle* + hash + revocation state (design §8.2: secrets never rendered/stored in the clear
here).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

# Lifecycle states of a registry entry.
LIFECYCLE_STANDING_UP = "standing_up"
LIFECYCLE_LIVE = "live"
LIFECYCLE_TORN_DOWN = "torn_down"
LIFECYCLE_STATES = frozenset({LIFECYCLE_STANDING_UP, LIFECYCLE_LIVE, LIFECYCLE_TORN_DOWN})

# Host-observed health states (#4834 comms Phase 1, design §9.2). Distinct from the LIFECYCLE_*
# states: lifecycle is "does the host believe this stack exists" (stand-up/teardown truth); health
# is "what did the host's last /health poll observe" (live/stale/down). A torn-down stack has no
# health; a live stack may still read HEALTH_DOWN if its /health is unreachable.
HEALTH_LIVE = "live"      # last poll reached /health, ok + the expected git_sha.
HEALTH_STALE = "stale"    # reachable-but-degraded (wrong/absent sha), or a transient miss within threshold.
HEALTH_DOWN = "down"      # unreachable (refused/timeout) and no fresh successful poll within threshold.
HEALTH_STATES = frozenset({HEALTH_LIVE, HEALTH_STALE, HEALTH_DOWN})

_REGISTRY_FILENAME = "fleet-registry.json"
_PORT_KEYS = ("postgres", "governance", "app_ui", "app_mgmt")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def credential_fingerprint(secret: str | None) -> str | None:
    """A non-reversible handle for a credential (sha256 hex, truncated). Never the secret itself."""
    if not secret:
        return None
    return "sha256:" + hashlib.sha256(secret.encode("utf-8")).hexdigest()[:32]


@contextmanager
def _file_lock(lock_path: Path, *, timeout_s: float = 30.0, poll_s: float = 0.05) -> Iterator[None]:
    """Advisory host flock for the registry read-modify-write critical section.

    Released on fd close / process death (no stale lock). Bounded acquire so an automated caller
    never hangs on a wedged peer.
    """
    import fcntl

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"could not acquire fleet registry lock {lock_path}")
                time.sleep(poll_s)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _entry_from_standup(record: dict[str, Any], *, lifecycle: str) -> dict[str, Any]:
    """Normalize a host stand-up record (or a reconciled ``replica.json``) into a registry entry."""
    ports = record.get("ports") or {}
    return {
        "instance_id": record.get("instance_id"),
        "project": record.get("project"),
        "parent_instance_id": record.get("requester_instance_id"),
        "requester_instance_id": record.get("requester_instance_id"),
        "mission_id": record.get("mission_id") or record.get("mission"),
        "ports": {k: ports.get(k) for k in _PORT_KEYS},
        "health_url": record.get("health_url"),
        "workflow": record.get("workflow") or record.get("workflow_selector"),
        "workflow_version": record.get("workflow_version"),
        "git_sha": record.get("git_sha"),
        "lifecycle_state": lifecycle,
        "credential_fingerprint": record.get("credential_fingerprint"),
        "credential_revoked": bool(record.get("credential_revoked", False)),
        "created_at": record.get("created_at") or _now_iso(),
        "updated_at": _now_iso(),
        "state_dir": record.get("state_dir"),
    }


class FleetRegistryStore:
    """The host-owned topology registry, backed by an atomic JSON file keyed by instance_id."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    # -- low-level durable read/write ----------------------------------------
    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
        os.replace(tmp, self.path)  # atomic swap: a crash never leaves a half-written registry

    # -- reads ----------------------------------------------------------------
    def all(self) -> list[dict[str, Any]]:
        return sorted(self._read().values(), key=lambda e: e.get("created_at") or "")

    def get(self, instance_id: str) -> dict[str, Any] | None:
        return self._read().get(instance_id)

    def live(self) -> list[dict[str, Any]]:
        return [e for e in self.all() if e.get("lifecycle_state") != LIFECYCLE_TORN_DOWN]

    # -- transactional writes (host lifecycle only) ---------------------------
    def upsert_standup(
        self, record: dict[str, Any], *, lifecycle: str = LIFECYCLE_LIVE,
        credential_secret: str | None = None,
    ) -> dict[str, Any]:
        """Record (or update) a replica from a host stand-up outcome, transactionally.

        Called by the host after a stand-up ``executed``/``host_executing`` result. ``lifecycle``
        is ``live`` once the stack passed its health assertion, ``standing_up`` while in progress.
        The credential is stored only as a fingerprint + never-in-the-clear.
        """
        if lifecycle not in LIFECYCLE_STATES:
            raise ValueError(f"invalid lifecycle {lifecycle!r}")
        instance_id = record.get("instance_id")
        if not instance_id:
            raise ValueError("cannot register a replica without an instance_id")
        with _file_lock(self.lock_path):
            data = self._read()
            entry = _entry_from_standup(record, lifecycle=lifecycle)
            if credential_secret is not None:
                entry["credential_fingerprint"] = credential_fingerprint(credential_secret)
            existing = data.get(instance_id)
            if existing:
                # Preserve the original created_at across updates.
                entry["created_at"] = existing.get("created_at") or entry["created_at"]
                if entry.get("credential_fingerprint") is None:
                    entry["credential_fingerprint"] = existing.get("credential_fingerprint")
            data[instance_id] = entry
            self._write(data)
            return entry

    def mark_torn_down(self, instance_id: str, *, revoke_credential: bool = True) -> dict[str, Any] | None:
        """Mark a replica torn down and (by default) revoke its comms credential (design §8.2).

        Returns the updated entry, or ``None`` if the instance was never registered.
        """
        with _file_lock(self.lock_path):
            data = self._read()
            entry = data.get(instance_id)
            if entry is None:
                return None
            entry["lifecycle_state"] = LIFECYCLE_TORN_DOWN
            entry["updated_at"] = _now_iso()
            if revoke_credential:
                entry["credential_revoked"] = True
            data[instance_id] = entry
            self._write(data)
            return entry

    def mark_torn_down_by_project(self, project: str, **kwargs: Any) -> list[dict[str, Any]]:
        """Teardown addresses replicas by compose project; map it to the instance ids we hold."""
        updated: list[dict[str, Any]] = []
        for entry in self.all():
            if entry.get("project") == project and entry.get("lifecycle_state") != LIFECYCLE_TORN_DOWN:
                got = self.mark_torn_down(entry["instance_id"], **kwargs)
                if got is not None:
                    updated.append(got)
        return updated

    def credential_revoked(self, instance_id: str) -> bool:
        entry = self.get(instance_id)
        return entry is None or bool(entry.get("credential_revoked"))

    # -- host-observed health (Phase 1 poller only) ---------------------------
    def last_successful_poll(self, instance_id: str) -> str | None:
        """The ISO time of the most recent HEALTH_LIVE observation, or None if never seen live.

        The Phase-1 poller reads this to classify the CURRENT poll (a transient miss within the
        stale threshold is ``stale``; a miss with no recent success is ``down``)."""
        entry = self.get(instance_id)
        health = (entry or {}).get("health") or {}
        return health.get("last_successful_poll")

    def record_observed_health(
        self, instance_id: str, *, state: str, observed_at: str, reason: str = "",
        reachable: bool = False, observed_git_sha: str | None = None,
        cycling: bool | None = None,
    ) -> dict[str, Any] | None:
        """Stamp the host's latest ``/health`` observation onto the entry (design §9.2).

        HOST-ONLY ground truth: this is written solely by the Phase-1 fleet poller (a host process
        outside the guest import closure), NEVER by a replica. ``last_successful_poll`` advances only
        on a ``live`` observation, so it is a monotonic watermark of "last time the host saw this
        replica actually healthy" that a transient miss does not reset. Returns the updated entry, or
        ``None`` if the instance is unregistered (the poller only ever polls registered live entries).
        """
        if state not in HEALTH_STATES:
            raise ValueError(f"invalid health state {state!r}")
        with _file_lock(self.lock_path):
            data = self._read()
            entry = data.get(instance_id)
            if entry is None:
                return None
            prior = entry.get("health") or {}
            last_ok = prior.get("last_successful_poll")
            if state == HEALTH_LIVE:
                last_ok = observed_at  # advance the healthy-watermark only on a live observation
            entry["health"] = {
                "state": state,
                "last_poll_at": observed_at,
                "last_successful_poll": last_ok,
                "reachable": bool(reachable),
                "observed_git_sha": observed_git_sha,
                "cycling": cycling,
                "reason": reason,
                "poll_count": int(prior.get("poll_count") or 0) + 1,
            }
            entry["updated_at"] = _now_iso()
            data[instance_id] = entry
            self._write(data)
            return entry

    # -- host outbox relay cursor (Phase 2 relay only) ------------------------
    def relay_cursor(self, instance_id: str) -> int:
        """The monotonic outbox watermark the host relay has already copied for this instance.

        HOST-ONLY: written solely by the Phase-2 outbox relay (a host process outside the guest import
        closure), NEVER by a replica. It lets a restarted relay resume from where it left off instead
        of re-pulling the whole outbox (idempotency-key/global-id dedup still makes a re-pull safe)."""
        entry = self.get(instance_id)
        relay = (entry or {}).get("relay") or {}
        try:
            return int(relay.get("cursor") or 0)
        except (TypeError, ValueError):
            return 0

    def record_relay_cursor(self, instance_id: str, *, cursor: int, relayed_at: str,
                            relayed_count: int = 0) -> dict[str, Any] | None:
        """Advance this instance's relay watermark after a batch is copied to the parent journal.

        Monotonic: the cursor never moves backward (a stale/racing batch cannot rewind it). Returns
        the updated entry, or ``None`` if the instance is unregistered."""
        with _file_lock(self.lock_path):
            data = self._read()
            entry = data.get(instance_id)
            if entry is None:
                return None
            prior = entry.get("relay") or {}
            prev_cursor = int(prior.get("cursor") or 0)
            new_cursor = max(prev_cursor, int(cursor))
            entry["relay"] = {
                "cursor": new_cursor,
                "last_relay_at": relayed_at,
                "relayed_total": int(prior.get("relayed_total") or 0) + int(relayed_count),
            }
            entry["updated_at"] = _now_iso()
            data[instance_id] = entry
            self._write(data)
            return entry

    # -- reconciliation (host/broker restart) ---------------------------------
    def reconcile(
        self,
        live_projects: list[str],
        *,
        replica_json_loader: Callable[[str], dict[str, Any] | None],
    ) -> dict[str, Any]:
        """Rebuild truth from Docker labels + each replica's ``replica.json`` after a restart.

        ``live_projects`` is the authoritative set of ``aq-replica-*`` compose projects seen via
        Docker labels. For each, ``replica_json_loader(project)`` returns the parsed ``replica.json``
        (or ``None`` if unreadable). Any registered project NOT in ``live_projects`` is marked
        ``torn_down`` (its stack is gone) and its credential revoked. Returns a summary.
        """
        summary = {"seen": len(live_projects), "reconciled": 0, "added": 0, "torn_down": 0}
        live_set = set(live_projects)
        with _file_lock(self.lock_path):
            data = self._read()
            by_project = {e.get("project"): iid for iid, e in data.items()}

            for project in live_projects:
                manifest = replica_json_loader(project)
                if not manifest or not manifest.get("instance_id"):
                    continue
                instance_id = manifest["instance_id"]
                # A live stack that passed stand-up is 'live'; a manifest with a failed/other status
                # still gets recorded (as standing_up) so the operator sees it, but is not marked live.
                lifecycle = LIFECYCLE_LIVE if manifest.get("status") == "executed" else LIFECYCLE_STANDING_UP
                entry = _entry_from_standup(manifest, lifecycle=lifecycle)
                existing = data.get(instance_id)
                if existing:
                    entry["created_at"] = existing.get("created_at") or entry["created_at"]
                    entry["credential_fingerprint"] = existing.get("credential_fingerprint") or entry.get("credential_fingerprint")
                    entry["credential_revoked"] = bool(existing.get("credential_revoked"))
                    summary["reconciled"] += 1
                else:
                    summary["added"] += 1
                data[instance_id] = entry

            # Registered projects whose stacks no longer exist -> torn_down + revoke.
            for project, instance_id in by_project.items():
                if project not in live_set and data[instance_id].get("lifecycle_state") != LIFECYCLE_TORN_DOWN:
                    data[instance_id]["lifecycle_state"] = LIFECYCLE_TORN_DOWN
                    data[instance_id]["credential_revoked"] = True
                    data[instance_id]["updated_at"] = _now_iso()
                    summary["torn_down"] += 1

            self._write(data)
        return summary


# --- default host paths ------------------------------------------------------
def default_registry_path(state_root: Path | str | None = None,
                          env: dict[str, str] | None = None) -> Path:
    """The one host-wide registry file, beside the per-replica state dirs and daemon ledger."""
    if state_root is not None:
        return Path(state_root) / _REGISTRY_FILENAME
    # Lazy import to keep a clean host-only dependency; host_replica_stack is host-only too.
    from ralph_portable.host_replica_stack import default_state_root

    return default_state_root(env) / _REGISTRY_FILENAME


def load_replica_json(project: str, *, state_root: Path | str | None = None,
                      env: dict[str, str] | None = None) -> dict[str, Any] | None:
    """Read + parse a replica's ``replica.json`` manifest by compose project (for reconcile)."""
    from ralph_portable.host_replica_stack import replica_dir

    manifest = replica_dir(project, state_root=state_root, env=env) / "replica.json"
    try:
        data = json.loads(manifest.read_text())
    except (FileNotFoundError, ValueError):
        return None
    return data if isinstance(data, dict) else None
