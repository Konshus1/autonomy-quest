"""Outbox pull + verification + publish-hardening at the HTTP boundary (#4834 comms Phase 2).

Covers:
  * GET /api/agent-comms/outbox — scoped read-only credential, cursor pagination, deny publish-only
    replica + unauthenticated (401/403), operator + relay-reader allowed;
  * POST /api/agent-comms/{id}/verify — parent-owned, operator-only (401/403 otherwise);
  * publish hardening — a forged experiment.result with a host-path/URL "reference" is a 400.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="fastapi is a Docker-kit dependency (requirements.txt)")

from fastapi.testclient import TestClient  # noqa: E402

from management.api import app as app_module  # noqa: E402
from management.api.comms_auth import CommsAuthenticator  # noqa: E402
from management.api.store import InMemoryStore  # noqa: E402

SELF = "urn:uuid:self"
PARENT = "urn:uuid:parent"
GOOD_DIGEST = "sha256:" + "a" * 64


@pytest.fixture
def client(monkeypatch):
    store = InMemoryStore()
    monkeypatch.setattr(app_module, "store", store)
    auth = CommsAuthenticator(credentials={
        "tok-self": {  # a plain replica: publish-only, NO outbox read
            "principal_id": f"instance:{SELF}", "origin_instance_id": SELF,
            "lineage": [SELF, PARENT],
            "kinds": ["status.report", "experiment.progress", "experiment.result"],
            "operator": False,
        },
        "tok-relay": {  # scoped host relay: read outbox, publish nothing
            "principal_id": "host:outbox-relay", "origin_instance_id": SELF,
            "lineage": [], "kinds": [], "operator": False, "can_read_outbox": True,
        },
        "tok-op": {  # operator: everything the UI needs
            "principal_id": "operator:local", "origin_instance_id": "operator",
            "lineage": [], "kinds": ["operator.message"], "operator": True,
        },
    })
    monkeypatch.setattr(app_module, "comms_auth", auth)
    monkeypatch.delenv("AQ_MGMT_DB_URL", raising=False)
    monkeypatch.delenv("AQ_DB_URL", raising=False)
    return TestClient(app_module.app), store


def _hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


def _publish(c, kind="status.report", **extra):
    body = {"channel": f"lineage/{PARENT}/status", "kind": kind,
            "payload": {"text": "hi"} if kind != "experiment.result"
            else {"summary": "done", "outcome_claimed": "success"}}
    body.update(extra)
    return c.post("/api/agent-comms", headers=_hdr("tok-self"), json=body)


# --- outbox read scope -------------------------------------------------------
def test_outbox_requires_credential(client):
    c, _ = client
    assert c.get("/api/agent-comms/outbox").status_code == 401


def test_outbox_denied_to_publish_only_replica(client):
    c, _ = client
    # A plain replica credential can PUBLISH but must not siphon an outbox (read scope denied).
    assert c.get("/api/agent-comms/outbox", headers=_hdr("tok-self")).status_code == 403


def test_outbox_readable_by_relay_and_operator(client):
    c, _ = client
    _publish(c)
    for tok in ("tok-relay", "tok-op"):
        r = c.get("/api/agent-comms/outbox", headers=_hdr(tok))
        assert r.status_code == 200, r.text
        assert r.json()["count"] == 1


def test_outbox_cursor_pagination(client):
    c, _ = client
    for i in range(5):
        _publish(c, payload={"text": f"m{i}"}, idempotency_key=f"k{i}")
    r1 = c.get("/api/agent-comms/outbox?after_seq=0&limit=2", headers=_hdr("tok-relay")).json()
    assert r1["count"] == 2 and [it["seq"] for it in r1["items"]] == [1, 2]
    r2 = c.get(f"/api/agent-comms/outbox?after_seq={r1['cursor']}&limit=2",
               headers=_hdr("tok-relay")).json()
    assert [it["seq"] for it in r2["items"]] == [3, 4]
    r3 = c.get(f"/api/agent-comms/outbox?after_seq={r2['cursor']}&limit=2",
               headers=_hdr("tok-relay")).json()
    assert [it["seq"] for it in r3["items"]] == [5]
    # Past the end: empty, cursor holds.
    r4 = c.get(f"/api/agent-comms/outbox?after_seq={r3['cursor']}", headers=_hdr("tok-relay")).json()
    assert r4["count"] == 0 and r4["cursor"] == 5


# --- publish hardening -------------------------------------------------------
def test_publish_forged_result_reference_rejected_400(client):
    c, _ = client
    r = c.post("/api/agent-comms", headers=_hdr("tok-self"), json={
        "channel": f"lineage/{PARENT}/experiments", "kind": "experiment.result",
        "payload": {"summary": "x", "artifact_refs": [
            {"digest": GOOD_DIGEST, "url": "http://169.254.169.254/latest/meta-data"}]}})
    assert r.status_code == 400


def test_publish_result_is_stamped_untrusted_claim(client):
    c, store = client
    _publish(c, kind="experiment.result",
             channel=f"lineage/{PARENT}/experiments",
             payload={"summary": "done", "outcome_claimed": "success",
                      "artifact_refs": [{"digest": GOOD_DIGEST}]})
    env = store.envelopes()[0]
    assert env["kind"] == "experiment.result"
    assert env["trust"] == "untrusted_claim"  # a replica can NEVER mint host_observed
    assert env["payload"]["verification_required"] is True


# --- verification is parent-owned -------------------------------------------
def test_verify_requires_operator(client):
    c, store = client
    _publish(c)
    eid = store.envelopes()[0]["id"]
    # unauthenticated + replica + relay-reader are all denied write to the verification state.
    assert c.post(f"/api/agent-comms/{eid}/verify", json={"state": "verified"}).status_code == 401
    assert c.post(f"/api/agent-comms/{eid}/verify", headers=_hdr("tok-self"),
                  json={"state": "verified"}).status_code == 403
    assert c.post(f"/api/agent-comms/{eid}/verify", headers=_hdr("tok-relay"),
                  json={"state": "verified"}).status_code == 403


def test_operator_sets_verification_without_mutating_claim(client):
    c, store = client
    _publish(c, kind="experiment.result", channel=f"lineage/{PARENT}/experiments",
             payload={"summary": "done", "outcome_claimed": "success"})
    env = store.envelopes()[0]
    eid = env["id"]
    before = dict(env)
    r = c.post(f"/api/agent-comms/{eid}/verify", headers=_hdr("tok-op"),
               json={"state": "rejected", "reason": "digest did not match the real run"})
    assert r.status_code == 200 and r.json()["verification"]["state"] == "rejected"
    # The immutable claim envelope is untouched; verification lives in a SEPARATE record.
    assert store.envelopes()[0] == before
    assert store.verifications()[eid]["state"] == "rejected"


def test_bad_verification_state_rejected_422(client):
    c, store = client
    _publish(c)
    eid = store.envelopes()[0]["id"]
    assert c.post(f"/api/agent-comms/{eid}/verify", headers=_hdr("tok-op"),
                  json={"state": "adopted"}).status_code == 422


# --- results projection defaults to unverified -------------------------------
def test_results_default_unverified_and_badged_claim(client):
    c, _ = client
    _publish(c, kind="experiment.result", channel=f"lineage/{PARENT}/experiments",
             payload={"summary": "done", "outcome_claimed": "success"})
    rows = c.get("/api/agent-comms/results", headers=_hdr("tok-op")).json()["items"]
    assert len(rows) == 1
    row = rows[0]
    assert row["trust"] == "untrusted_claim" and row["claim"] is True
    assert row["verification"] == "unverified"  # never adopted just because the replica claimed it
    assert row["outcome_claimed"] == "success"


def test_results_operator_only(client):
    c, _ = client
    assert c.get("/api/agent-comms/results").status_code == 401
    assert c.get("/api/agent-comms/results", headers=_hdr("tok-self")).status_code == 403
