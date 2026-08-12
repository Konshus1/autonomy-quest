#!/usr/bin/env python3
"""Host-side tbagents BRIDGE: mirror selected, REDACTED AQ channels -> tbagents (#4834 comms Phase 5,
design §5 Option E / §10 Phase 5). DEFAULT-OFF, ONE-WAY, REDACTED, HOST-ONLY. An inert tool.

This is the OPTIONAL operator-view bridge from the design's Option E: it gives Kevin a cross-system
view of AQ activity inside tbagents WITHOUT making tbagents an AQ authority/transport. It is:

  * DEFAULT-OFF. ``bridge_enabled()`` is False unless ``AQ_TBAGENTS_BRIDGE`` is explicitly truthy AND a
    tbagents service token is present. Import + a dry run never contact tbagents. It is not wired to
    any daemon or entrypoint — an operator runs it deliberately.
  * ONE-WAY. It only READS the parent journal and POSTs a redacted mirror into tbagents. It NEVER
    writes back into AQ, never accepts a tbagents message as input, and reaches no AQ actuator.
  * REDACTED (reusing the Phase-1 redaction discipline). It strips secrets/tokens/credentials/DSNs,
    host-local ports, URLs, and filesystem paths from every mirrored envelope; artifact references
    stay digest-only. Only an allowlist of operator-view channels/kinds is mirrored — the command
    plane (work.request/response/receipt, the inbox) is NEVER mirrored.
  * HOST-ONLY (import-firewall). It imports ``ralph_portable.fleet_registry`` (host-owned; a FORBIDDEN
    import for any guest-reachable module) to read which lineages are live, exactly like the Phase-1/2
    host relays. ``tests/test_import_firewall.py`` proves it stays outside the guest import closure, so
    guest-reachable code can never import or drive it.

The redaction/selection CORE is pure and unit-tested; the network send is a thin, default-off wrapper.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# HOST-ONLY marker (import-firewall): importing the host-owned registry keeps this file on the host
# side of the wall, exactly like host_outbox_relay / host_fleet_poller. A guest can never import it.
from ralph_portable.fleet_registry import (  # noqa: E402  (HOST-ONLY — forbidden to guests)
    FleetRegistryStore,
    default_registry_path,
)

log = logging.getLogger("aq.comms.tbagents_bridge")

# ---------------------------------------------------------------------------
# What may be mirrored (operator-view only). The command plane is deliberately EXCLUDED.
# ---------------------------------------------------------------------------
MIRRORABLE_KINDS = frozenset(
    {"status.report", "experiment.progress", "experiment.result", "health.observed", "operator.message"})
# work.request / work.response / receipt (the parent->replica command plane) are NEVER mirrored.
EXCLUDED_KINDS = frozenset({"work.request", "work.response", "receipt"})

MAX_MIRRORED_TEXT = 2000

# ---------------------------------------------------------------------------
# Redaction (Phase-1 discipline): strip anything that could leak a secret, a host-local port, a URL,
# or a filesystem path. Fail toward dropping, never toward leaking.
# ---------------------------------------------------------------------------
_SENSITIVE_KEY_RE = re.compile(
    r"(token|secret|password|passwd|credential|api[_-]?key|authorization|bearer|dsn|"
    r"private[_-]?key|session|cookie|port)", re.IGNORECASE)

_URL_RE = re.compile(r"\b[a-z][a-z0-9+.\-]*://\S+", re.IGNORECASE)          # any scheme://...
_HOSTPORT_RE = re.compile(r"\b(?:127\.0\.0\.1|localhost|0\.0\.0\.0|host\.docker\.internal):\d+")
_ABSPATH_RE = re.compile(r"(?:^|\s)(/[^\s/][^\s]*)")                        # /abs/paths
_BEARER_RE = re.compile(r"\bbearer\s+\S+", re.IGNORECASE)
# A long opaque token-ish run (>= 24 chars of base64/hex-ish) that is NOT a sha256 digest reference.
_TOKENISH_RE = re.compile(r"\b(?!sha256:)[A-Za-z0-9_\-]{24,}\b")


_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")


def scrub_text(value: str, *, limit: int = MAX_MIRRORED_TEXT) -> str:
    """Redact a free-text field: drop URLs, host-local host:port, absolute paths, bearer tokens, and
    long opaque token-like runs. sha256 digests are PRESERVED (safe evidence references): they are
    stashed behind placeholders before token-scrubbing and restored after."""
    if not isinstance(value, str):
        return ""
    # Protect sha256 digests from the tokenish scrubber.
    digests: list[str] = []

    def _stash(m: "re.Match[str]") -> str:
        digests.append(m.group(0))
        return f"\x00DIGEST{len(digests) - 1}\x00"

    out = _DIGEST_RE.sub(_stash, value)
    out = _BEARER_RE.sub("[redacted-bearer]", out)
    out = _URL_RE.sub("[redacted-url]", out)
    out = _HOSTPORT_RE.sub("[redacted-host-port]", out)
    out = _TOKENISH_RE.sub("[redacted-token]", out)
    out = _ABSPATH_RE.sub(" [redacted-path]", out)
    for i, d in enumerate(digests):
        out = out.replace(f"\x00DIGEST{i}\x00", d)
    if len(out) > limit:
        out = out[:limit] + "…"
    return out


def scrub_value(value: Any) -> Any:
    """Recursively redact a JSON value: sensitive keys dropped, strings scrubbed, structure bounded."""
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str) or _SENSITIVE_KEY_RE.search(k):
                # Drop a sensitive key entirely (do not even mirror a redacted placeholder value).
                continue
            clean[k] = scrub_value(v)
        return clean
    if isinstance(value, list):
        return [scrub_value(v) for v in value[:64]]
    if isinstance(value, str):
        return scrub_text(value)
    return value


def is_mirrorable(envelope: dict[str, Any]) -> bool:
    """True iff this envelope belongs to the operator-view mirror set: an allowed kind, NOT on the
    command plane, and NOT an inbox channel (the command intake surface is never mirrored)."""
    kind = envelope.get("kind")
    if kind in EXCLUDED_KINDS or kind not in MIRRORABLE_KINDS:
        return False
    channel = envelope.get("channel") or ""
    if channel.endswith("/inbox") or "/inbox/" in channel:
        return False
    return True


def redact_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    """Project an AQ envelope into a REDACTED, one-way operator-view mirror record for tbagents.

    Keeps only non-sensitive fields; scrubs free text; drops any secret/token/port/url/path. Artifact
    references remain digest-only (already validated upstream). It carries an explicit note that this
    is an untrusted, non-authoritative mirror."""
    payload = envelope.get("payload") or {}
    return {
        "aq_envelope_id": envelope.get("id"),
        "channel": envelope.get("channel"),
        "kind": envelope.get("kind"),
        "trust": envelope.get("trust"),
        "origin_instance_id": envelope.get("origin_instance_id"),
        "created_at": envelope.get("created_at"),
        # Redacted, bounded payload projection (no secrets/ports/urls/paths).
        "payload": scrub_value(payload),
        "mirror_note": (
            "One-way redacted mirror of an AQ channel for operator visibility. NOT authoritative: an "
            "AQ authority state is never changed by this mirror, and tbagents is not an AQ transport."
        ),
    }


def select_mirror(envelopes: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """The pure core: filter to the operator-view mirror set and redact each. No I/O, no network."""
    return [redact_envelope(e) for e in envelopes if is_mirrorable(e)]


def bridge_enabled(env: dict[str, str] | None = None) -> bool:
    """True ONLY when the operator EXPLICITLY enabled the bridge AND a tbagents token is configured.

    Land-inert / default-off: unset / "0" / "false" / "" => the bridge never contacts tbagents. Even a
    truthy flag without ``INTERNAL_SERVICE_TOKEN`` stays off (fail-closed: no token, no mirror)."""
    source = env if env is not None else os.environ
    raw = (source.get("AQ_TBAGENTS_BRIDGE") or "").strip().lower()
    has_token = bool((source.get("INTERNAL_SERVICE_TOKEN") or "").strip())
    return raw in {"1", "true", "yes", "on"} and has_token


def _read_journal(path: str | None) -> list[dict[str, Any]]:
    """Read a parent-journal export (a JSON list of envelopes) for a dry run. The bridge NEVER writes
    to AQ — it only reads this snapshot. A missing path yields an empty mirror."""
    if not path:
        return []
    p = Path(path)
    if not p.is_file():
        return []
    data = json.loads(p.read_text())
    return data.get("items", data) if isinstance(data, dict) else list(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Host-only, default-off, one-way redacted tbagents bridge.")
    parser.add_argument("--journal", help="path to a JSON export of the parent journal (envelopes)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the redacted mirror; never contact tbagents (the safe default)")
    parser.add_argument("--registry", default=None, help="host fleet registry path (host-only)")
    args = parser.parse_args(argv)

    envelopes = _read_journal(args.journal)
    mirror = select_mirror(envelopes)

    # The registry read proves this is a host-only tool (reads live lineages); it never drives AQ.
    reg_path = args.registry or default_registry_path()
    try:
        live = FleetRegistryStore(reg_path).live() if Path(reg_path).exists() else []
    except Exception:
        live = []

    if args.dry_run or not bridge_enabled():
        print(json.dumps({
            "enabled": bridge_enabled(),
            "dry_run": True,
            "live_lineages": len(live),
            "mirrored": len(mirror),
            "sample": mirror[:5],
        }, indent=2))
        return 0

    # ONE-WAY send (only reached when explicitly enabled + token present). Kept minimal and honest.
    token = os.environ["INTERNAL_SERVICE_TOKEN"]
    url = os.environ.get("AQ_TBAGENTS_URL", "http://localhost:8000/api/agents/messages")
    import urllib.request

    sent = 0
    for rec in mirror:
        body = json.dumps({
            "channel": "aq-fleet-mirror",
            "sender_name": "aq-tbagents-bridge",
            "message_type": "status",
            "importance": "low",
            "payload": rec,
        }).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Content-Type": "application/json", "X-Internal-Service-Token": token})
        try:
            urllib.request.urlopen(req, timeout=5).read()
            sent += 1
        except Exception as exc:  # one-way + best-effort: a mirror failure never affects AQ
            log.warning("tbagents mirror send failed for %s: %s", rec.get("aq_envelope_id"), exc)
    print(json.dumps({"enabled": True, "mirrored": len(mirror), "sent": sent}))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
