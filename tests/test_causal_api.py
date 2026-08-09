"""Task #4407 slice 1d — causal front-half management API surfaces."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="fastapi is a Docker-kit dependency (requirements.txt)")

from fastapi.testclient import TestClient  # noqa: E402

from management.api.app import app  # noqa: E402

client = TestClient(app)


def _edge(cause, effect="e1", **over):
    e = {
        "cause": cause, "effect": effect, "scope": {"region": "us"},
        "formality": "evidential", "strictness": "advisory", "directness": "predicate",
        "executor": {"kind": "predicate", "ref": "checks/x.sql"}, "predicted_certainty": 0.7,
    }
    e.update(over)
    return e


def test_put_edge_valid_and_invalid():
    r = client.post("/api/causal/edges", json=_edge("api-a"))
    assert r.status_code == 200
    assert r.json()["identity"][0] == "api-a"
    # invalid: formal + judgment is rejected server-side
    bad = _edge("api-bad", formality="formal", directness="judgment", executor={"kind": "judgment"})
    assert client.post("/api/causal/edges", json=bad).status_code == 400


def test_edges_list_includes_put_edge():
    client.post("/api/causal/edges", json=_edge("api-list"))
    items = client.get("/api/causal/edges").json()["items"]
    assert any(e["cause"] == "api-list" for e in items)


def test_assess_plan_returns_profile():
    # An evidential+script edge is COVERED but NOT guaranteed — only formal+script is guaranteed,
    # and formal is unreachable via a raw PUT (see test_put_formal_is_rejected_laundering_guard).
    client.post("/api/causal/edges", json=_edge("api-plan", formality="evidential", directness="script",
                                                executor={"kind": "script", "ref": "s.py"},
                                                predicted_certainty=0.67))
    r = client.post("/api/causal/assess-plan", json={"steps": [
        {"action": "api-plan", "effect": "e1"}, {"action": "nope", "effect": "e9"}]})
    assert r.status_code == 200
    prof = r.json()
    assert prof["covered"] == 1 and prof["uncovered"] == 1
    assert prof["per_step"][0]["guaranteed"] is False


def test_put_formal_is_rejected_laundering_guard():
    # BB #775 / FORMAL_LAYER_SPEC §7: formality=formal cannot be minted by a raw PUT — it must be
    # earned via attach-executor + verify (a passing oracle proof) + promote. This closes the
    # plan-certainty-1.0 laundering hole (a client PUTting formal+script to fake a guaranteed step).
    r = client.post("/api/causal/edges", json=_edge("api-formal", formality="formal", directness="script",
                                                    executor={"kind": "script", "ref": "s.py"}))
    assert r.status_code == 400 and "formal cannot be set by PUT" in r.json()["detail"]


def test_record_outcome_surprise_and_gated_proposal():
    client.post("/api/causal/edges", json=_edge("api-out"))
    r = client.post("/api/causal/record-outcome", json={
        "cause": "api-out", "effect": "e1", "scope": {"region": "us"},
        "predicted_certainty": 0.7, "actual_success": True})
    assert r.status_code == 200
    body = r.json()
    assert body["surprise"]["signal"] == "confirm"
    assert body["proposal"]["gated"] is True


def test_record_outcome_unknown_edge_404():
    r = client.post("/api/causal/record-outcome", json={
        "cause": "nonexistent", "effect": "eX", "predicted_certainty": 0.5, "actual_success": False})
    assert r.status_code == 404


def test_put_edge_strips_system_managed_fields():
    # A client cannot launder a forced promotion / fake provenance through the write endpoint:
    # support_count/evidence/provenance/mined are system-managed and dropped server-side.
    r = client.post("/api/causal/edges", json=_edge("api-launder", support_count=999,
                                                    evidence=[{"x": 1}], mined=True,
                                                    provenance=[{"run_id": 7}], observed_runs=42))
    assert r.status_code == 200
    stored = next(e for e in client.get("/api/causal/edges").json()["items"]
                  if e["cause"] == "api-launder")
    assert "support_count" not in stored and "evidence" not in stored
    assert "provenance" not in stored and stored.get("mined") is not True
    assert "observed_runs" not in stored


def test_mine_requires_pg_store_without_db():
    # the module-level causal store is in-memory in tests (no DB) -> mining is unavailable (503)
    r = client.post("/api/causal/mine")
    assert r.status_code == 503
    assert "postgres" in r.json()["detail"].lower()


def test_plan_and_outcome_reject_extra_fields():
    assert client.post("/api/causal/assess-plan", json={"steps": [], "x": 1}).status_code == 422
    assert client.post("/api/causal/record-outcome", json={
        "cause": "a", "effect": "e", "predicted_certainty": 0.5, "actual_success": True,
        "sneaky": 1}).status_code == 422


def test_governance_surfaces_require_distinct_credentials(monkeypatch):
    test_body = {"cause": "a", "effect": "b", "environment": {
        "environment_id": "e", "domain": "d", "mission_id": "m", "harness": "h"},
        "evidence_ref": "run:1", "expected_direction": "increase", "observed_delta": -1}
    monkeypatch.setenv("AQ_GOVERNANCE_EVIDENCE_TOKEN", "evidence-secret")
    assert client.post("/api/causal/governance/test", json=test_body).status_code == 403

    promote_body = {"cause": "a", "effect": "b", "authorization_environment": test_body["environment"],
        "evidence_ref": "review:1", "applies_here": True, "applies_here_how": "query",
        "negative_control": "invert", "negative_control_result": "refuted",
        "adjudicated_by": "self-relabeled"}
    monkeypatch.setenv("AQ_GOVERNANCE_TOKEN", "promotion-secret")
    monkeypatch.setenv("AQ_GOVERNANCE_ADJUDICATOR", "independent-reviewer")
    r = client.post("/api/causal/governance/promote", json=promote_body,
                    headers={"x-aq-governance-token": "promotion-secret"})
    assert r.status_code == 403 and "authenticated adjudicator" in r.json()["detail"]
