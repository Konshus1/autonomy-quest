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

    pulled = relayed = duplicates = dropped = rejected = 0
    max_seq = cursor
    for row in items:
        if not isinstance(row, dict):
            continue
        seq = row.get("seq")
        env = row.get("envelope")
        if not isinstance(env, dict) or "id" not in env:
            continue
        # CURSOR-POISON GUARD (seq is REPLICA-CONTROLLED): this pull requested `after_seq=cursor` with
        # `limit=page`, and the outbox seq is a per-replica append-only monotonic counter, so a
        # legitimate page cannot contain a seq outside (cursor, cursor+page]. Reject anything beyond
        # that window WITHOUT advancing max_seq, so a hostile outbox cannot claim a huge seq (e.g.
        # 10**18) and permanently skip ALL genuine future messages. A benign near-boundary gap just
        # gets re-pulled next cycle (dedup-on-id makes re-relay safe), so this never loses a message.
        if not isinstance(seq, int) or seq <= cursor or seq > cursor + int(page):
            rejected += 1
            log.warning("relay %s: outbox row seq %r outside (%d, %d]; rejected (cursor-poison guard)",
                        instance_id, seq, cursor, cursor + int(page))
            continue
        pulled += 1
        # A claim that EXPIRED before the host could pull it is DROPPED — never copied into the
        # parent journal — but the cursor still advances past it so it is not re-pulled forever.
        if _is_expired(env):
            dropped += 1
            if isinstance(seq, int):
                max_seq = max(max_seq, seq)
            continue
        # Treat the outbox body as FULLY UNTRUSTED INPUT: force untrusted_claim, re-derive identity,
        # re-validate the payload + constrain kind. A body that fails is REJECTED (dropped) — never
        # copied — while the cursor still advances so a hostile item cannot wedge the relay.
        stamped = _sanitize_and_stamp(env, source_instance_id=instance_id, relayed_at=relayed_at)
        if stamped is None:
            rejected += 1
            if isinstance(seq, int):
                max_seq = max(max_seq, seq)
            continue
        result = store.relay_envelope(stamped)
        if result.get("duplicate"):
            duplicates += 1
        else:
            relayed += 1
        if isinstance(seq, int):
            max_seq = max(max_seq, seq)

    # CONTIGUITY CAP (defence-in-depth on the window guard): advance the cursor by AT MOST the number
    # of in-window rows we actually PROCESSED (``pulled``), never past the highest seq seen. Without
    # this, a hostile response containing only a single high in-window row (e.g. one row at
    # seq=cursor+page) would still pass the (cursor, cursor+page] window guard and advance the cursor
    # over all the OMITTED lower seqs, permanently skipping them. Capping the advance by the processed
    # count means the cursor can never skip more messages than were actually delivered; a genuine
    # (small) append-only-seq gap simply gets re-pulled next cycle (dedup-on-id makes re-relay safe),
    # so no message is lost and a replica cannot make the host skip its own un-relayed messages.
    advanced = min(max_seq, cursor + pulled)
    if advanced > cursor:
        registry.record_relay_cursor(instance_id, cursor=advanced, relayed_at=relayed_at,
                                     relayed_count=relayed)
    log.info("relayed %s: pulled=%d new=%d dup=%d dropped=%d rejected=%d cursor %d->%d",
             instance_id, pulled, relayed, duplicates, dropped, rejected, cursor, advanced)
    return {"instance_id": instance_id, "pulled": pulled, "relayed": relayed,
            "duplicates": duplicates, "dropped": dropped, "rejected": rejected, "cursor": advanced}


def _sanitize_and_stamp(env: dict[str, Any], *, source_instance_id: str,
                        relayed_at: str) -> dict[str, Any] | None:
    """Sanitize a replica-controlled outbox body into a safe parent-journal copy, or ``None`` to DROP.

    THE TRUST BOUNDARY THAT MATTERS (de-correlated safety review, blocking): the adversary speaks to
    the RELAY, and the /outbox body is FULLY UNTRUSTED INPUT — a malicious replica can put anything in
    it (``trust=host_observed``, ``kind=health.observed``, ``principal_id=host:fleet-poller``, a
    ``url`` artifact ref). ALL of the publish-path hardening lives in the replica's OWN API and does
    NOT protect this path, so the relay MUST re-apply it here, exactly like a fresh publish:

      1. FORCE ``trust=untrusted_claim`` — a relay can NEVER carry host_observed from a replica body
         (host_observed is minted ONLY by the host poller, never relayed). This is what closes the
         fleet-health mint exploit: fleet_from_envelopes only trusts host_observed, so a forced-down
         claim can never move fleet health/topology.
      2. RE-DERIVE identity from the registry instance the host is actually polling — discard the
         body's ``origin_instance_id``/``principal_id`` so a replica cannot author-as another instance,
         the operator, or the host poller in the journal.
      3. CONSTRAIN kind to the replica-emit allowlist and RE-VALIDATE the payload through the same
         per-kind schema as publish (``validate_replica_payload``) — so a non-emit kind or a non-digest
         (url/path) artifact ref is DROPPED and never reaches the journal.

    Returns the sanitized copy, or ``None`` when the body must be dropped (caller advances the cursor
    past it and logs — a bad body must not wedge or crash the relay). The immutable global ``id`` and
    the relay provenance block are preserved so re-relay still dedups and the audit trail is intact.
    """
    from management.api.comms_envelope import EnvelopeError
    from management.api.comms_payloads import EMITTABLE_KINDS, validate_replica_payload

    kind = env.get("kind")
    if kind not in EMITTABLE_KINDS:
        log.warning("relay drop %s: replica outbox body carried non-emit kind %r",
                    source_instance_id, kind)
        return None
    payload = env.get("payload")
    if not isinstance(payload, dict):
        return None
    try:
        clean_payload = validate_replica_payload(kind, payload)
    except (EnvelopeError, ValueError, TypeError) as exc:
        log.warning("relay drop %s: replica payload failed re-validation (%s)", source_instance_id, exc)
        return None

    copy = dict(env)
    # 1. FORCE untrusted_claim — never carry a replica-supplied host_observed.
    copy["trust"] = "untrusted_claim"
    # 2. RE-DERIVE identity from the polled instance; discard the body's spoofed claims.
    copy["origin_instance_id"] = source_instance_id
    copy["principal_id"] = f"instance:{source_instance_id}"
    # 3. Use the RE-VALIDATED payload (url/path artifact refs already rejected above).
    copy["payload"] = clean_payload
    copy["kind"] = kind
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
                               "duplicates": 0, "dropped": 0, "rejected": 0, "skipped": 0,
                               "results": []}
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
        summary["rejected"] += res.get("rejected", 0)
        summary["results"].append(res)
    log.info("outbox relay: %d live -> pulled %d / new %d / dup %d / dropped %d / rejected %d / skipped %d",
             summary["instances"], summary["pulled"], summary["relayed"],
             summary["duplicates"], summary["dropped"], summary["rejected"], summary["skipped"])
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
