"""Escalation ladder — the "stuck stops spending" claims, test-backed (proof-track audit).

Public claims under test (runner/escalation.py docstrings):
  - was_productive is deliberately STRICT and NOT handed back to the model to self-grade:
    the number moved, or a real pointable artifact — a "succeeded but narrated" cycle is
    unproductive ("those are the ones that burn a night").
  - the ladder climbs on consecutive unproductive cycles: autonomous -> self-nudge -> peer
    -> human -> HIBERNATE, and Hibernate STOPS the loop rather than keep spending.
  - the streak comes from the DB, not a resettable in-memory counter (a crash must not
    un-stick a stuck loop).

Scope honesty: Db is stubbed (recent_productivity); these tests prove the ladder logic,
not Postgres.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from runner.escalation import (
    HIBERNATE_AT,
    HUMAN_AT,
    PEER_AT,
    SELF_NUDGE_AT,
    Escalation,
    Hibernate,
)


class StubDb:
    def __init__(self, rows):
        self._rows = rows
        self.calls = []

    def recent_productivity(self, *, limit: int):
        self.calls.append(limit)
        return list(self._rows[:limit])


# ---- was_productive: strict, not self-graded ----------------------------------------

def test_number_moved_is_productive():
    assert Escalation.was_productive("success", True, "", Decimal("1"), Decimal("2")) is True


def test_real_pointable_artifact_is_productive():
    assert Escalation.was_productive("success", True, "fixed tests/test_x.py", Decimal("1"), Decimal("1")) is True


def test_succeeded_but_narrated_is_unproductive():
    """Success with nothing to point at is the cycle that burns a night."""
    assert Escalation.was_productive("success", True, "", Decimal("1"), Decimal("1")) is False
    assert Escalation.was_productive("success", True, "n/a", Decimal("1"), Decimal("1")) is False


def test_failure_is_not_productive_but_teaches_once():
    """A genuine failure counts as productive ONCE via the ladder's grace — consecutive
    failures still climb, because a loop failing the same way repeatedly IS stuck."""
    assert Escalation.was_productive("failed", False, "traceback", Decimal("1"), Decimal("1")) is False


# ---- the ladder: boundaries ----------------------------------------------------------

def test_ladder_autonomous_below_self_nudge():
    e = Escalation(StubDb([]))
    v = e.assess(SELF_NUDGE_AT - 1)
    assert v.level == "autonomous" and v.notify_human is False


def test_ladder_self_nudge_at_threshold():
    e = Escalation(StubDb([]))
    v = e.assess(SELF_NUDGE_AT)
    assert v.level == "self-nudge" and "Do NOT try it again" in v.guidance


def test_ladder_peer_at_threshold():
    e = Escalation(StubDb([]))
    v = e.assess(PEER_AT)
    assert v.level == "peer" and "bb_handoff" in v.guidance


def test_ladder_human_at_threshold_notifies():
    e = Escalation(StubDb([]))
    v = e.assess(HUMAN_AT)
    assert v.level == "human" and v.notify_human is True


def test_hibernate_at_threshold_stops_the_loop():
    e = Escalation(StubDb([]))
    with pytest.raises(Hibernate, match="STOPPING rather than"):
        e.assess(HIBERNATE_AT)


def test_hibernate_above_threshold_also_stops():
    e = Escalation(StubDb([]))
    with pytest.raises(Hibernate):
        e.assess(HIBERNATE_AT + 5)


# ---- the streak: from the DB, crash-safe --------------------------------------------

def _rows(seq):
    """seq is newest-first productivity flags."""
    return [{"productive": p} for p in seq]


def test_streak_counts_consecutive_unproductive_from_newest():
    e = Escalation(StubDb(_rows([False, False, False, True, False])))
    assert e.unproductive_streak() == 3


def test_streak_resets_at_first_productive():
    e = Escalation(StubDb(_rows([True, False, False])))
    assert e.unproductive_streak() == 0


def test_streak_reads_the_db_with_bounded_window():
    db = StubDb(_rows([False] * 20))
    e = Escalation(db)
    e.unproductive_streak()
    assert db.calls == [HIBERNATE_AT + 2], "the streak reads a bounded DB window, not a memory counter"


def test_empty_history_is_zero_streak():
    e = Escalation(StubDb([]))
    assert e.unproductive_streak() == 0
