#!/usr/bin/env python3
"""Host-side fleet HEALTH poller (#4834 comms Phase 1, design §10 Phase 1 + §9.2).

PURE OBSERVABILITY. The host OBSERVES replicas; replicas send nothing. This process reads the
HOST-OWNED fleet registry for live replicas and polls each one's ``/health`` at
``127.0.0.1:<app_mgmt_port>`` — the port coming ONLY from the registry, never an arbitrary or
guest-supplied address. Per poll it writes a ``health.observed`` envelope (``trust=host_observed``)
to the parent journal and updates the registry's topology health (live/stale/down + watermark). It
records the OBSERVER, observation time, endpoint, observed SHA, loop heartbeat, and failure reason —
strictly stronger than trusting a replica's self-reported heartbeat.

Why it is HOST-ONLY (import-firewall proof): it imports ``ralph_portable.fleet_registry`` (the
host-owned registry, a FORBIDDEN import for any guest-reachable module), exactly like the
replication daemon imports the docker step. ``tests/test_import_firewall.py`` proves this module's
import closure reaches the registry while the guest closure never does.

SAFETY RAILS (design §10 Phase 1 safety review):
  * SSRF-safe. The only URL it ever builds is ``http://127.0.0.1:<registry app_mgmt port>/health``.
    It reads endpoints ONLY from ``registry.live()`` — never from the guest-writable proposal store,
    never from an envelope, never a caller-supplied host. ``_assert_loopback_health_url`` re-checks
    the host is loopback and the path is exactly ``/health`` before every request, so it can call
    ONLY the read-only ``/health`` route — never a write / replication / actuation endpoint.
  * Bounded. Bounded thread-pool concurrency (``--max-workers``) and a hard per-poll timeout.
  * Observation only. It never grants a capability, never mutates replication controls, and accepts
    NO replica-authored status (Phase 2). It only READS ``/health`` and WRITES host-owned records.

Modes:
    --once      poll every live replica once, then exit (used by tests / cron).
    --interval  poll every N seconds until SIGINT/SIGTERM -> clean exit.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# Allow running from the repo root without an install.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ralph_portable.fleet_registry import (  # noqa: E402  (HOST-ONLY registry — forbidden to guests)
    HEALTH_DOWN,
    HEALTH_LIVE,
    HEALTH_STALE,
    FleetRegistryStore,
    default_registry_path,
)

log = logging.getLogger("aq.fleet.poller")

# Staleness threshold: once a replica has been observed live, a transient unreachable poll within
# this many seconds is "stale" (recently alive, momentarily missed); beyond it (or never seen live)
# it is "down". Env-overridable; no new required config.
DEFAULT_STALE_THRESHOLD_S = float(os.environ.get("AQ_FLEET_STALE_THRESHOLD_S", "90"))
DEFAULT_TIMEOUT_S = float(os.environ.get("AQ_FLEET_POLL_TIMEOUT_S", "5"))
DEFAULT_MAX_WORKERS = int(os.environ.get("AQ_FLEET_POLL_MAX_WORKERS", "8"))
DEFAULT_INTERVAL_S = float(os.environ.get("AQ_FLEET_POLL_INTERVAL_S", "30"))

# The host is the observer. This principal is server(host)-derived, never claimed by a replica.
HOST_OBSERVER_PRINCIPAL = "host:fleet-poller"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- one HTTP poll -----------------------------------------------------------
@dataclass
class PollResult:
    """The raw outcome of one ``/health`` GET. ``reachable`` means we got an HTTP response body."""

    instance_id: str
    endpoint: str                       # "127.0.0.1:<port>" — host-local, redacted (not a URL)
    reachable: bool = False
    http_result: str = "unknown"        # ok | refused | timeout | http_error | non_json | degraded
    git_sha: str | None = None
    cycling: bool | None = None
    seconds_since_last_cycle: float | None = None
    reason: str = ""
    body: dict[str, Any] = field(default_factory=dict)


def _default_opener(url: str, *, timeout_s: float) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout_s) as resp:  # noqa: S310 (loopback /health only)
        return resp.read()


def _assert_loopback_health_url(url: str) -> None:
    """Fail closed unless ``url`` is exactly a loopback ``/health`` GET (SSRF + route guard).

    Belt-and-suspenders: even though the poller only ever builds this URL itself from a registry
    port, this asserts — right before the request — that the host is loopback and the path is
    ``/health`` and nothing else. It makes "the poller can hit ONLY /health, only on loopback"
    a checked invariant, not merely a convention.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme != "http":
        raise ValueError(f"refusing non-http fleet poll url {url!r}")
    if parsed.hostname not in ("127.0.0.1", "localhost"):
        raise ValueError(f"refusing non-loopback fleet poll host {parsed.hostname!r} (SSRF guard)")
    if parsed.path != "/health":
        raise ValueError(f"refusing non-/health fleet poll path {parsed.path!r} (read-only route)")
    if parsed.query or parsed.params:
        raise ValueError("refusing fleet poll url with query/params (read-only /health only)")


def poll_one(
    entry: dict[str, Any], *, timeout_s: float = DEFAULT_TIMEOUT_S,
    opener: Callable[..., bytes] | None = None,
) -> PollResult:
    """Poll ONE registry entry's ``/health``. The URL is built ONLY from the registry app_mgmt port
    on loopback — the entry's ``health_url`` and any other address are deliberately ignored."""
    instance_id = str(entry.get("instance_id") or "unknown")
    ports = entry.get("ports") or {}
    port = ports.get("app_mgmt")
    endpoint = f"127.0.0.1:{port}"
    if port is None:
        return PollResult(instance_id, endpoint, reachable=False, http_result="no_port",
                          reason="registry entry has no app_mgmt port")

    url = f"http://127.0.0.1:{int(port)}/health"
    _assert_loopback_health_url(url)  # SSRF + read-only-route guard, checked every call
    fetch = opener or _default_opener

    try:
        raw = fetch(url, timeout_s=timeout_s)
    except (urllib.error.HTTPError,) as exc:
        return PollResult(instance_id, endpoint, reachable=False, http_result="http_error",
                          reason=f"/health HTTP error: {exc}")
    except (urllib.error.URLError, ConnectionError, OSError, TimeoutError) as exc:
        reason = str(exc)
        result = "timeout" if "timed out" in reason.lower() or isinstance(exc, TimeoutError) else "refused"
        return PollResult(instance_id, endpoint, reachable=False, http_result=result,
                          reason=f"/health unreachable at {endpoint} ({reason})")

    try:
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError("not a JSON object")
    except (ValueError, json.JSONDecodeError) as exc:
        return PollResult(instance_id, endpoint, reachable=True, http_result="non_json",
                          reason=f"/health returned non-JSON ({exc})")

    git_sha = body.get("git_sha")
    cycling = body.get("cycling")
    ssc = body.get("seconds_since_last_cycle")
    return PollResult(
        instance_id, endpoint, reachable=True, http_result="ok",
        git_sha=str(git_sha) if git_sha else None,
        cycling=bool(cycling) if cycling is not None else None,
        seconds_since_last_cycle=float(ssc) if isinstance(ssc, (int, float)) else None,
        reason="reachable", body=body,
    )


# --- health classification ---------------------------------------------------
def classify_health(
    poll: PollResult, *, expected_git_sha: str | None, last_successful_poll: str | None,
    now: str, stale_threshold_s: float,
) -> tuple[str, str]:
    """Derive ``(health_state, reason)`` from a poll + the healthy-watermark (design §9.2).

    - reachable + ok + expected SHA matches (or none expected) -> ``live``.
    - reachable but DEGRADED (SHA mismatch, missing SHA, ok:false) -> ``stale`` + reason.
    - unreachable (refused/timeout) -> ``stale`` if a successful poll is within the threshold
      (recently alive, momentary miss), else ``down``.
    """
    if poll.reachable and poll.http_result == "ok":
        if not poll.git_sha:
            return HEALTH_STALE, "reachable but /health reported no git_sha"
        if expected_git_sha and poll.git_sha != expected_git_sha:
            return HEALTH_STALE, (
                f"version mismatch: registry expected {str(expected_git_sha)[:12]}, "
                f"observed {str(poll.git_sha)[:12]}")
        return HEALTH_LIVE, poll.reason
    if poll.reachable:
        # reachable but non_json / http_error body — degraded, not fully live.
        return HEALTH_STALE, poll.reason or "reachable but degraded /health"

    # Unreachable: age of the last healthy observation decides stale vs down.
    age = _age_seconds(last_successful_poll, now)
    if age is not None and age <= stale_threshold_s:
        return HEALTH_STALE, f"{poll.reason}; last healthy {age:.0f}s ago (<= {stale_threshold_s:.0f}s)"
    detail = "never observed healthy" if age is None else f"last healthy {age:.0f}s ago"
    return HEALTH_DOWN, f"{poll.reason}; {detail}"


def _age_seconds(iso: str | None, now_iso: str) -> float | None:
    if not iso:
        return None
    try:
        then = datetime.fromisoformat(iso)
        now = datetime.fromisoformat(now_iso)
    except ValueError:
        return None
    return (now - then).total_seconds()


# --- envelope construction ---------------------------------------------------
def build_health_observed_envelope(
    entry: dict[str, Any], poll: PollResult, *, health_state: str, reason: str,
    last_successful_poll: str | None, observed_at: str, parent_instance_id: str | None = None,
) -> dict[str, Any]:
    """A host-observed ``health.observed`` envelope for the parent journal (design §4.1/§10 Phase 1).

    ``trust=host_observed`` — the host is the observer; this is ground truth, NOT a replica claim.
    Only this poller (writing directly to the store) can mint host_observed; a replica publishing
    the same kind through the authenticated API is stamped ``untrusted_claim`` and can never
    overwrite this observation.
    """
    from management.api.comms_envelope import build_envelope

    instance_id = str(entry.get("instance_id") or "unknown")
    ports = entry.get("ports") or {}
    origin = parent_instance_id or entry.get("parent_instance_id") or "host"
    topology = {
        "instance_id": instance_id,
        "project": entry.get("project"),
        "parent_instance_id": entry.get("parent_instance_id"),
        "lineage": [x for x in (entry.get("parent_instance_id"), instance_id) if x],
        "created_at": entry.get("created_at"),
        "workflow": entry.get("workflow"),
        "workflow_version": entry.get("workflow_version"),
        "expected_git_sha": entry.get("git_sha"),
        "app_mgmt_port": ports.get("app_mgmt"),
        "lifecycle_state": entry.get("lifecycle_state"),
        # A live/standing-up stack still counts against the replica cap; a torn-down one does not.
        "counts_against_cap": entry.get("lifecycle_state") != "torn_down",
        "credential_present": bool(entry.get("credential_fingerprint")),
        "credential_revoked": bool(entry.get("credential_revoked")),
    }
    payload = {
        "observed_at": observed_at,
        "endpoint": poll.endpoint,                 # host-local port string, not a URL
        "health_state": health_state,
        "reachable": poll.reachable,
        "http_result": poll.http_result,
        "observed_git_sha": poll.git_sha,
        "cycling": poll.cycling,
        "seconds_since_last_cycle": poll.seconds_since_last_cycle,
        "reason": reason,
        "last_successful_poll": last_successful_poll,
        "topology": topology,
    }
    return build_envelope(
        origin_instance_id=str(origin),
        principal_id=HOST_OBSERVER_PRINCIPAL,
        channel=f"instance/{instance_id}/health",
        kind="health.observed",
        payload=payload,
        target_instance_id=instance_id,
        target_handle="replica",
        trust="host_observed",
    )


# --- one poll of the whole fleet ---------------------------------------------
def run_once(
    registry: FleetRegistryStore,
    store: Any,
    *,
    opener: Callable[..., bytes] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_workers: int = DEFAULT_MAX_WORKERS,
    stale_threshold_s: float = DEFAULT_STALE_THRESHOLD_S,
    parent_instance_id: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Poll every LIVE registry replica once, writing one host_observed event per replica and
    updating each entry's topology health. HTTP polls run under bounded concurrency; the registry
    writes are then applied sequentially (each is its own flock'd transaction).

    Returns a summary ``{polled, live, stale, down, events, results}``.
    """
    live = [e for e in registry.live() if (e.get("ports") or {}).get("app_mgmt") is not None]
    observed_at = now or _now_iso()

    # ---- bounded-concurrency HTTP fan-out (read-only /health) --------------
    def _do(entry: dict[str, Any]) -> PollResult:
        return poll_one(entry, timeout_s=timeout_s, opener=opener)

    if live:
        workers = max(1, min(max_workers, len(live)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            polls = list(pool.map(_do, live))
    else:
        polls = []

    summary: dict[str, Any] = {"polled": len(live), "live": 0, "stale": 0, "down": 0,
                               "events": 0, "results": []}

    # ---- classify + persist (sequential, each write is a flock'd transaction) ----
    for entry, poll in zip(live, polls):
        instance_id = str(entry.get("instance_id"))
        last_ok = registry.last_successful_poll(instance_id)
        state, reason = classify_health(
            poll, expected_git_sha=entry.get("git_sha"), last_successful_poll=last_ok,
            now=observed_at, stale_threshold_s=stale_threshold_s)

        registry.record_observed_health(
            instance_id, state=state, observed_at=observed_at, reason=reason,
            reachable=poll.reachable, observed_git_sha=poll.git_sha, cycling=poll.cycling)

        envelope = build_health_observed_envelope(
            entry, poll, health_state=state, reason=reason, last_successful_poll=last_ok,
            observed_at=observed_at, parent_instance_id=parent_instance_id)
        store.create_envelope(envelope)

        summary[state] += 1
        summary["events"] += 1
        summary["results"].append({
            "instance_id": instance_id, "health_state": state,
            "endpoint": poll.endpoint, "reason": reason, "envelope_id": envelope["id"],
        })
        log.info("observed %s -> %s (%s) at %s", instance_id, state, poll.http_result, poll.endpoint)

    log.info("fleet poll: %d live replicas -> %d live / %d stale / %d down",
             summary["polled"], summary["live"], summary["stale"], summary["down"])
    return summary


# --- interval loop -----------------------------------------------------------
def run_forever(
    registry_factory: Callable[[], FleetRegistryStore],
    store_factory: Callable[[], Any],
    *,
    interval_s: float,
    max_polls: int | None = None,
    should_stop: Callable[[], bool] | None = None,
    **run_once_kwargs: Any,
) -> dict[str, Any]:
    """Poll on an interval until signalled (or ``max_polls`` reached — used by tests)."""
    polls = 0
    totals = {"polls": 0, "events": 0, "live": 0, "stale": 0, "down": 0}
    while True:
        if should_stop is not None and should_stop():
            break
        summary = run_once(registry_factory(), store_factory(), **run_once_kwargs)
        polls += 1
        totals["polls"] = polls
        for k in ("events", "live", "stale", "down"):
            totals[k] += summary[k]
        if max_polls is not None and polls >= max_polls:
            break
        if should_stop is not None and should_stop():
            break
        time.sleep(interval_s)
    return totals


# --- CLI ---------------------------------------------------------------------
def _build_live_store(env: dict[str, str]):
    # Live path only: the same durable parent journal the guest reads. Imported lazily so importing
    # this module for tests never pulls the store in.
    from management.api.store import build_store  # noqa: PLC0415

    return build_store(env)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Host fleet /health poller (HOST-ONLY, pure observability)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true",
                      help="Poll every live replica once, then exit.")
    mode.add_argument("--interval", type=float, metavar="SECONDS",
                      help="Poll every SECONDS until SIGINT/SIGTERM.")
    parser.add_argument("--registry", type=Path, default=None,
                        help="Fleet registry JSON path (default: the host-wide registry).")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S,
                        help=f"Per-poll /health timeout seconds (default {DEFAULT_TIMEOUT_S}).")
    parser.add_argument("--max-workers", type=int, default=DEFAULT_MAX_WORKERS,
                        help=f"Bounded poll concurrency (default {DEFAULT_MAX_WORKERS}).")
    parser.add_argument("--stale-threshold", type=float, default=DEFAULT_STALE_THRESHOLD_S,
                        help=f"Seconds a missed poll stays stale before down (default "
                             f"{DEFAULT_STALE_THRESHOLD_S}).")
    parser.add_argument("--max-polls", type=int, default=None,
                        help="Stop after N polls in --interval mode (mostly for tests).")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    env = dict(os.environ)
    registry_path = args.registry or default_registry_path(env=env)
    parent_instance_id = (env.get("AQ_INSTANCE_ID") or "").strip() or None

    def _registry() -> FleetRegistryStore:
        return FleetRegistryStore(registry_path)

    common = dict(
        timeout_s=args.timeout, max_workers=args.max_workers,
        stale_threshold_s=args.stale_threshold, parent_instance_id=parent_instance_id,
    )

    if args.once:
        summary = run_once(_registry(), _build_live_store(env), **common)
        print(json.dumps({"ok": True, "mode": "once", **summary}, indent=2, default=str))
        return 0

    stop = {"flag": False}

    def _handle(signum, _frame):
        log.warning("signal %s received — finishing the current poll then exiting", signum)
        stop["flag"] = True

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    totals = run_forever(
        _registry, lambda: _build_live_store(env), interval_s=args.interval,
        max_polls=args.max_polls, should_stop=lambda: stop["flag"], **common)
    print(json.dumps({"ok": True, "mode": "interval", **totals}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
