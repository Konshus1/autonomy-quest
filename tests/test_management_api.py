"""Task #4407 — FastAPI management skeleton smoke tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from management.api.app import app

client = TestClient(app)


def test_health() -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["merge_gate"] == "manager_gated"


def test_replication_awaits_operator_without_override(monkeypatch) -> None:
    monkeypatch.delenv("AQ_REPLICATION_AUTO_APPROVE", raising=False)
    r = client.post(
        "/api/replication/propose",
        json={
            "mode": "straight_copy",
            "mission_id": "m1",
            "requester_instance_id": "aq-1",
        },
    )
    assert r.status_code == 200
    assert r.json()["status"] == "awaiting_operator"


def test_replication_auto_approve_with_override(monkeypatch) -> None:
    monkeypatch.setenv("AQ_REPLICATION_AUTO_APPROVE", "1")
    r = client.post(
        "/api/replication/propose",
        json={
            "mode": "straight_copy",
            "mission_id": "m2",
            "requester_instance_id": "aq-1",
        },
    )
    assert r.status_code == 200
    assert r.json()["status"] == "auto_approved"


def test_manager_merge_approve() -> None:
    r = client.post(
        "/api/manager/merge-decision",
        json={
            "decision": "approve_merge",
            "manager_handle": "mgr-1",
            "cohort_id": "c1",
            "rationale": "Cohort green; cursory review clean.",
        },
    )
    assert r.status_code == 200
    assert r.json()["decision"] == "approve_merge"
