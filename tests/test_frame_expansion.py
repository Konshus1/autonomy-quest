"""Tests for T11 frame-expansion mechanism (C10/DR5).

Tests the three-part pipeline: mapping_exhausted detection, recurring mismatch
accumulation + dimension proposal, and manager-gated promotion.

Origin: BB #2358 jump-system gap analysis. Fixture proven in task #4903.
"""

from __future__ import annotations

from ralph_portable.frame_expansion import (
    DEFAULT_DIMENSIONS,
    DimensionLibrary,
    accumulate_mismatches,
    detect_mapping_exhausted,
    gate_candidate,
    propose_dimension,
    run_frame_expansion,
)


# ── Test episodes ───────────────────────────────────────────────────────────

MAPPABLE_EPISODE = {
    "episode_id": "ep_001",
    "attributes": ["identity", "failure_modes", "state_transitions", "inputs"],
    "relational_graph": {"nodes": [{"id": "a", "type": "action"}], "edges": []},
}

MAPPABLE_EPISODE_2 = {
    "episode_id": "ep_002",
    "attributes": ["purpose", "dependencies", "constraints", "outputs"],
    "relational_graph": {"nodes": [{"id": "b", "type": "action"}], "edges": []},
}

UNCAPPED_EPISODE = {
    "episode_id": "ep_003",
    "attributes": ["identity", "failure_modes", "quantum_entanglement"],
    "relational_graph": {"nodes": [{"id": "c", "type": "action"}], "edges": []},
}

UNCAPPED_EPISODE_2 = {
    "episode_id": "ep_004",
    "attributes": ["purpose", "quantum_entanglement", "constraints"],
    "relational_graph": {"nodes": [{"id": "d", "type": "action"}], "edges": []},
}


# ── Part A: detection ──────────────────────────────────────────────────────

def test_detection_succeeds_on_mappable_episode():
    """An episode whose attributes all map above threshold should return None."""
    lib = DimensionLibrary()
    result = detect_mapping_exhausted(MAPPABLE_EPISODE, lib)
    assert result is None


def test_detection_fires_on_uncapped_attribute():
    """An episode with an attribute no dimension captures should fire mapping_exhausted."""
    lib = DimensionLibrary()
    result = detect_mapping_exhausted(UNCAPPED_EPISODE, lib)
    assert result is not None
    assert result.episode_id == "ep_003"
    assert len(result.uncapped_attributes) >= 1
    uncapped_attrs = [a["attribute"] for a in result.uncapped_attributes]
    assert "quantum_entanglement" in uncapped_attrs


def test_detection_carries_structural_mismatch_data():
    """The mapping_exhausted signal should carry the specific uncapped attributes."""
    lib = DimensionLibrary()
    result = detect_mapping_exhausted(UNCAPPED_EPISODE, lib)
    assert result is not None
    for attr in result.uncapped_attributes:
        assert "attribute" in attr
        assert "best_match_dim" in attr
        assert "best_similarity" in attr


# ── Part B: proposal ───────────────────────────────────────────────────────

def test_accumulate_finds_recurring_mismatches():
    """Same uncapped attribute in >= 2 episodes should be flagged as recurring."""
    lib = DimensionLibrary()
    sig1 = detect_mapping_exhausted(UNCAPPED_EPISODE, lib)
    sig2 = detect_mapping_exhausted(UNCAPPED_EPISODE_2, lib)
    assert sig1 is not None
    assert sig2 is not None

    recurring = accumulate_mismatches([sig1, sig2])
    assert "quantum_entanglement" in recurring
    assert len(recurring["quantum_entanglement"]) == 2


def test_accumulate_ignores_non_recurring():
    """An attribute appearing only once should not be in the recurring set."""
    lib = DimensionLibrary()
    sig = detect_mapping_exhausted(UNCAPPED_EPISODE, lib)
    assert sig is not None
    recurring = accumulate_mismatches([sig])
    # quantum_entanglement appears only once -> not recurring
    assert "quantum_entanglement" not in recurring


def test_propose_dimension_heuristic():
    """The heuristic proposer should generate a plausible candidate dimension."""
    proposal = propose_dimension("quantum_entanglement", 2, ["ep_003", "ep_004"])
    assert proposal["id"] == "quantum_entanglement"
    assert "definition" in proposal
    assert proposal["recurrence_count"] == 2


def test_propose_dimension_with_llm():
    """When an LLM callback is provided, it should be used for the proposal."""
    called = [False]

    def mock_llm(prompt: str) -> str:
        called[0] = True
        import json
        return json.dumps({
            "id": "quantum_coherence",
            "definition": "The degree to which a system maintains coherent quantum states.",
            "facet": "epistemic",
            "examples": ["superposition", "entanglement"],
        })

    proposal = propose_dimension("quantum_entanglement", 2, ["ep_003", "ep_004"], llm_propose_fn=mock_llm)
    assert called[0]
    assert proposal["id"] == "quantum_coherence"
    assert proposal["facet"] == "epistemic"


# ── Part C: gate ────────────────────────────────────────────────────────────

def test_gate_adds_candidate_without_promoting():
    """The gate should add the candidate as status='proposed', NOT promote it."""
    lib = DimensionLibrary()
    proposal = propose_dimension("quantum_entanglement", 2, ["ep_003", "ep_004"])
    report = gate_candidate(proposal, lib)

    assert report["manager_approval"] is False  # NEVER auto-granted
    assert len(lib.candidates()) == 1
    assert lib.candidates()[0]["status"] == "proposed"
    # The dimension should NOT be in the active library
    assert lib.get("quantum_entanglement") is None


def test_gate_recurrence_check():
    """A candidate with recurrence < threshold should not be ready for promotion."""
    lib = DimensionLibrary()
    proposal = {"id": "test_dim", "recurrence_count": 1, "definition": "test", "facet": "causal"}
    report = gate_candidate(proposal, lib)
    assert not report["recurrence_met"]
    assert not report["ready_for_promotion"]


def test_gate_promotion_requires_manager():
    """Promotion should only happen through explicit manager approval."""
    lib = DimensionLibrary()
    proposal = propose_dimension("quantum_entanglement", 2, ["ep_003", "ep_004"])
    gate_candidate(proposal, lib)

    # Before promotion: candidate exists, not in active library
    assert len(lib.candidates()) == 1
    assert lib.get("quantum_entanglement") is None

    # Manager promotes
    result = lib.promote("quantum_entanglement", "manager approved: recurring across 2 episodes")
    assert result is not None
    assert result["status"] == "active"
    assert lib.get("quantum_entanglement") is not None


# ── Full pipeline ───────────────────────────────────────────────────────────

def test_run_frame_expansion_finds_gaps():
    """The full pipeline should detect gaps and propose dimensions."""
    episodes = [MAPPABLE_EPISODE, UNCAPPED_EPISODE, UNCAPPED_EPISODE_2]
    result = run_frame_expansion(episodes)

    assert result["episodes_scanned"] == 3
    assert len(result["mapping_exhausted_signals"]) == 2  # ep_003 and ep_004
    assert "quantum_entanglement" in result["recurring_mismatches"]
    assert len(result["proposed_dimensions"]) >= 1
    assert result["promoted"] == 0  # no auto-promotion


def test_run_frame_expansion_no_gaps_on_mappable_episodes():
    """The pipeline should report no gaps when all episodes are mappable."""
    episodes = [MAPPABLE_EPISODE, MAPPABLE_EPISODE_2]
    result = run_frame_expansion(episodes)

    assert result["episodes_scanned"] == 2
    assert len(result["mapping_exhausted_signals"]) == 0
    assert len(result["proposed_dimensions"]) == 0
    assert result["promoted"] == 0
    assert result["verdict"] == "no_gaps_detected"


def test_dimension_library_has_default_dimensions():
    """The default dimension library should include the T1 54-dim core subset."""
    lib = DimensionLibrary()
    dims = lib.all()
    assert len(dims) >= 10  # at least the core dimensions
    dim_ids = [d["id"] for d in dims]
    assert "identity" in dim_ids
    assert "failure_modes" in dim_ids
    assert "reversibility" in dim_ids  # from T1's extensibility test
    assert "blast_radius" in dim_ids
