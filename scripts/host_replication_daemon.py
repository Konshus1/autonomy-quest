#!/usr/bin/env python3
"""Host-side replication AUTO-EXECUTOR daemon (#4834 replication Step 1.5).

DANGER — THE MOST POWERFUL SURFACE IN THE REPO. This daemon closes the self-replication
loop AUTONOMOUSLY: a guest container writes an approved/auto-approved ``straight_copy``
proposal row and — with ZERO further human action — this host process polls it and stands
up a REAL, independent ~4-service Docker Compose replica. It is the automation of the manual
``scripts/host_replication_broker.py`` path. Because it removes the human from the loop, its
safety rails are non-negotiable:

  * HOST-ONLY. The operator starts it on the host. It is NEVER auto-started with the compose
    stack and NEVER runs inside a guest container. It imports ``ralph_portable.host_replica_stack``
    (the docker step), which the import firewall forbids any guest-reachable module from
    importing — so the daemon is structurally outside the guest closure.
  * DOUBLE OPT-IN. It refuses to run unless BOTH an explicit host env
    (``AQ_REPLICATION_DAEMON_ENABLE=1``) AND the testing override
    (``AQ_REPLICATION_AUTO_APPROVE=1``) are set. Neither is committed on. It never runs by
    accident and never on the committed public default.
  * SAME GATES AS THE BROKER. Every stand-up runs ``stand_up_replica_stack``, which enforces
    the cap (bounded across generations, counts stopped stacks), the memory gate (fail-closed),
    host-entropy isolation, and the reversible teardown rail — all BEFORE any compose, all
    under the host-side cap flock. A FLOOD of proposals therefore yields at most ``cap`` replicas
    then refusals.
  * IDEMPOTENT. A host-side ledger (``daemon-ledger.json`` beside the replica state) records
    which proposals were executed so a proposal is never stood up twice — across ``--once`` runs
    and daemon restarts. The daemon keeps its own host-authoritative ledger rather than trusting
    or mutating the guest-writable proposal store.

Modes:
    --once      drain all currently-pending proposals, then exit (used by tests / cron).
    --interval  poll every N seconds until signalled (SIGINT/SIGTERM -> clean exit).

Do NOT point this at the live ``aq-seed-c2f4834`` proposals; use an isolated test store.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Callable

# Allow running from the repo root without an install.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ralph_portable.host_replica_stack import (  # noqa: E402  (HOST-ONLY docker step)
    default_state_root,
    stand_up_replica_stack,
)
from ralph_portable.replication_request import (  # noqa: E402
    override_enabled,
    validate_replication_request,
)

log = logging.getLogger("aq.replication.daemon")

# The second opt-in (the first is AQ_REPLICATION_AUTO_APPROVE via override_enabled).
DAEMON_ENABLE_ENV = "AQ_REPLICATION_DAEMON_ENABLE"

# A proposal is a daemon work item when it is a straight_copy that the host has approved and
# has not yet executed. copy_with_modifications is Step 2 and is deliberately NOT executed here.
_EXECUTABLE_MODES = frozenset({"straight_copy"})
_APPROVED_STATUSES = frozenset({"approved", "auto_approved"})
_TRUTHY = frozenset({"1", "true", "yes", "on"})


# --- opt-in guard ------------------------------------------------------------
def daemon_enabled(env: dict[str, str] | None = None) -> tuple[bool, str]:
    """Double opt-in gate. Returns ``(enabled, reason)``.

    Requires BOTH the explicit daemon enable env AND the auto-approve/testing override. Off by
    the committed default; either missing => the daemon refuses to touch docker.
    """
    source = env if env is not None else os.environ
    enable = str(source.get(DAEMON_ENABLE_ENV, "")).strip().lower() in _TRUTHY
    override = override_enabled(source)
    if enable and override:
        return True, "enabled: daemon opt-in + auto-approve override both set"
    missing = []
    if not enable:
        missing.append(f"{DAEMON_ENABLE_ENV}=1")
    if not override:
        missing.append("AQ_REPLICATION_AUTO_APPROVE=1")
    return False, (
        "REFUSING to run the auto-executor daemon: require BOTH "
        + " AND ".join(missing)
        + " (neither is committed on — this is the most powerful surface, testing only)"
    )


# --- proposal selection ------------------------------------------------------
def proposal_id(record: dict[str, Any], index: int) -> str:
    """A stable id for a proposal row: positional index + canonical content hash.

    The store is append-only and order-preserving (both InMemoryStore and PgStore order by
    insertion), so ``index`` is stable for a given row across polls; the content hash keeps two
    identical-content proposals distinct. This id keys the idempotency ledger.
    """
    payload = {k: record.get(k) for k in ("mode", "mission_id", "mission",
                                          "requester_instance_id", "modifications")}
    blob = f"{index}:" + json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def is_executable_proposal(record: dict[str, Any]) -> bool:
    if not isinstance(record, dict):
        return False
    if str(record.get("mode") or "").strip() not in _EXECUTABLE_MODES:
        return False
    if str(record.get("status") or "").strip() not in _APPROVED_STATUSES:
        return False
    # Never act on a malformed/guest-privilege-escalating request.
    return not validate_replication_request(record)


def select_pending(records: list[dict[str, Any]], executed_ids: set[str]) -> list[tuple[str, dict[str, Any]]]:
    """(proposal_id, record) for executable proposals not already in the ledger."""
    pending: list[tuple[str, dict[str, Any]]] = []
    for i, rec in enumerate(records):
        if not is_executable_proposal(rec):
            continue
        pid = proposal_id(rec, i)
        if pid in executed_ids:
            continue
        pending.append((pid, rec))
    return pending


# --- host-authoritative idempotency ledger -----------------------------------
class Ledger:
    """A JSON file recording which proposal ids the host has already executed.

    Host-side and host-authoritative: the daemon does NOT trust the guest-writable proposal
    store for idempotency (a compromised guest could flip a status), nor does it mutate that
    store. This ledger is the source of truth for "already stood up".
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        try:
            self._data = json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError):
            self._data = {}
        if not isinstance(self._data, dict):
            self._data = {}

    def executed_ids(self) -> set[str]:
        return set(self._data.keys())

    def record(self, pid: str, entry: dict[str, Any]) -> None:
        self._data[pid] = entry
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True))
        os.replace(tmp, self.path)  # atomic swap so a crash never leaves a half-written ledger


def default_ledger_path(env: dict[str, str] | None = None) -> Path:
    return default_state_root(env) / "daemon-ledger.json"


# --- one poll (drain) --------------------------------------------------------
def run_once(
    store: Any,
    *,
    repo_root: Path | str,
    env: dict[str, str] | None = None,
    state_root: Path | str | None = None,
    ledger_path: Path | str | None = None,
    lock_path: Path | str | None = None,
    build: bool = True,
    do_health_check: bool = True,
    health_timeout_s: float = 240.0,
    docker=None,
    mem_reader=None,
    compose_runner=None,
    stand_up: Callable[..., dict[str, Any]] = stand_up_replica_stack,
) -> dict[str, Any]:
    """Drain every currently-pending proposal ONCE. Bounded by the same cap+memory gates.

    Returns a summary ``{polled, pending, executed, refused, failed, results}``. Records an
    executed proposal in the ledger so it is never re-executed. A proposal REFUSED by a gate
    (cap/memory) is deliberately left OUT of the ledger so it can execute later when capacity
    frees — this is what makes a flood bounded (``cap`` execute, the rest refuse) yet not lost.
    """
    env = dict(env) if env is not None else dict(os.environ)
    ledger = Ledger(ledger_path if ledger_path is not None else default_ledger_path(env))

    records = list(store.replications())
    pending = select_pending(records, ledger.executed_ids())

    summary: dict[str, Any] = {
        "polled": len(records), "pending": len(pending),
        "executed": 0, "refused": 0, "failed": 0, "results": [],
    }
    log.info("daemon poll: %d proposal rows, %d pending straight_copy to execute",
             len(records), len(pending))

    # Pass through the injectable seams only when provided (keep stand_up's own defaults otherwise).
    extra: dict[str, Any] = {}
    if docker is not None:
        extra["docker"] = docker
    if mem_reader is not None:
        extra["mem_reader"] = mem_reader
    if compose_runner is not None:
        extra["compose_runner"] = compose_runner

    for pid, rec in pending:
        log.info("standing up replica for proposal %s (mission=%s requester=%s)",
                 pid, rec.get("mission_id") or rec.get("mission"), rec.get("requester_instance_id"))
        result = stand_up(
            rec, status=str(rec.get("status")), repo_root=repo_root,
            state_root=state_root, env=env, build=build,
            do_health_check=do_health_check, health_timeout_s=health_timeout_s,
            lock_path=lock_path, **extra,
        )
        outcome = {"proposal_id": pid, "status": result.get("status"),
                   "project": result.get("project"), "gate": result.get("gate"),
                   "reason": result.get("reason") or result.get("error")}
        summary["results"].append(outcome)

        if result.get("status") == "executed":
            summary["executed"] += 1
            ledger.record(pid, {
                "status": "executed", "project": result.get("project"),
                "instance_id": result.get("instance_id"),
                "mission_id": rec.get("mission_id") or rec.get("mission"),
                "requester_instance_id": rec.get("requester_instance_id"),
                "health_url": result.get("health_url"),
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })
            log.info("EXECUTED proposal %s -> %s", pid, result.get("project"))
        elif result.get("status") == "refused":
            summary["refused"] += 1
            log.warning("REFUSED proposal %s at gate=%s: %s", pid,
                        result.get("gate"), result.get("reason"))
        else:
            summary["failed"] += 1
            log.error("FAILED proposal %s: %s", pid, result.get("error"))

    return summary


# --- interval loop -----------------------------------------------------------
def run_forever(
    store_factory: Callable[[], Any],
    *,
    interval_s: float,
    max_polls: int | None = None,
    should_stop: Callable[[], bool] | None = None,
    **run_once_kwargs: Any,
) -> dict[str, Any]:
    """Poll on an interval until signalled (or ``max_polls`` reached — used by tests)."""
    polls = 0
    totals = {"polls": 0, "executed": 0, "refused": 0, "failed": 0}
    while True:
        if should_stop is not None and should_stop():
            break
        summary = run_once(store_factory(), **run_once_kwargs)
        polls += 1
        totals["polls"] = polls
        for k in ("executed", "refused", "failed"):
            totals[k] += summary[k]
        if max_polls is not None and polls >= max_polls:
            break
        if should_stop is not None and should_stop():
            break
        time.sleep(interval_s)
    return totals


# --- CLI ---------------------------------------------------------------------
def _build_live_store(env: dict[str, str]):
    # Live path only: the same durable store the guest wrote proposals to (a shared DB on the
    # host). Imported lazily so importing this module for tests never pulls the store in.
    from management.api.store import build_store  # noqa: PLC0415

    return build_store(env)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Host replication auto-executor daemon (HOST-ONLY)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true",
                      help="Drain all pending approved straight_copy proposals, then exit.")
    mode.add_argument("--interval", type=float, metavar="SECONDS",
                      help="Poll every SECONDS until SIGINT/SIGTERM.")
    parser.add_argument("--repo-root", type=Path, default=ROOT,
                        help="Build-context worktree for the replica image (default: this repo).")
    parser.add_argument("--no-build", action="store_true",
                        help="Skip --build on compose up (reuse cached image; faster).")
    parser.add_argument("--no-health-check", action="store_true",
                        help="Skip the /health assertion (diagnostics only).")
    parser.add_argument("--max-polls", type=int, default=None,
                        help="Stop after N polls in --interval mode (mostly for tests).")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    env = dict(os.environ)
    enabled, reason = daemon_enabled(env)
    if not enabled:
        log.error(reason)
        print(json.dumps({"ok": False, "enabled": False, "reason": reason}, indent=2))
        return 3

    log.warning("AUTO-EXECUTOR DAEMON ENABLED — approved straight_copy proposals will be stood up "
                "with NO human action. %s", reason)

    common = dict(
        repo_root=args.repo_root, env=env,
        build=not args.no_build, do_health_check=not args.no_health_check,
    )

    if args.once:
        summary = run_once(_build_live_store(env), **common)
        print(json.dumps({"ok": True, "mode": "once", **summary}, indent=2))
        return 0

    # --interval: clean shutdown on SIGINT/SIGTERM.
    stop = {"flag": False}

    def _handle(signum, _frame):
        log.warning("signal %s received — finishing the current poll then exiting", signum)
        stop["flag"] = True

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    totals = run_forever(
        lambda: _build_live_store(env), interval_s=args.interval,
        max_polls=args.max_polls, should_stop=lambda: stop["flag"], **common,
    )
    print(json.dumps({"ok": True, "mode": "interval", **totals}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
