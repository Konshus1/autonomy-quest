"""End-to-end wiring tests: T10 + T11 through the management API.

Proves the jump-mechanism components work through the full API stack,
not just at the module level. Uses FastAPI's TestClient against the
real management app — no mocking of the API layer.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from management.api.app import app


client = TestClient(app)


# ── T10: conceptual-inconsistency scan through API ─────────────────────────

def test_api_scan_inconsistencies_returns_report():
    """The /api/causal/scan-inconsistencies endpoint should return a scan report."""
    resp = client.post("/api/causal/scan-inconsistencies")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    report = body["report"]
    assert "total_principles" in report
    assert "conflicts_found" in report
    assert "classifications" in report
    assert "surprise_packets" in report


def test_api_scan_inconsistencies_emits_correct_surprise_type():
    """Any surprise packets emitted should have surprise_type='conceptual_inconsistency'."""
    resp = client.post("/api/causal/scan-inconsistencies")
    body = resp.json()
    for packet in body["report"].get("surprise_packets", []):
        assert packet["surprise_type"] == "conceptual_inconsistency"
        assert packet["packet_type"] == "ralph_surprise_packet_v0"


# ── T11: frame-expansion through API ───────────────────────────────────────

def test_api_frame_expansion_with_mappable_episode():
    """An episode with all mappable attributes should produce no signals."""
    resp = client.post("/api/causal/frame-expansion", json={
        "episodes": [
            {
                "episode_id": "test_ep",
                "attributes": ["identity", "failure_modes", "purpose"],
            }
        ],
        "mode": "situation_driven",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    result = body["result"]
    assert result["episodes_scanned"] == 1
    assert result["verdict"] == "no_gaps_detected"
    assert result["promoted"] == 0


def test_api_frame_expansion_with_uncapped_attribute():
    """An episode with an uncapped attribute should fire mapping_exhausted."""
    resp = client.post("/api/causal/frame-expansion", json={
        "episodes": [
            {
                "episode_id": "test_uncapped",
                "attributes": ["identity", "quantum_phase_coherence_factor"],
            }
        ],
        "mode": "situation_driven",
    })
    assert resp.status_code == 200
    body = resp.json()
    result = body["result"]
    assert len(result["mapping_exhausted_signals"]) >= 1
    # The uncapped attribute should appear in the signal
    signal = result["mapping_exhausted_signals"][0]
    uncapped_attrs = [a["attribute"] for a in signal["uncapped_attributes"]]
    assert "quantum_phase_coherence_factor" in uncapped_attrs


def test_api_frame_expansion_no_auto_promotion():
    """No dimensions should be auto-promoted even when gaps are detected."""
    resp = client.post("/api/causal/frame-expansion", json={
        "episodes": [
            {"episode_id": "ep1", "attributes": ["identity", "unknown_thing"]},
            {"episode_id": "ep2", "attributes": ["purpose", "unknown_thing"]},
        ],
        "mode": "situation_driven",
    })
    body = resp.json()
    assert body["result"]["promoted"] == 0


# ── T11: dimension library through API ─────────────────────────────────────

def test_api_list_dimensions():
    """The /api/causal/dimensions endpoint should list the active dimension library."""
    resp = client.get("/api/causal/dimensions")
    assert resp.status_code == 200
    body = resp.json()
    assert "active" in body
    assert "candidates" in body
    assert len(body["active"]) >= 10  # core dimensions
    dim_ids = [d["id"] for d in body["active"]]
    assert "identity" in dim_ids
    assert "reversibility" in dim_ids


def test_api_promote_dimension_requires_manager():
    """Dimension promotion should require manager_handle and reason (DR12)."""
    # First, feed an episode with a recurring uncapped attribute to create a candidate
    client.post("/api/causal/frame-expansion", json={
        "episodes": [
            {"episode_id": "ep_a", "attributes": ["identity", "nonlinear_dynamics"]},
            {"episode_id": "ep_b", "attributes": ["purpose", "nonlinear_dynamics"]},
        ],
        "mode": "situation_driven",
    })

    # Get the candidates
    resp = client.get("/api/causal/dimensions")
    candidates = resp.json()["candidates"]

    if candidates:
        candidate_id = candidates[0]["id"]
        # Try to promote without manager_handle -> should fail
        resp = client.post("/api/causal/dimensions/promote", json={
            "candidate_id": candidate_id,
            "reason": "test",
            "manager_handle": "",
        })
        # FastAPI should reject empty string for a min_length=1 field
        assert resp.status_code == 422


# ── Surprise type registration ────────────────────────────────────────────

def test_conceptual_inconsistency_is_supported_surprise_type():
    """The surprise_resolution module should accept 'conceptual_inconsistency'."""
    from ralph_portable.surprise_resolution import SUPPORTED_SURPRISE_TYPES
    assert "conceptual_inconsistency" in SUPPORTED_SURPRISE_TYPES
