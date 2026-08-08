"""Tests for T10 conceptual-inconsistency detector (C4b/DR4).

Tests the three-phase pipeline: deterministic tag-overlap filter, LLM/heuristic
classification, and surprise-packet emission.

Origin: BB #2358 jump-system gap analysis. Fixture proven in task #4902.
"""

from __future__ import annotations

import json

from ralph_portable.inconsistency_detector import (
    CandidatePair,
    check_polarity_conflict,
    parse_hint_polarity,
    phase1_deterministic_filter,
    phase2_classify_pair,
    phase3_emit_packet,
    scan_inconsistencies,
)


# ── Test principles ─────────────────────────────────────────────────────────

COHERENT_PRINCIPLES = [
    {
        "principle_id": "fast_feedback",
        "principle_text": "When uncertain, prefer fast feedback before large commitments.",
        "tags": ["speed_to_validation", "learning_value", "risk_cost"],
        "status": "provisional",
        "is_active": True,
        "failure_modes_addressed": ["large_unvalidated_commitment", "slow_learning_loop"],
        "formalization_hint": "Planner can prefer actions with measurable_outcome before high-cost actions.",
    },
    {
        "principle_id": "human_attention_scarce",
        "principle_text": "Human attention is scarce; reserve for high-uncertainty decisions.",
        "tags": ["risk_cost", "strategic_leverage"],
        "status": "provisional",
        "is_active": True,
        "failure_modes_addressed": ["manager_bottleneck", "unnecessary_escalation"],
        "formalization_hint": "ASP: penalize escalate actions that consume human_review unnecessarily.",
    },
    {
        "principle_id": "record_predictions",
        "principle_text": "Record expected observations before execution.",
        "tags": ["learning_value", "reliability_impact"],
        "status": "provisional",
        "is_active": True,
        "failure_modes_addressed": ["no_credit_assignment", "post_hoc_rationalization"],
        "formalization_hint": "ASP: require expected_observation facts before action execution.",
    },
]

CONFLICTING_PRINCIPLES = [
    {
        "principle_id": "prefer_speed",
        "principle_text": "Always prefer speed — act fast and iterate.",
        "tags": ["speed_to_validation", "risk_cost", "learning_value"],
        "status": "provisional",
        "is_active": True,
        "failure_modes_addressed": ["slow_delivery"],
        "formalization_hint": "Planner: reward fast_execution; penalize slow_review.",
    },
    {
        "principle_id": "prefer_caution",
        "principle_text": "Always prefer caution — review thoroughly before acting.",
        "tags": ["speed_to_validation", "risk_cost", "learning_value"],
        "status": "provisional",
        "is_active": True,
        "failure_modes_addressed": ["slow_delivery"],
        "formalization_hint": "Planner: penalize fast_execution; reward slow_review.",
    },
    {
        "principle_id": "irrelevant",
        "principle_text": "An unrelated principle.",
        "tags": ["unrelated_tag"],
        "status": "provisional",
        "is_active": True,
        "failure_modes_addressed": ["unrelated_failure"],
        "formalization_hint": "",
    },
]


# ── Phase 1: deterministic filter ───────────────────────────────────────────

def test_phase1_finds_overlap_candidates():
    """Principles with >= 2 shared tags should become candidates."""
    candidates = phase1_deterministic_filter(CONFLICTING_PRINCIPLES)
    # prefer_speed and prefer_caution share 3 tags -> candidate
    pair_ids = {(c.principle_a, c.principle_b) for c in candidates}
    assert ("prefer_speed", "prefer_caution") in pair_ids or ("prefer_caution", "prefer_speed") in pair_ids


def test_phase1_prunes_non_overlapping():
    """Principles with < 2 shared tags and no shared failure modes should be pruned."""
    candidates = phase1_deterministic_filter(CONFLICTING_PRINCIPLES)
    # "irrelevant" has only 1 tag, shared with nothing -> no candidates involving it
    for c in candidates:
        assert "irrelevant" not in (c.principle_a, c.principle_b)


def test_phase1_polarity_conflict_detection():
    """Two hints that reward and penalize the same action should be flagged."""
    polarity = check_polarity_conflict(
        "Planner: reward fast_execution; penalize slow_review.",
        "Planner: penalize fast_execution; reward slow_review.",
    )
    assert polarity["has_conflict"]
    assert len(polarity["conflicts"]) >= 1
    # fast_execution should be in the conflicts (rewarded by A, penalized by B)
    actions = {c["action"] for c in polarity["conflicts"]}
    assert "fast_execution" in actions


def test_phase1_no_polarity_conflict_on_coherent():
    """Coherent principles should not have polarity conflicts."""
    polarity = check_polarity_conflict(
        "Planner: reward measurable_outcome before high-cost actions.",
        "ASP: penalize escalate actions that consume human_review unnecessarily.",
    )
    assert not polarity["has_conflict"]


# ── Phase 2: classification ─────────────────────────────────────────────────

def test_phase2_heuristic_finds_direct_conflict():
    """Two principles with opposing formalization hints should classify as direct_conflict."""
    result = phase2_classify_pair(CONFLICTING_PRINCIPLES[0], CONFLICTING_PRINCIPLES[1])
    assert result["classification"] in ("direct_conflict", "guidance_conflict")


def test_phase2_heuristic_no_conflict_on_coherent():
    """Coherent principles should classify as no_conflict."""
    result = phase2_classify_pair(COHERENT_PRINCIPLES[0], COHERENT_PRINCIPLES[1])
    assert result["classification"] in ("no_conflict", "soft_tension")


def test_phase2_llm_callback_used_when_provided():
    """When an LLM callback is provided, it should be used for classification."""
    called = [False]

    def mock_llm(prompt: str) -> str:
        called[0] = True
        return json.dumps({
            "classification": "direct_conflict",
            "scope": "test_scope",
            "explanation": "test explanation",
        })

    result = phase2_classify_pair(CONFLICTING_PRINCIPLES[0], CONFLICTING_PRINCIPLES[1], llm_classify_fn=mock_llm)
    assert called[0]
    assert result["classification"] == "direct_conflict"
    assert result["scope"] == "test_scope"


# ── Phase 3: surprise packet emission ──────────────────────────────────────

def test_phase3_emits_packet_for_conflict():
    """A direct_conflict classification should produce a surprise packet."""
    packet = phase3_emit_packet(
        CONFLICTING_PRINCIPLES[0],
        CONFLICTING_PRINCIPLES[1],
        {"classification": "direct_conflict", "scope": "fast_execution", "explanation": "opposing polarity"},
    )
    assert packet is not None
    assert packet["surprise_type"] == "conceptual_inconsistency"
    assert packet["severity"] == "high"
    assert packet["classification"] == "direct_conflict"


def test_phase3_no_packet_for_no_conflict():
    """A no_conflict classification should produce no packet."""
    packet = phase3_emit_packet(
        COHERENT_PRINCIPLES[0],
        COHERENT_PRINCIPLES[1],
        {"classification": "no_conflict", "scope": "", "explanation": ""},
    )
    assert packet is None


# ── Full pipeline ───────────────────────────────────────────────────────────

def test_scan_coherent_corpus_finds_zero_conflicts():
    """A coherent corpus should produce zero conflicts (the expected T10 result)."""
    report = scan_inconsistencies(COHERENT_PRINCIPLES)
    assert report["conflicts_found"] == 0
    assert report["total_principles"] == 3


def test_scan_conflicting_corpus_finds_conflicts():
    """A corpus with real conflicts should find them."""
    report = scan_inconsistencies(CONFLICTING_PRINCIPLES)
    assert report["conflicts_found"] >= 1
    # The conflict should be between prefer_speed and prefer_caution
    classifications = report["classifications"]
    conflict_found = any(
        c["classification"] in ("direct_conflict", "guidance_conflict")
        for c in classifications
    )
    assert conflict_found


def test_scan_pruning_rate():
    """Phase 1 should prune most pairs (deterministic filter works)."""
    # 3 principles = 3 pairs. With 2 sharing 3 tags and 1 unrelated,
    # only 1 pair should be a candidate (33% of pairs, 67% pruned)
    report = scan_inconsistencies(CONFLICTING_PRINCIPLES)
    assert report["total_pairs"] == 3
    assert report["candidate_pairs"] >= 1
    assert report["pruned_pct"] > 0
