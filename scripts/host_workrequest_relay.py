#!/usr/bin/env python3
"""Host-side WORK REQUEST relay: parent/operator -> replica inbox (#4834 comms Phase 3, design §10 Phase 3).

THE FIRST parent->replica COMMAND DIRECTION, and an INERT TOOL: this script is NOT wired to any daemon
or the live loop. An operator runs it by hand to POST ONE authenticated ``work.request`` (or a cancel-
as-request) into a live replica's loopback management inbox. Nothing here runs automatically.

Direction is REVERSED from Phase 1/2 (which were read-only GET pulls), but the SSRF/safety discipline is
the SAME rigor:

  * TARGET FROM THE REGISTRY ONLY. The replica + its app_mgmt port come ONLY from ``registry.live()`` —
    never from a body, a proposal row, or a caller-supplied host. The only URL ever built is
    ``http://127.0.0.1:<registry app_mgmt port>/api/agent-comms/inbox``.
  * LOOPBACK-ONLY + EXACT ROUTE, re-asserted before AND after the request (``_assert_loopback_inbox_url``).
  * REFUSE REDIRECTS. A replica controls its own responses; auto-following a 3xx would turn this into an
    arbitrary-outbound-POST primitive (other ports, cloud metadata, side-effecting routes). A healthy
    inbox returns 200, never a redirect, so refusing to follow is both correct and safe.
  * SCOPED HOST CREDENTIAL. The relay presents the scoped inbox-deliver token (distinct from the
    outbox-READ token), which the replica derives as the PARENT principal. The relay never sends a
    capability, a replication override, or the host bus-admin token.
  * BOUNDED. Bounded timeout + a hard request-body size cap; the payload is a CLEAN, schema-validated
    work.request (goal + optional priority/constraints/cancel_of) — no shell/replication/capability
    fields exist in the schema to send.

HOST-ONLY (import-firewall proof): it imports ``ralph_portable.fleet_registry`` (host-owned, a FORBIDDEN
import for any guest-reachable module), exactly like the Phase-1 poller and Phase-2 relay. The import
firewall test proves this import closure reaches the registry while the guest closure never does — so a
guest/replica can never import this relay or the registry to POST itself work.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.request
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

log = logging.getLogger("aq.comms.workrequest-relay")

DEFAULT_TIMEOUT_S = float(os.environ.get("AQ_WORKREQ_TIMEOUT_S", "5"))
# Hard request-body cap so a runaway payload can never be POSTed at a replica.
MAX_REQUEST_BODY_BYTES = 64 * 1024

INBOX_ROUTE = "/api/agent-comms/inbox"


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse to FOLLOW any 3xx on the inbox POST (SSRF discipline shared with the Phase-1/2 pollers)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler)


def _assert_loopback_inbox_url(url: str) -> None:
    """Fail closed unless ``url`` is exactly a loopback ``/api/agent-comms/inbox`` POST (SSRF + route guard)."""
    parsed = urlparse(url)
    if parsed.scheme != "http":
        raise ValueError(f"refusing non-http work-request relay url {url!r}")
    if parsed.hostname not in ("127.0.0.1", "localhost"):
        raise ValueError(f"refusing non-loopback work-request relay host {parsed.hostname!r} (SSRF guard)")
    if parsed.path != INBOX_ROUTE:
        raise ValueError(f"refusing non-inbox work-request relay path {parsed.path!r} (exact route only)")
    if parsed.query or parsed.params:
        raise ValueError("refusing work-request relay url with query/params (exact inbox route only)")


def _default_post(url: str, *, token: str, body: bytes, timeout_s: float) -> bytes:
    req = urllib.request.Request(
        url, method="POST", data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with _NO_REDIRECT_OPENER.open(req, timeout=timeout_s) as resp:  # noqa: S310 (loopback inbox)
        # Defense in depth: even a non-redirecting response must have stayed on the exact loopback
        # inbox URL we requested. If any handler ever lands us elsewhere, refuse to trust the outcome.
        _assert_loopback_inbox_url(resp.geturl())
        return resp.read()


def build_inbox_body(
    *, goal: str, target_instance_id: str, priority: str | None = None,
    constraints: str | None = None, cancel_of: str | None = None,
    correlation_id: str | None = None, idempotency_key: str | None = None,
    expires_at: str | None = None,
) -> dict[str, Any]:
    """Build a CLEAN, schema-validated work.request body (host-side). ``build_work_request`` bounds the
    fields and there is no schema slot for a shell/replication/capability field — the replica re-derives
    identity + re-validates anyway (both ingestion points guard independently)."""
    from management.api.comms_workrequest import build_work_request

    payload = build_work_request(goal=goal, priority=priority, constraints=constraints,
                                 cancel_of=cancel_of)
    return {
        "kind": "work.request",
        "payload": payload,
        "target": {"instance_id": target_instance_id, "handle": "replica"},
        "correlation_id": correlation_id,
        "idempotency_key": idempotency_key,
        "expires_at": expires_at,
    }


def _token_for(instance_id: str, env: dict[str, str]) -> str | None:
    """Resolve the scoped inbox-deliver token the host presents to a replica (host config ONLY).

    ``AQ_COMMS_INBOX_RELAY_TOKENS`` JSON ``{instance_id: token}``, else a single ``AQ_COMMS_INBOX_TOKEN``.
    Never sourced from a body/proposal. No token => no POST (fail-closed)."""
    raw = (env.get("AQ_COMMS_INBOX_RELAY_TOKENS") or "").strip()
    if raw:
        try:
            mapping = json.loads(raw)
            if isinstance(mapping, dict) and mapping.get(instance_id):
                return str(mapping[instance_id])
        except ValueError:
            pass
    return (env.get("AQ_COMMS_INBOX_TOKEN") or "").strip() or None


def post_workrequest(
    entry: dict[str, Any], body: dict[str, Any], *, token: str,
    timeout_s: float = DEFAULT_TIMEOUT_S, post: Callable[..., bytes] | None = None,
) -> dict[str, Any]:
    """POST one work.request to ONE registry entry's inbox. The URL is built ONLY from the registry
    app_mgmt port on loopback — the entry's ``health_url`` and any body-supplied host are ignored.

    Returns ``{instance_id, posted, status}``. Raises ``ValueError`` on an SSRF/route violation or an
    oversize body (the guard fails closed before any network call)."""
    instance_id = str(entry.get("instance_id") or "unknown")
    ports = entry.get("ports") or {}
    port = ports.get("app_mgmt")
    if port is None:
        return {"instance_id": instance_id, "posted": False, "reason": "no app_mgmt port"}

    raw = json.dumps(body).encode("utf-8")
    if len(raw) > MAX_REQUEST_BODY_BYTES:
        raise ValueError(f"work.request body {len(raw)} bytes exceeds cap {MAX_REQUEST_BODY_BYTES}")

    url = f"http://127.0.0.1:{int(port)}{INBOX_ROUTE}"
    _assert_loopback_inbox_url(url)  # SSRF + exact-route guard, checked every call
    do_post = post or _default_post

    try:
        resp = do_post(url, token=token, body=raw, timeout_s=timeout_s)
    except (urllib.error.HTTPError, urllib.error.URLError, ConnectionError, OSError, TimeoutError) as exc:
        log.info("work-request POST skip %s: inbox unreachable/refused (%s)", instance_id, exc)
        return {"instance_id": instance_id, "posted": False, "reason": f"unreachable: {exc}"}

    try:
        parsed = json.loads(resp)
    except (ValueError, json.JSONDecodeError):
        parsed = {}
    log.info("work-request delivered to %s (stored_inert=%s)", instance_id,
             parsed.get("stored_inert"))
    return {"instance_id": instance_id, "posted": True, "response": parsed}


def deliver_to_instance(
    registry: FleetRegistryStore, target_instance_id: str, body: dict[str, Any], *,
    env: dict[str, str] | None = None, timeout_s: float = DEFAULT_TIMEOUT_S,
    post: Callable[..., bytes] | None = None,
) -> dict[str, Any]:
    """Look up a LIVE replica by instance id in the registry and POST one work.request to its inbox.

    Fail-closed: an instance not in ``registry.live()`` (unknown / torn-down) or with no scoped inbox
    token is refused — never POSTed to a guessed port/host."""
    env = dict(env if env is not None else os.environ)
    entry = next((e for e in registry.live() if e.get("instance_id") == target_instance_id), None)
    if entry is None:
        return {"instance_id": target_instance_id, "posted": False,
                "reason": "not a live registry instance"}
    token = _token_for(target_instance_id, env)
    if not token:
        return {"instance_id": target_instance_id, "posted": False,
                "reason": "no scoped inbox-deliver token configured"}
    return post_workrequest(entry, body, token=token, timeout_s=timeout_s, post=post)


def _build_live_store(env: dict[str, str]):  # pragma: no cover - live wiring only
    from management.api.store import build_store

    return build_store(env)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - inert CLI tool
    parser = argparse.ArgumentParser(
        description="Host->replica work.request relay (HOST-ONLY, SSRF-safe, INERT TOOL). "
                    "Operator-run; not wired to any daemon or the live loop.")
    parser.add_argument("--instance-id", required=True, help="Target replica instance id (must be live in the registry).")
    parser.add_argument("--goal", required=True, help="Bounded free-text goal for the work.request.")
    parser.add_argument("--priority", choices=["low", "normal", "high"], default=None)
    parser.add_argument("--constraints", default=None, help="Optional advisory constraints text.")
    parser.add_argument("--cancel-of", default=None,
                        help="Correlation id to CANCEL (cancel-as-request; never a process signal).")
    parser.add_argument("--correlation-id", default=None)
    parser.add_argument("--idempotency-key", default=None)
    parser.add_argument("--expires-at", default=None, help="ISO-8601 expiry; an expired request is refused by the replica.")
    parser.add_argument("--registry", type=Path, default=None)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--yes", action="store_true",
                        help="Required acknowledgement that this POSTs a real command to a live replica.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if not args.yes:
        print("Refusing to POST without --yes (this delivers a real work.request to a live replica "
              "inbox; it lands INERT there but is still the first parent->replica command).")
        return 2

    env = dict(os.environ)
    registry = FleetRegistryStore(args.registry or default_registry_path(env=env))
    body = build_inbox_body(
        goal=args.goal, target_instance_id=args.instance_id, priority=args.priority,
        constraints=args.constraints, cancel_of=args.cancel_of, correlation_id=args.correlation_id,
        idempotency_key=args.idempotency_key, expires_at=args.expires_at)
    result = deliver_to_instance(registry, args.instance_id, body, env=env, timeout_s=args.timeout)
    print(json.dumps({"ok": bool(result.get("posted")), **result}, indent=2, default=str))
    return 0 if result.get("posted") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
