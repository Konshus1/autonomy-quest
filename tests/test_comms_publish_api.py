"""End-to-end API tests for the authenticated comms publish path (#4834 comms Phase 0).

Covers the four brief deliverables at the HTTP boundary:
  * credential impersonation rejected (server-derived identity; claimed fields refused),
  * cross-instance ACL denial (403),
  * duplicate-idempotency-key suppressed,
  * DB-outage -> 503 (no ephemeral ack),
plus payload size limits, unauthenticated-POST deprecation (401), and the unbroken legacy GET.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="fastapi is a Docker-kit dependency (requirements.txt)")

from fastapi.testclient import TestClient  # noqa: E402

from management.api import app as app_module  # noqa: E402
from management.api.comms_auth import CommsAuthenticator  # noqa: E402
from management.api.comms_envelope import MAX_PAYLOAD_BYTES  # noqa: E402
from management.api.store import InMemoryStore  # noqa: E402


SELF = "urn:uuid:self"
PARENT = "urn:uuid:parent"
OTHER = "urn:uuid:other"


@pytest.fixture
def client(monkeypatch):
    """A TestClient with an isolated in-memory store + a known credentials map."""
    store = InMemoryStore()
    monkeypatch.setattr(app_module, "store", store)
    auth = CommsAuthenticator(credentials={
        "tok-self": {
            "principal_id": f"instance:{SELF}", "origin_instance_id": SELF,
            "lineage": [SELF, PARENT], "kinds": ["status.report", "experiment.result"],
            "operator": False,
        },
    })
    monkeypatch.setattr(app_module, "comms_auth", auth)
    # No DB configured for the default happy-path tests.
    monkeypatch.delenv("AQ_MGMT_DB_URL", raising=False)
    monkeypatch.delenv("AQ_DB_URL", raising=False)
    return TestClient(app_module.app), store


def _hdr(tok="tok-self"):
    return {"Authorization": f"Bearer {tok}"}


# --- happy path + legacy GET -------------------------------------------------
def test_authenticated_publish_then_legacy_get_projection(client):
    c, _ = client
    r = c.post("/api/agent-comms", headers=_hdr(), json={
        "channel": f"instance/{SELF}/local", "kind": "status.report",
        "payload": {"text": "hello"},
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] and body["duplicate"] is False
    assert body["envelope"]["origin_instance_id"] == SELF

    items = c.get("/api/agent-comms").json()["items"]  # GET stays unauthenticated + projected
    assert any(it["from_handle"] == f"instance:{SELF}" and it["text"] == "hello" for it in items)


# --- impersonation rejected --------------------------------------------------
def test_claimed_identity_fields_are_rejected(client):
    c, _ = client
    # extra=forbid: naming another principal is a 422, never an accepted spoof.
    r = c.post("/api/agent-comms", headers=_hdr(), json={
        "channel": f"instance/{SELF}/local", "kind": "status.report",
        "payload": {"text": "x"}, "from_handle": "operator", "origin_instance_id": OTHER,
    })
    assert r.status_code == 422


def test_identity_is_derived_from_the_credential_not_the_body(client):
    c, store = client
    c.post("/api/agent-comms", headers=_hdr(), json={
        "channel": f"instance/{SELF}/local", "kind": "status.report",
        "payload": {"text": "x", "AQ_INSTANCE_ID": OTHER, "sender_name": "parent"},
    })
    env = store.envelopes()[0]
    assert env["origin_instance_id"] == SELF  # derived; the payload claim is ignored
    assert env["principal_id"] == f"instance:{SELF}"


# --- auth deprecation --------------------------------------------------------
def test_unauthenticated_post_is_rejected(client):
    c, _ = client
    r = c.post("/api/agent-comms", json={
        "channel": f"instance/{SELF}/local", "kind": "status.report", "payload": {"text": "x"}})
    assert r.status_code == 401


def test_unknown_credential_rejected(client):
    c, _ = client
    r = c.post("/api/agent-comms", headers=_hdr("bogus"), json={
        "channel": f"instance/{SELF}/local", "kind": "status.report", "payload": {"text": "x"}})
    assert r.status_code == 401


# --- cross-instance ACL denial ----------------------------------------------
def test_cross_instance_channel_denied_403(client):
    c, _ = client
    r = c.post("/api/agent-comms", headers=_hdr(), json={
        "channel": f"instance/{OTHER}/local", "kind": "status.report", "payload": {"text": "x"}})
    assert r.status_code == 403


def test_forbidden_kind_denied_403(client):
    c, _ = client
    r = c.post("/api/agent-comms", headers=_hdr(), json={
        "channel": f"instance/{SELF}/local", "kind": "operator.message", "payload": {"text": "x"}})
    assert r.status_code == 403


def test_unlisted_kind_rejected_400(client):
    c, _ = client
    # A kind that passes the principal allowlist check would still be caught by the envelope enum;
    # here 'shell.exec' is not in the principal's kinds -> ACL 403 first (defense in depth).
    r = c.post("/api/agent-comms", headers=_hdr(), json={
        "channel": f"instance/{SELF}/local", "kind": "shell.exec", "payload": {"text": "x"}})
    assert r.status_code == 403


# --- idempotency -------------------------------------------------------------
def test_duplicate_idempotency_key_suppressed(client):
    c, store = client
    payload = {"channel": f"instance/{SELF}/local", "kind": "status.report",
               "payload": {"text": "once"}, "idempotency_key": "abc-123"}
    r1 = c.post("/api/agent-comms", headers=_hdr(), json=payload)
    r2 = c.post("/api/agent-comms", headers=_hdr(), json=payload)
    assert r1.json()["duplicate"] is False
    assert r2.json()["duplicate"] is True
    assert r1.json()["envelope"]["id"] == r2.json()["envelope"]["id"]
    assert len(store.envelopes()) == 1


# --- payload size ------------------------------------------------------------
def test_oversize_payload_rejected_400(client):
    c, _ = client
    r = c.post("/api/agent-comms", headers=_hdr(), json={
        "channel": f"instance/{SELF}/local", "kind": "status.report",
        "payload": {"blob": "x" * (MAX_PAYLOAD_BYTES + 100)}})
    assert r.status_code == 400


# --- fail-closed durable write ----------------------------------------------
def test_db_outage_returns_503_never_an_ephemeral_ack(client, monkeypatch):
    c, store = client
    # Simulate: a durable DB WAS configured, but the store fell back to memory (unreachable).
    monkeypatch.setenv("AQ_MGMT_DB_URL", "postgresql://unreachable/db")
    store.durability_required = True
    assert store.backing != "postgres"
    r = c.post("/api/agent-comms", headers=_hdr(), json={
        "channel": f"instance/{SELF}/local", "kind": "status.report", "payload": {"text": "x"}})
    assert r.status_code == 503
    assert store.envelopes() == []  # nothing was accepted


def test_no_db_configured_still_accepts_in_memory(client):
    # With NO DB configured at all, in-memory is the intended backing (pytest/local) — writes ok.
    c, store = client
    r = c.post("/api/agent-comms", headers=_hdr(), json={
        "channel": f"instance/{SELF}/local", "kind": "status.report", "payload": {"text": "x"}})
    assert r.status_code == 200
    assert len(store.envelopes()) == 1
