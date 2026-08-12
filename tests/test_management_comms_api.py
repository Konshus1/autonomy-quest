"""/api/agent-comms GET (legacy read projection) + POST (authenticated publish).

Originally Task #4407 v8's unauthenticated demo POST; updated for #4834 comms Phase 0, which
DEPRECATES the unauthenticated POST (now an authenticated, ACL-enforced publish of a versioned
envelope) while KEEPING GET as a back-compat read projection so the React poll stays unbroken.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="fastapi is a Docker-kit dependency (requirements.txt)")

from fastapi.testclient import TestClient  # noqa: E402

from management.api import app as app_module  # noqa: E402
from management.api.comms_auth import CommsAuthenticator  # noqa: E402
from management.api.store import InMemoryStore  # noqa: E402

SELF = "urn:uuid:self"


@pytest.fixture
def client(monkeypatch):
    store = InMemoryStore()
    monkeypatch.setattr(app_module, "store", store)
    monkeypatch.setattr(app_module, "comms_auth", CommsAuthenticator(credentials={
        "tok": {"principal_id": f"instance:{SELF}", "origin_instance_id": SELF,
                "lineage": [SELF], "kinds": ["status.report"], "operator": False},
    }))
    monkeypatch.delenv("AQ_MGMT_DB_URL", raising=False)
    monkeypatch.delenv("AQ_DB_URL", raising=False)
    return TestClient(app_module.app)


def _hdr():
    return {"Authorization": "Bearer tok"}


def test_authenticated_post_then_get_lists_projection(client) -> None:
    before = len(client.get("/api/agent-comms").json()["items"])
    r = client.post("/api/agent-comms", headers=_hdr(), json={
        "channel": f"instance/{SELF}/local", "kind": "status.report",
        "payload": {"text": "ping"}, "target": {"handle": "worker-2"}})
    assert r.status_code == 200
    item = r.json()["item"]
    assert item["from_handle"] == f"instance:{SELF}"  # SERVER-derived
    assert item["to_handle"] == "worker-2"
    assert item["text"] == "ping"
    assert isinstance(item["ts"], str)

    items = client.get("/api/agent-comms").json()["items"]
    assert len(items) == before + 1
    assert any(c["id"] == item["id"] and c["text"] == "ping" for c in items)


def test_unauthenticated_post_is_deprecated_and_rejected(client) -> None:
    r = client.post("/api/agent-comms", json={
        "channel": f"instance/{SELF}/local", "kind": "status.report", "payload": {"text": "x"}})
    assert r.status_code == 401


def test_state_counts_reflect_published_comms(client) -> None:
    client.post("/api/agent-comms", headers=_hdr(), json={
        "channel": f"instance/{SELF}/local", "kind": "status.report", "payload": {"text": "counted"}})
    state = client.get("/api/ralph/state").json()
    assert state["counts"]["agent_comms"] >= 1
