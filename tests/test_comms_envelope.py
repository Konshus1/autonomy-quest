"""Schema/contract + input-hardening tests for the versioned comms envelope (#4834 comms Phase 0).

Covers design §4.1 (envelope shape, allowlisted kinds, transport-only delivery) and §8.5 (payload
size limits, fuzz) at the pure-schema layer, plus the store's idempotency suppression.
"""

from __future__ import annotations

import pytest

from management.api.comms_envelope import (
    ALLOWED_KINDS,
    DELIVERY_STATES,
    MAX_PAYLOAD_BYTES,
    MAX_TEXT_BYTES,
    EnvelopeError,
    build_envelope,
    legacy_projection,
    new_ulid,
)
from management.api.store import InMemoryStore


def _env(**over):
    base = dict(
        origin_instance_id="urn:uuid:self", principal_id="instance:urn:uuid:self",
        channel="instance/urn:uuid:self/local", kind="status.report",
        payload={"text": "hi"},
    )
    base.update(over)
    return build_envelope(**base)


# --- schema/contract ---------------------------------------------------------
def test_envelope_has_the_full_versioned_shape():
    e = _env()
    for key in ("id", "envelope_version", "origin_instance_id", "principal_id", "channel",
                "target", "kind", "payload", "correlation_id", "in_reply_to", "idempotency_key",
                "created_at", "expires_at", "trust", "delivery"):
        assert key in e
    assert e["delivery"] == "accepted" and e["delivery"] in DELIVERY_STATES
    assert e["trust"] == "untrusted_claim"
    assert e["target"] == {"instance_id": None, "handle": None}


def test_ulid_is_sortable_by_time():
    a = new_ulid(now_ms=1000)
    b = new_ulid(now_ms=2000)
    assert a < b and len(a) == 26 and len(b) == 26


def test_kind_must_be_allowlisted():
    with pytest.raises(EnvelopeError):
        _env(kind="shell.exec")
    with pytest.raises(EnvelopeError):
        _env(kind="command")
    # every allowlisted kind builds
    for k in ALLOWED_KINDS:
        assert _env(kind=k)["kind"] == k


def test_server_identity_is_required():
    with pytest.raises(EnvelopeError):
        build_envelope(origin_instance_id="", principal_id="p",
                       channel="instance/x/local", kind="status.report")


# --- input hardening (design §8.5) ------------------------------------------
def test_oversize_payload_rejected():
    big = {"blob": "x" * (MAX_PAYLOAD_BYTES + 100)}
    with pytest.raises(EnvelopeError):
        _env(payload=big)


def test_oversize_text_rejected():
    with pytest.raises(EnvelopeError):
        _env(payload={"text": "x" * (MAX_TEXT_BYTES + 1)})


def test_non_serializable_payload_rejected():
    with pytest.raises(EnvelopeError):
        _env(payload={"bad": {1, 2, 3}})  # a set is not JSON-serializable


@pytest.mark.parametrize("bad", [
    {"channel": ""},
    {"channel": "x" * 10_000},
    {"idempotency_key": "k" * 10_000},
    {"target_handle": "h" * 10_000},
])
def test_fuzz_malformed_fields_rejected(bad):
    with pytest.raises(EnvelopeError):
        _env(**bad)


# --- legacy projection -------------------------------------------------------
def test_legacy_projection_uses_derived_principal_not_a_claimed_handle():
    e = _env(target_handle="parent", payload={"text": "status ok"})
    row = legacy_projection(e, row_id=e["id"])
    assert row["from_handle"] == "instance:urn:uuid:self"  # SERVER-derived, never a claim
    assert row["to_handle"] == "parent"
    assert row["text"] == "status ok"
    assert row["kind"] == "status.report"
    # Phase 2 adds trust to the row so the UI badges host_observed vs an untrusted replica claim.
    assert set(row) == {"id", "from_handle", "to_handle", "text", "kind", "ts", "trust"}


# --- store idempotency -------------------------------------------------------
def test_store_suppresses_duplicate_idempotency_key():
    store = InMemoryStore()
    e1 = _env(idempotency_key="k-1")
    r1 = store.create_envelope(e1)
    assert r1["duplicate"] is False
    # a RETRY with the same key + origin returns the original, no new row
    e2 = _env(idempotency_key="k-1")
    r2 = store.create_envelope(e2)
    assert r2["duplicate"] is True
    assert r2["envelope"]["id"] == e1["id"]
    assert len(store.envelopes()) == 1


def test_store_distinguishes_keys_and_null_keys():
    store = InMemoryStore()
    store.create_envelope(_env(idempotency_key="a"))
    store.create_envelope(_env(idempotency_key="b"))
    store.create_envelope(_env())  # null key never dedups
    store.create_envelope(_env())
    assert len(store.envelopes()) == 4
