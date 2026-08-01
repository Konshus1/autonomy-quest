"""Task #4407 slice 2a — principle mining: candidate fuzzy causal edges from run outcomes."""

from __future__ import annotations

from ralph_portable.causal_edges import validate_causal_edge
from ralph_portable.principle_mining import mine_causal_edges


def _obs(**over):
    o = {
        "work_kind": "send_followup", "succeeded": True,
        "measure_before": 10, "measure_after": 12,
        "learning_insight": "followups within a day lift replies",
        "learning_confidence": 0.6,
        "run_id": 1, "work_id": 1, "learning_id": 1,
    }
    o.update(over)
    return o


def test_mines_fuzzy_edge_with_provenance():
    edges = mine_causal_edges([_obs()])
    assert len(edges) == 1
    e = edges[0]
    assert e["cause"] == "send_followup" and e["effect"] == "measure_up"
    assert e["formality"] == "fuzzy" and e["directness"] == "judgment" and e["mined"] is True
    assert e["predicted_certainty"] <= 0.34  # capped fuzzy
    assert e["evidence_run_ids"] == [1]
    assert e["provenance"][0]["learning_id"] == 1
    assert validate_causal_edge(e) == []   # a well-formed fuzzy edge


def test_skips_failed_and_non_moving_runs():
    assert mine_causal_edges([_obs(succeeded=False)]) == []
    assert mine_causal_edges([_obs(measure_before=10, measure_after=10)]) == []  # no move
    # a measure DROP is still a (negative) causal effect worth recording
    down = mine_causal_edges([_obs(measure_before=10, measure_after=8)])
    assert down and down[0]["effect"] == "measure_down"


def test_min_confidence_filter():
    assert mine_causal_edges([_obs(learning_confidence=0.2)], min_confidence=0.5) == []
    assert len(mine_causal_edges([_obs(learning_confidence=0.6)], min_confidence=0.5)) == 1


def test_dedup_and_accumulate_provenance():
    obs = [
        _obs(run_id=1, learning_id=1, learning_confidence=0.2),
        _obs(run_id=2, learning_id=2, learning_confidence=0.5),   # same cause+effect, new run
        _obs(work_kind="other", run_id=3, learning_id=3),          # different cause
    ]
    edges = mine_causal_edges(obs)
    by_cause = {e["cause"]: e for e in edges}
    assert set(by_cause) == {"send_followup", "other"}
    acc = by_cause["send_followup"]
    # observation BREADTH = distinct supporting runs; the miner does NOT set support_count
    # (that is earned by the surprise loop). See BB #746.
    assert acc["observed_runs"] == 2
    assert "support_count" not in acc
    assert acc["evidence_run_ids"] == [1, 2]
    assert len(acc["provenance"]) == 2
    # certainty is the max seen, still capped fuzzy
    assert acc["predicted_certainty"] == min(0.5, 0.34)


def test_one_run_many_learnings_counts_as_one_supporting_run():
    # runs->learnings is many-to-one: a single run that recorded three learnings must NOT
    # inflate observed_runs to 3 or duplicate the run in evidence_run_ids (adversarial finding 2).
    obs = [
        _obs(run_id=5, learning_id=10, learning_confidence=0.3, learning_insight="a"),
        _obs(run_id=5, learning_id=11, learning_confidence=0.6, learning_insight="b"),  # best conf
        _obs(run_id=5, learning_id=12, learning_confidence=0.4, learning_insight="c"),
    ]
    edges = mine_causal_edges(obs)
    assert len(edges) == 1
    e = edges[0]
    assert e["observed_runs"] == 1
    assert e["evidence_run_ids"] == [5]              # no [5, 5, 5]
    assert len(e["provenance"]) == 1
    assert e["provenance"][0]["insight"] == "b"      # the run's highest-confidence learning
    assert e["predicted_certainty"] == min(0.6, 0.34)
