"""Task #4407 stage 8 — merge_sync: packet build passes the gate validator; submit is best-effort."""

from __future__ import annotations

from ralph_portable.manager_merge_gate import validate_manager_merge_decision
from runner.evaluate import EvalResult
from runner import merge_sync


def _ev(verdict):
    return EvalResult(productive=(verdict != "rework"), test_level="unit", tests_passed=True,
                      intent_covered=True, completeness_ok=True, coherence_ok=(verdict != "escalate"),
                      verdict=verdict, detail="tests=True level=unit coherent=%s" % (verdict != "escalate"))


def test_pass_packet_validates_as_approve_merge():
    p = merge_sync.build_merge_packet(ev=_ev("pass"), run_id=7, work=None,
                                      manager_handle="ralph-manager", cohort_id="cohort-run-7")
    assert validate_manager_merge_decision(p) == []          # clean per the gate
    assert p["decision"] == "approve_merge"
    assert "run #7" in p["rationale"]
    assert set(p) <= {"decision", "manager_handle", "cohort_id", "rationale", "uncertainty_note"}


def test_escalate_packet_carries_uncertainty_note():
    p = merge_sync.build_merge_packet(ev=_ev("escalate"), run_id=8, work=None,
                                      manager_handle="ralph-manager", cohort_id="cohort-run-8")
    assert validate_manager_merge_decision(p) == []
    assert p["decision"] == "escalate_human"
    assert p.get("uncertainty_note")                          # required when escalating


def test_submit_is_best_effort_on_dead_endpoint():
    p = merge_sync.build_merge_packet(ev=_ev("pass"), run_id=1, work=None,
                                      manager_handle="m", cohort_id="c")
    assert merge_sync.submit_merge_decision("http://127.0.0.1:1", p, timeout=0.5) is None


def test_submit_refuses_locally_invalid_packet():
    bad = {"decision": "approve_merge", "cohort_id": "c", "rationale": "r"}  # missing manager_handle
    assert validate_manager_merge_decision(bad)              # sanity: it IS invalid
    assert merge_sync.submit_merge_decision("http://127.0.0.1:1", bad, timeout=0.5) is None
