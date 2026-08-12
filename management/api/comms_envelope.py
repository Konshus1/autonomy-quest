"""Versioned agent-comms message envelope (#4834 comms Phase 0, design §4.1).

This is the append-only, versioned envelope that replaces the opaque ``ralph_comms`` demo
payload. It is guest-reachable *schema only*: it validates shape, enforces the allowlisted
``kind`` enum, and clamps payload/text size. It grants NO capability — it never touches Docker,
the fleet registry, a credential store, or a replication gate. Identity is NOT derived here;
the API layer derives ``origin_instance_id`` + ``principal_id`` from the authenticated
credential and passes them in. Any identity fields a caller tries to claim in the body are
dropped by the API before ``build_envelope`` is ever called.

Envelope (design §4.1)::

    {
      "id": "01J...",                      # ULID-like, sortable, server-minted
      "origin_instance_id": "urn:uuid:...",# server-derived from the credential
      "principal_id": "instance:.../agent:.../session:...",  # server-derived
      "channel": "lineage/urn:uuid:parent/experiments",
      "target": {"instance_id": "urn:uuid:...", "handle": "parent"},
      "kind": "experiment.result",         # allowlisted enum
      "payload": {...},                    # versioned per kind, size-bounded
      "correlation_id": "...", "in_reply_to": null,
      "idempotency_key": "...",
      "created_at": "...Z", "expires_at": null,
      "trust": "untrusted_claim",          # or host_observed
      "delivery": "accepted"               # transport state, never "executed/true"
    }
"""

from __future__ import annotations

import json
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Any

# The current envelope schema version. Bump when the envelope shape changes; per-kind payload
# schemas are versioned inside the payload by their producers.
ENVELOPE_VERSION = 1

# Allowlisted message kinds (design §4.1). A kind outside this set is rejected — there is
# deliberately NO generic ``command``/``shell`` kind (design §8.4): the comms plane carries
# proposals and evidence, never executable authority.
ALLOWED_KINDS = frozenset(
    {
        "health.observed",
        "status.report",
        "experiment.progress",
        "experiment.result",
        "work.request",
        "work.response",
        "receipt",
        "operator.message",
    }
)

# Transport-only delivery states (design §4.1). NONE of these is ever evidence that a request
# was executed or succeeded — that requires a separately correlated, independently verified result.
DELIVERY_STATES = frozenset({"accepted", "relayed", "delivered", "read", "rejected", "expired"})

# Trust classes. ``untrusted_claim`` is the default for anything a principal asserts; only the
# host may stamp ``host_observed`` (e.g. a health poll it performed itself).
TRUST_CLASSES = frozenset({"untrusted_claim", "host_observed"})

# Input hardening (design §8.5). Strict maximums so a single message cannot exhaust storage.
MAX_PAYLOAD_BYTES = 64 * 1024
MAX_TEXT_BYTES = 16 * 1024
MAX_CHANNEL_LEN = 512
MAX_HANDLE_LEN = 256
MAX_ID_LEN = 256

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class EnvelopeError(ValueError):
    """A message envelope failed validation (bad kind, oversize payload, malformed field)."""


def new_ulid(*, now_ms: int | None = None) -> str:
    """A ULID-like sortable id: 48-bit millisecond timestamp + 80 bits of randomness.

    Lexicographic order matches creation order (to the millisecond), so consumers can use it as a
    monotonic watermark without a separate serial. Crockford base32, 26 chars — same alphabet and
    length as a real ULID, generated without an external dependency.
    """
    ms = now_ms if now_ms is not None else int(time.time() * 1000)
    rand = secrets.randbits(80)
    value = (ms << 80) | rand
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Max nesting depth for a payload (design §8.5 input hardening). Deeply-nested JSON can drive
# ``json.dumps`` (and any recursive walk) into a RecursionError; reject it up front rather than crash.
MAX_PAYLOAD_DEPTH = 32


def _assert_bounded_depth(value: Any, *, depth: int = 0) -> None:
    if depth > MAX_PAYLOAD_DEPTH:
        raise EnvelopeError(f"payload nesting exceeds max depth {MAX_PAYLOAD_DEPTH}")
    if isinstance(value, dict):
        for v in value.values():
            _assert_bounded_depth(v, depth=depth + 1)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _assert_bounded_depth(v, depth=depth + 1)


def _payload_size(payload: Any) -> int:
    # Strict (no ``default=``): a payload that is not JSON-native is rejected, not silently
    # coerced — the comms plane carries structured JSON, and lenient coercion would let a
    # non-serializable object smuggle past the size/shape contract (design §8.5).
    # Depth-guard FIRST (design §8.5): a deeply-nested payload must be rejected before it can drive
    # json.dumps into a RecursionError (which would otherwise surface as a 500).
    _assert_bounded_depth(payload)
    try:
        return len(json.dumps(payload).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise EnvelopeError(f"payload is not JSON-serializable: {exc}") from exc
    except RecursionError as exc:
        raise EnvelopeError("payload nesting too deep to serialize") from exc


def build_envelope(
    *,
    origin_instance_id: str,
    principal_id: str,
    channel: str,
    kind: str,
    payload: dict[str, Any] | None = None,
    target_instance_id: str | None = None,
    target_handle: str | None = None,
    correlation_id: str | None = None,
    in_reply_to: str | None = None,
    idempotency_key: str | None = None,
    expires_at: str | None = None,
    trust: str = "untrusted_claim",
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Construct a validated envelope dict. ``origin_instance_id``/``principal_id`` MUST already
    be server-derived from the authenticated credential — this function does not authenticate.

    Raises ``EnvelopeError`` on a disallowed kind, oversize payload/text, or malformed field.
    """
    if not origin_instance_id or not isinstance(origin_instance_id, str):
        raise EnvelopeError("origin_instance_id (server-derived) is required")
    if not principal_id or not isinstance(principal_id, str):
        raise EnvelopeError("principal_id (server-derived) is required")

    if kind not in ALLOWED_KINDS:
        raise EnvelopeError(
            f"kind {kind!r} is not allowlisted; must be one of {sorted(ALLOWED_KINDS)}"
        )
    if trust not in TRUST_CLASSES:
        raise EnvelopeError(f"trust {trust!r} invalid; must be one of {sorted(TRUST_CLASSES)}")

    if not channel or not isinstance(channel, str):
        raise EnvelopeError("channel is required")
    if len(channel) > MAX_CHANNEL_LEN:
        raise EnvelopeError(f"channel exceeds {MAX_CHANNEL_LEN} chars")

    for label, value, limit in (
        ("target_handle", target_handle, MAX_HANDLE_LEN),
        ("target_instance_id", target_instance_id, MAX_ID_LEN),
        ("correlation_id", correlation_id, MAX_ID_LEN),
        ("in_reply_to", in_reply_to, MAX_ID_LEN),
        ("idempotency_key", idempotency_key, MAX_ID_LEN),
    ):
        if value is not None:
            if not isinstance(value, str):
                raise EnvelopeError(f"{label} must be a string")
            if len(value) > limit:
                raise EnvelopeError(f"{label} exceeds {limit} chars")

    payload = payload if payload is not None else {}
    if not isinstance(payload, dict):
        raise EnvelopeError("payload must be a JSON object")
    size = _payload_size(payload)
    if size > MAX_PAYLOAD_BYTES:
        raise EnvelopeError(f"payload {size} bytes exceeds limit {MAX_PAYLOAD_BYTES}")
    text = payload.get("text")
    if isinstance(text, str) and len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise EnvelopeError(f"payload.text exceeds {MAX_TEXT_BYTES} bytes")

    return {
        "id": new_ulid(now_ms=now_ms),
        "envelope_version": ENVELOPE_VERSION,
        "origin_instance_id": origin_instance_id,
        "principal_id": principal_id,
        "channel": channel,
        "target": {"instance_id": target_instance_id, "handle": target_handle},
        "kind": kind,
        "payload": payload,
        "correlation_id": correlation_id,
        "in_reply_to": in_reply_to,
        "idempotency_key": idempotency_key,
        "created_at": _utc_now_iso(),
        "expires_at": expires_at,
        "trust": trust,
        "delivery": "accepted",
    }


def legacy_projection(envelope: dict[str, Any], *, row_id: str) -> dict[str, Any]:
    """Project an envelope down to the legacy ``ralph_comms`` row shape the React poll expects.

    The old shape is ``{id, from_handle, to_handle, text, kind, ts}``. ``from_handle`` is the
    SERVER-DERIVED principal (never a caller-claimed handle), ``to_handle`` is the target handle,
    ``text`` is ``payload.text`` when present. This keeps GET /api/agent-comms unbroken while the
    stored record is now a full versioned envelope.
    """
    payload = envelope.get("payload") or {}
    target = envelope.get("target") or {}
    text = payload.get("text")
    if not isinstance(text, str):
        # Non-text payloads still render as a row: show a compact JSON summary, never crash the UI.
        text = json.dumps(payload, default=str)[:MAX_TEXT_BYTES]
    return {
        "id": row_id,
        "from_handle": envelope.get("principal_id"),
        "to_handle": target.get("handle") or target.get("instance_id"),
        "text": text,
        "kind": envelope.get("kind"),
        "ts": envelope.get("created_at"),
        # Trust class rides on the row so the UI can badge host_observed ground truth vs an
        # untrusted replica claim without a second fetch (design §9.4).
        "trust": envelope.get("trust"),
    }


def is_expired(envelope: dict[str, Any], *, now: datetime | None = None) -> bool:
    """True when the envelope's ``expires_at`` is in the past (design §8.5 expiry).

    Used by the host relay to DROP a claim that expired before it could be pulled (a dropped message
    is never copied into the parent journal) and by consumers to filter stale claims. A malformed or
    absent ``expires_at`` is treated as non-expiring (never accidentally drops a valid message)."""
    raw = envelope.get("expires_at")
    if not raw or not isinstance(raw, str):
        return False
    try:
        exp = datetime.fromisoformat(raw)
    except ValueError:
        return False
    now = now or datetime.now(timezone.utc)
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return exp < now


def durability_required(env: dict[str, str] | None = None) -> bool:
    """True when a durable DB is configured — so a comms write MUST NOT fall back to memory.

    When no DB URL is set at all (pure local dev / pytest), in-memory is the honest intended
    backing and writes are allowed. When a DB URL IS set, the write path must fail closed (503)
    rather than acknowledge from an ephemeral in-memory fallback (design §2.2 / §8.5).
    """
    source = env if env is not None else os.environ
    return bool(source.get("AQ_MGMT_DB_URL") or source.get("AQ_DB_URL"))
