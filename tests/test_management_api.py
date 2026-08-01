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
    # Honesty marker: `/` must serve one of the two real surfaces and identify itself
    # honestly. When the React build is present (web/dist), it is preferred and mounts a
    # <div id="root"> + a hashed /assets bundle; otherwise the interim vanilla console is
    # served and must say "interim" (never masquerade as the React build).
    from management.api.app import _DIST_INDEX

    if _DIST_INDEX.is_file():
        assert 'id="root"' in r.text  # the React shell
        assert "interim" not in r.text.lower()  # and it is NOT the interim console
    else:
        assert "interim" in r.text.lower()  # honest interim fallback


def test_root_prefers_react_build_when_present() -> None:
    # Regression guard for the React-shell wire-in (Task #4407 slice): when web/dist exists,
    # `/` must serve the built shell, not fall back to the interim console.
    from management.api.app import _DIST_INDEX

    if not _DIST_INDEX.is_file():
        import pytest

        pytest.skip("React build not present (web/dist) — build with `npm run build` to exercise")
    r = client.get("/")
    assert r.status_code == 200
    assert 'id="root"' in r.text
    assert "interim" not in r.text.lower()


def test_ralph_state_snapshot() -> None:
    r = client.get("/api/ralph/state")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["merge_gate"] == "manager_gated"
    assert body["backing"] == "in_memory_stub"
    assert "counts" in body and "workstreams" in body["counts"]
    assert "replication" in body and "override_enabled" in body["replication"]
