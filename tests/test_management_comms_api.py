"""Task #4407 v8 — /api/agent-comms POST + GET (additive surface).

Kept in a separate file from test_management_api.py (co-owned with ccf-kevin-bootstrap)
to avoid stepping on the shared console/state tests.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="fastapi is a Docker-kit dependency (requirements.txt)")

from fastapi.testclient import TestClient  # noqa: E402

from management.api.app import app  # noqa: E402

client = TestClient(app)


def test_post_comm_then_get_lists_it() -> None:
    before = len(client.get("/api/agent-comms").json()["items"])
    r = client.post(
        "/api/agent-comms",
        json={"from_handle": "mgr-1", "to_handle": "worker-2", "text": "ping", "kind": "message"},
    )
    assert r.status_code == 200
    item = r.json()["item"]
    assert item["from_handle"] == "mgr-1"
    assert item["to_handle"] == "worker-2"
    assert item["text"] == "ping"
    assert item["id"].startswith("comm-")
    assert isinstance(item["ts"], str)

    items = client.get("/api/agent-comms").json()["items"]
    assert len(items) == before + 1
    assert any(c["id"] == item["id"] and c["text"] == "ping" for c in items)


def test_post_comm_broadcast_without_target() -> None:
    r = client.post("/api/agent-comms", json={"from_handle": "mgr-1", "text": "all hands"})
    assert r.status_code == 200
    item = r.json()["item"]
    assert item["to_handle"] is None
    assert item["kind"] == "message"  # default


def test_post_comm_requires_text_and_sender() -> None:
    assert client.post("/api/agent-comms", json={"from_handle": "mgr-1", "text": ""}).status_code == 422
    assert client.post("/api/agent-comms", json={"text": "no sender"}).status_code == 422


def test_state_counts_reflect_posted_comms() -> None:
    client.post("/api/agent-comms", json={"from_handle": "mgr-1", "text": "counted"})
    state = client.get("/api/ralph/state").json()
    assert state["counts"]["agent_comms"] >= 1
