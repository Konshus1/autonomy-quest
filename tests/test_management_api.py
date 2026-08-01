"""Task #4407 — FastAPI management skeleton smoke tests.

FastAPI is a Docker-OOTB kit dependency (see requirements.txt). In a stdlib-only
environment it may be absent; skip cleanly rather than erroring collection so the
rest of the smoke suite stays green and honest.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="fastapi is a Docker-kit dependency (requirements.txt)")

from fastapi.testclient import TestClient  # noqa: E402

from management.api.app import app  # noqa: E402

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


def test_console_root_serves_html() -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    # Honesty marker: the interim shell must not masquerade as the React build.
    assert "interim" in r.text.lower()


def test_ralph_state_snapshot() -> None:
    r = client.get("/api/ralph/state")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["merge_gate"] == "manager_gated"
    assert body["backing"] == "in_memory_stub"
    assert "counts" in body and "workstreams" in body["counts"]
    assert "replication" in body and "override_enabled" in body["replication"]
