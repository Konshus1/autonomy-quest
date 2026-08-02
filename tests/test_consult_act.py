"""Tests for consult-act (Slice 3) — the certainty-gated deferral.

The central property under test is the ONE-DIRECTIONAL invariant: consult-act may only
ADD caution (defer to review), never authorize or un-gate. Every case here is either
"defers" or "does not defer" — there is no path by which consult-act makes the loop act.
"""
from dataclasses import dataclass

from runner import consult_act


@dataclass
class FakeWork:
    reversible: bool = True
    spends_money: bool = False
    touches_human: bool = False
    commits: bool = False


REVERSIBLE = FakeWork(reversible=True)
SIDE_EFFECTING = FakeWork(reversible=False)
SPENDS = FakeWork(spends_money=True)
COMMITS = FakeWork(commits=True)


def test_no_governing_principle_never_defers():
    # None certainty = no principle governs this step. Consult-act has no opinion and must
    # NOT defer — the base autonomy gate already handles genuine unknowns.
    assert consult_act.should_defer(SIDE_EFFECTING, None) is False
    assert consult_act.gate_reason(SIDE_EFFECTING, None) is None


def test_low_certainty_side_effecting_defers():
    assert consult_act.should_defer(SIDE_EFFECTING, 0.10) is True
    reason = consult_act.gate_reason(SIDE_EFFECTING, 0.10)
    assert reason and "consult-act" in reason and "deferred to review" in reason


def test_high_certainty_side_effecting_does_not_defer():
    # High confidence -> consult-act does not add caution; the base gate decides.
    assert consult_act.should_defer(SIDE_EFFECTING, 0.90) is False
    assert consult_act.gate_reason(SIDE_EFFECTING, 0.90) is None


def test_low_certainty_reversible_does_not_defer():
    # Reversible, no-side-effect work is not worth deferring on a low certainty alone.
    assert consult_act.should_defer(REVERSIBLE, 0.01) is False


def test_spends_money_and_commits_count_as_side_effecting():
    assert consult_act.should_defer(SPENDS, 0.10) is True
    assert consult_act.should_defer(COMMITS, 0.10) is True


def test_threshold_boundary_is_exclusive():
    t = consult_act.CONSULT_ACT_GATE_THRESHOLD
    assert consult_act.should_defer(SIDE_EFFECTING, t) is False       # exactly at threshold: not below
    assert consult_act.should_defer(SIDE_EFFECTING, t - 0.001) is True  # just below: defer


def test_is_side_effecting_matches_the_gate_signals():
    assert consult_act.is_side_effecting(REVERSIBLE) is False
    assert consult_act.is_side_effecting(SIDE_EFFECTING) is True
    assert consult_act.is_side_effecting(SPENDS) is True
    assert consult_act.is_side_effecting(FakeWork(touches_human=True)) is True


def test_only_ever_adds_caution_never_authorizes():
    # Property sweep: across the whole certainty range and both risk classes, the ONLY
    # outputs are True (defer) or False (no-op). There is no return value that means "act".
    for c in (None, 0.0, 0.2, 0.35, 0.5, 1.0):
        for w in (REVERSIBLE, SIDE_EFFECTING, SPENDS, COMMITS):
            assert consult_act.should_defer(w, c) in (True, False)
