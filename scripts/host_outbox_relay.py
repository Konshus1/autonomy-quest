#!/usr/bin/env python3
"""Host-side OUTBOX RELAY: replica local outbox -> parent journal (#4834 comms Phase 2, design §4.3/§10 Phase 2).

The host pulls each live replica's AUTHENTICATED outbox and COPIES the envelopes into the parent
journal, stamping both the original and relay timestamps. The replica AUTHORS the claims; the host
only relays them. Copied envelopes keep ``trust=untrusted_claim`` — the relay can NEVER upgrade a
replica claim to host_observed, satisfy a gate, adopt, merge, or promote. They land in the journal as
untrusted claims, queued for INDEPENDENT parent verification.

HOST-ONLY (import-firewall proof): it imports ``ralph_portable.fleet_registry`` (host-owned, a
FORBIDDEN import for any guest-reachable module), exactly like the Phase-1 poller and the replication
daemon. ``tests/test_import_firewall.py`` proves this import closure reaches the registry while the
guest closure never does.

SAFETY RAILS (design §10 Phase 2 safety review), reusing the Phase-1 poller discipline:
  * SSRF-safe. The ONLY URL it builds is ``http://127.0.0.1:<registry app_mgmt port>/api/agent-comms/outbox``.
    Ports come ONLY from ``registry.live()`` — never from an envelope, a proposal row, or a caller host.
    It REFUSES to follow any 3xx redirect (a replica controls its own responses) and re-asserts the
    loopback host + exact ``/api/agent-comms/outbox`` path before trusting any body. It is a read-only
    GET against one route; it never POSTs to a replica (no parent->replica command path — that is Phase 3).
  * Idempotent. Copies dedup on the immutable GLOBAL envelope id (``store.relay_envelope``), and a
    per-replica monotonic cursor persisted in the host registry lets a restart resume without re-pulling.
  * Bounded. Bounded page size + per-request timeout; teardown mid-transfer leaves no orphan (each copy
    is its own idempotent transaction; a half-finished batch just re-relays safely next pass).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ralph_portable.fleet_registry import (  # noqa: E402  (HOST-ONLY registry — forbidden to guests)
    FleetRegistryStore,
    default_registry_path,
)

log = logging.getLogger("aq.comms.relay")

DEFAULT_TIMEOUT_S = float(os.environ.get("AQ_RELAY_TIMEOUT_S", "5"))
DEFAULT_PAGE = int(os.environ.get("AQ_RELAY_PAGE", "200"))

RELAY_PRINCIPAL = "host:outbox-relay"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_expired(env: dict[str, Any]) -> bool:
    # Lazy import: comms_envelope is guest-safe, kept off the module-import top to keep this host-only
    # script's closure lean (and importing this file for tests never pulls the API layer).
    from management.api.comms_envelope import is_expired

    return is_expired(env)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse to FOLLOW any 3xx on the outbox pull (SSRF discipline shared with the Phase-1 poller).

    A replica fully controls its own ``/api/agent-comms/outbox`` response. Auto-following a redirect
    would turn the relay into an arbitrary-outbound-GET primitive (other ports, cloud metadata,
    GET-with-side-effect routes). A healthy outbox returns 200, never a 3xx, so refusing to follow is
    both correct and safe."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler)


def _assert_loopback_outbox_url(url: str) -> None:
    """Fail closed unless ``url`` is exactly a loopback ``/api/agent-comms/outbox`` GET (SSRF + route guard)."""
    parsed = urlparse(url)
    if parsed.scheme != "http":
        raise ValueError(f"refusing non-http relay url {url!r}")
    if parsed.hostname not in ("127.0.0.1", "localhost"):
        raise ValueError(f"refusing non-loopback relay host {parsed.hostname!r} (SSRF guard)")
    if parsed.path != "/api/agent-comms/outbox":
        raise ValueError(f"refusing non-outbox relay path {parsed.path!r} (read-only route)")


def _default_fetch(url: str, *, token: str, timeout_s: float) -> bytes:
    req = urllib.request.Request(url, method="GET",
                                 headers={"Authorization": f"Bearer {token}"})
    with _NO_REDIRECT_OPENER.open(req, timeout=timeout_s) as resp:  # noqa: S310 (loopback outbox)
        _assert_loopback_outbox_url(resp.geturl())
        return resp.read()


def _token_for(instance_id: str, entry: dict[str, Any], env: dict[str, str]) -> str | None:
    """Resolve the scoped outbox-read token the host presents to a replica.

    Sourced from host config only (never from an envelope): ``AQ_COMMS_RELAY_TOKENS`` JSON
    ``{instance_id: token}``, else a single ``AQ_COMMS_OUTBOX_READ_TOKEN`` for all replicas (dev)."""
    raw = (env.get("AQ_COMMS_RELAY_TOKENS") or "").strip()
    if raw:
        try:
            mapping = json.loads(raw)
            if isinstance(mapping, dict) and mapping.get(instance_id):
                return str(mapping[instance_id])
        except ValueError:
            pass
    return (env.get("AQ_COMMS_OUTBOX_READ_TOKEN") or "").strip() or None


def relay_one(
    entry: dict[str, Any], registry: FleetRegistryStore, store: Any, *,
    token: str, timeout_s: float = DEFAULT_TIMEOUT_S, page: int = DEFAULT_PAGE,
    fetch: Callable[..., bytes] | None = None, relayed_at: str | None = None,
) -> dict[str, Any]:
    """Pull one replica's outbox from its cursor and copy each envelope to the parent journal.

    Returns ``{instance_id, pulled, relayed, duplicates, cursor}``. Every copy is idempotent on the
    global id, so an at-least-once retry, a relay restart, or teardown mid-transfer never duplicates."""
    instance_id = str(entry.get("instance_id") or "unknown")
    ports = entry.get("ports") or {}
    port = ports.get("app_mgmt")
    relayed_at = relayed_at or _now_iso()
    if port is None:
        return {"instance_id": instance_id, "pulled": 0, "relayed": 0, "duplicates": 0,
                "cursor": registry.relay_cursor(instance_id), "reason": "no app_mgmt port"}

    cursor = registry.relay_cursor(instance_id)
    url = f"http://127.0.0.1:{int(port)}/api/agent-comms/outbox?after_seq={cursor}&limit={int(page)}"
    _assert_loopback_outbox_url(url)
    do_fetch = fetch or _default_fetch

    try:
        raw = do_fetch(url, token=token, timeout_s=timeout_s)
    except (urllib.error.HTTPError, urllib.error.URLError, ConnectionError, OSError, TimeoutError) as exc:
        log.info("relay skip %s: outbox unreachable (%s)", instance_id, exc)
        return {"instance_id": instance_id, "pulled": 0, "relayed": 0, "duplicates": 0,
                "cursor": cursor, "reason": f"unreachable: {exc}"}

    try:
        body = json.loads(raw)
        items = body.get("items") if isinstance(body, dict) else None
        if not isinstance(items, list):
            raise ValueError("outbox response missing items list")
    except (ValueError, json.JSONDecodeError) as exc:
        log.warning("relay skip %s: bad outbox body (%s)", instance_id, exc)
        return {"instance_id": instance_id, "pulled": 0, "relayed": 0, "duplicates": 0,
                "cursor": cursor, "reason": f"bad body: {exc}"}

    pulled = relayed = duplicates = dropped = 0
    max_seq = cursor
    for row in items:
        if not isinstance(row, dict):
            continue
        seq = row.get("seq")
        env = row.get("envelope")
        if not isinstance(env, dict) or "id" not in env:
            continue
        pulled += 1
        # A claim that EXPIRED before the host could pull it is DROPPED — never copied into the
        # parent journal — but the cursor still advances past it so it is not re-pulled forever.
        if _is_expired(env):
            dropped += 1
            if isinstance(seq, int):
                max_seq = max(max_seq, seq)
            continue
        stamped = _stamp_relay(env, source_instance_id=instance_id, relayed_at=relayed_at)
        result = store.relay_envelope(stamped)
        if result.get("duplicate"):
            duplicates += 1
        else:
            relayed += 1
        if isinstance(seq, int):
            max_seq = max(max_seq, seq)

    if max_seq > cursor:
        registry.record_relay_cursor(instance_id, cursor=max_seq, relayed_at=relayed_at,
                                     relayed_count=relayed)
    log.info("relayed %s: pulled=%d new=%d dup=%d dropped=%d cursor %d->%d",
             instance_id, pulled, relayed, duplicates, dropped, cursor, max_seq)
    return {"instance_id": instance_id, "pulled": pulled, "relayed": relayed,
            "duplicates": duplicates, "dropped": dropped, "cursor": max_seq}


def _stamp_relay(env: dict[str, Any], *, source_instance_id: str, relayed_at: str) -> dict[str, Any]:
    """Copy the envelope, adding relay provenance WITHOUT changing identity/trust/kind/payload.

    The copy keeps the immutable global ``id`` (so re-relay dedups) and ``trust=untrusted_claim`` (a
    relay can never upgrade trust). It only ADDS a ``relay`` block with both timestamps + the observer,
    and sets transport ``delivery=relayed`` (a transport state — never evidence of execution/success)."""
    copy = dict(env)
    copy["delivery"] = "relayed"
    copy["relay"] = {
        "relayed_at": relayed_at,
        "relayed_by": RELAY_PRINCIPAL,
        "source_instance_id": source_instance_id,
        "original_created_at": env.get("created_at"),
    }
    return copy


def run_once(registry: FleetRegistryStore, store: Any, *, env: dict[str, str] | None = None,
             timeout_s: float = DEFAULT_TIMEOUT_S, page: int = DEFAULT_PAGE,
             fetch: Callable[..., bytes] | None = None,
             token_for: Callable[[str, dict[str, Any], dict[str, str]], str | None] | None = None,
             ) -> dict[str, Any]:
    """Relay every live replica's outbox once. Replicas with no resolvable relay token are skipped
    (fail-closed: no token => no pull), never scanned or guessed."""
    env = dict(env if env is not None else os.environ)
    token_of = token_for or _token_for
    live = [e for e in registry.live() if (e.get("ports") or {}).get("app_mgmt") is not None]
    relayed_at = _now_iso()
    summary: dict[str, Any] = {"instances": len(live), "pulled": 0, "relayed": 0,
                               "duplicates": 0, "dropped": 0, "skipped": 0, "results": []}
    for entry in live:
        iid = str(entry.get("instance_id") or "unknown")
        token = token_of(iid, entry, env)
        if not token:
            summary["skipped"] += 1
            log.info("relay skip %s: no scoped outbox-read token configured", iid)
            continue
        res = relay_one(entry, registry, store, token=token, timeout_s=timeout_s, page=page,
                        fetch=fetch, relayed_at=relayed_at)
        summary["pulled"] += res["pulled"]
        summary["relayed"] += res["relayed"]
        summary["duplicates"] += res["duplicates"]
        summary["dropped"] += res.get("dropped", 0)
        summary["results"].append(res)
    log.info("outbox relay: %d live -> pulled %d / new %d / dup %d / dropped %d / skipped %d",
             summary["instances"], summary["pulled"], summary["relayed"],
             summary["duplicates"], summary["dropped"], summary["skipped"])
    return summary


def _build_live_store(env: dict[str, str]):
    from management.api.store import build_store  # lazy: importing this module for tests won't pull it

    return build_store(env)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Host outbox relay: replica outbox -> parent journal (HOST-ONLY, SSRF-safe)")
    parser.add_argument("--once", action="store_true", required=True,
                        help="Relay every live replica's outbox once, then exit.")
    parser.add_argument("--registry", type=Path, default=None)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--page", type=int, default=DEFAULT_PAGE)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    env = dict(os.environ)
    registry_path = args.registry or default_registry_path(env=env)
    registry = FleetRegistryStore(registry_path)
    store = _build_live_store(env)
    summary = run_once(registry, store, env=env, timeout_s=args.timeout, page=args.page)
    print(json.dumps({"ok": True, **summary}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
