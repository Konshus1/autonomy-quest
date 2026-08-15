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


def test_replication_authority_envelope_dropped_when_flag_off(monkeypatch) -> None:
    # Slice-4: flag OFF => the authority_envelope field is dropped; behaviour byte-identical.
    monkeypatch.delenv("AQ_REPLICATION_AUTHORITY_ENVELOPE", raising=False)
    monkeypatch.setenv("AQ_REPLICATION_AUTO_APPROVE", "1")
    r = client.post(
        "/api/replication/propose",
        json={
            "mode": "straight_copy", "mission_id": "m-ae-off", "requester_instance_id": "aq-1",
            "authority_envelope": {"dispositions": {"deploy": "open"}},
        },
    )
    assert r.status_code == 200
    assert "authority_envelope" not in r.json()["record"]


def test_replication_authority_envelope_shape_validated_when_flag_on(monkeypatch) -> None:
    monkeypatch.setenv("AQ_REPLICATION_AUTHORITY_ENVELOPE", "1")
    monkeypatch.setenv("AQ_REPLICATION_AUTO_APPROVE", "1")
    # bad class -> 400
    bad = client.post(
        "/api/replication/propose",
        json={
            "mode": "straight_copy", "mission_id": "m-ae-bad", "requester_instance_id": "aq-1",
            "authority_envelope": {"dispositions": {"nope": "open"}},
        },
    )
    assert bad.status_code == 400
    assert any("unknown class" in d for d in bad.json()["detail"])
    # valid -> 200 and the grant is carried in the stored record for the host to enforce
    ok = client.post(
        "/api/replication/propose",
        json={
            "mode": "straight_copy", "mission_id": "m-ae-ok", "requester_instance_id": "aq-1",
            "authority_envelope": {"dispositions": {"commit": "open"}, "may_self_widen": False},
        },
    )
    assert ok.status_code == 200
    assert ok.json()["record"]["authority_envelope"] == {
        "dispositions": {"commit": "open"}, "may_self_widen": False}


def test_replication_forged_requester_id_overridden_by_authenticated_identity(monkeypatch) -> None:
    # Slice-4 HIGH-#1: the propose endpoint runs in-container as the instance's own mgmt API; it
    # STAMPS the authenticated requester from its own host-set AQ_INSTANCE_ID and IGNORES whatever
    # the guest body claimed. A forged requester_instance_id cannot select a wider ancestor.
    monkeypatch.setenv("AQ_REPLICATION_AUTHORITY_ENVELOPE", "1")
    monkeypatch.setenv("AQ_REPLICATION_AUTO_APPROVE", "1")
    monkeypatch.setenv("AQ_INSTANCE_ID", "urn:uuid:the-real-caller")
    r = client.post(
        "/api/replication/propose",
        json={
            "mode": "straight_copy", "mission_id": "m-forge",
            "requester_instance_id": "urn:uuid:FORGED-wider-ancestor",  # guest lie
            "authority_envelope": {"dispositions": {"commit": "open"}},
        },
    )
    assert r.status_code == 200
    rec = r.json()["record"]
    # the host-trusted parent-selection key is the authenticated id, NOT the forged body value
    assert rec["authenticated_requester_instance_id"] == "urn:uuid:the-real-caller"
    assert rec["requester_instance_id"] == "urn:uuid:FORGED-wider-ancestor"  # body kept, but unused


def test_replication_authenticated_requester_empty_when_instance_id_unset(monkeypatch) -> None:
    # Fail-closed input: an unauthenticated propose (no AQ_INSTANCE_ID) stamps an EMPTY authenticated
    # requester, which the host resolves to the fully-gated DEFAULT parent at stand-up.
    monkeypatch.setenv("AQ_REPLICATION_AUTHORITY_ENVELOPE", "1")
    monkeypatch.setenv("AQ_REPLICATION_AUTO_APPROVE", "1")
    monkeypatch.delenv("AQ_INSTANCE_ID", raising=False)
    r = client.post(
        "/api/replication/propose",
        json={
            "mode": "straight_copy", "mission_id": "m-noauth", "requester_instance_id": "aq-x",
            "authority_envelope": {"dispositions": {"commit": "open"}},
        },
    )
    assert r.status_code == 200
    assert r.json()["record"]["authenticated_requester_instance_id"] == ""


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


def test_flagship_endpoint_preserves_measure_error_instead_of_zero(monkeypatch) -> None:
    import management.api.app as module

    broken = {
        "mission": {
            "objective": "Get to 20 paying customers by the end of Q3",
            "measure": "count of active paying customers",
            "now": None,
            "error": 'relation "missing_subscriptions" does not exist',
            "target": 20,
            "target_error": None,
            "goal": "reach_and_maintain",
            "satisfied": False,
            "overshooting": False,
        },
        "health": {"status": "STALLED", "level": "red", "headline": "MEASURE ERROR", "detail": "cannot read measure"},
        "loop": {"cycles": 0, "turning": False},
        "trend": [], "runs": [], "learnings": [], "parked": [], "beliefs_revised_count": 0,
    }
    monkeypatch.setattr(module, "flagship_state", lambda: broken)
    r = client.get("/api/flagship")
    assert r.status_code == 200
    assert r.json()["mission"]["now"] is None
    assert "does not exist" in r.json()["mission"]["error"]


def test_flagship_gate_rejects_missing_token_before_touching_database(monkeypatch) -> None:
    monkeypatch.setenv("AQ_APPROVAL_TOKEN", "secret")
    r = client.post("/api/flagship/gate/1/approve", json={})
    assert r.status_code == 403
