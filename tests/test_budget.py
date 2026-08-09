"""Budget caps — the money-trust claims, test-backed (proof-track audit).

Public claims under test (runner/budget.py docstrings + README's local-control framing):
  - the HARD cap stops the loop ("The loop has stopped and will not spend further") and has
    NO override flag — raising it is a human act in instance.yaml, never the system's.
  - the check fires BEFORE spending (would_exceed), not from a receipt after.
  - the SOFT cap throttles and tells the human but does NOT halt (honest semantics — only
    the hard cap stops the loop).
  - SUBSCRIPTION mode (caps == 0, "not applicable") must never read 0>=0 as exceeded and
    halt a loop that was never dollar-metered — a real historical bootstrap bug, now pinned
    by a regression test.
  - spend is computed from the RUNS TABLE (db.sum_cost), not a resettable in-memory counter.

Scope honesty: Db is stubbed (sum_cost); these tests prove the cap LOGIC, not Postgres.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from runner.budget import Budget, BudgetExceeded


class StubDb:
    def __init__(self, today="0.00", month="0.00"):
        self._today = Decimal(today)
        self._month = Decimal(month)
        self.calls = []
        self.plan_expense = Decimal("0")
        self.reservations = {}

    def sum_cost(self, *, since: str) -> Decimal:
        self.calls.append(since)
        return self._today if since == "today" else self._month

    def sum_plan_expense(self) -> Decimal:
        return self.plan_expense

    def reserve_plan_expense(self, work_id, amount):
        key = ("work", work_id)
        if key not in self.reservations:
            self.reservations[key] = Decimal(amount)
            self.plan_expense += Decimal(amount)

    def meta_mode_expense_reservation(self, decision_id):
        return self.reservations.get(("meta", decision_id))

    def reserve_meta_mode_expense(self, decision_id, amount):
        key = ("meta", decision_id)
        if key not in self.reservations:
            self.reservations[key] = Decimal(amount)
            self.plan_expense += Decimal(amount)


def _budget(today="0.00", month="0.00", daily_soft="5.0", monthly_hard="100.0"):
    cfg = SimpleNamespace(money=SimpleNamespace(daily_soft_usd=daily_soft,
                                                monthly_hard_usd=monthly_hard))
    db = StubDb(today, month)
    return Budget(cfg, db), db


# ---- hard cap: the one that is not negotiable ---------------------------------------

def test_hard_cap_raises_at_cap_and_says_the_loop_stopped():
    b, _ = _budget(month="100.00")
    with pytest.raises(BudgetExceeded, match="will not spend further"):
        b.check_hard_cap()


def test_hard_cap_raises_over_cap():
    b, _ = _budget(month="150.25")
    with pytest.raises(BudgetExceeded):
        b.check_hard_cap()


def test_hard_cap_passes_under_cap():
    b, db = _budget(month="99.99")
    b.check_hard_cap()  # no raise
    assert db.calls == ["month"], "the hard cap reads the runs table (this month's actual spend)"


def test_unmetered_subscription_mode_never_halts_on_zero():
    """The 0>=0 trap: caps of 0 mean 'not applicable', NOT 'you may spend nothing'."""
    b, _ = _budget(month="0.00", monthly_hard="0.0")
    b.check_hard_cap()          # must NOT raise
    assert b.metered is False
    assert b.check_soft_cap() is False, "unmetered soft cap is inapplicable, not 'over'"


def test_would_exceed_checks_before_spending():
    b, _ = _budget(month="90.00")
    assert b.would_exceed(Decimal("9.99")) is False
    assert b.would_exceed(Decimal("10.00")) is True, "an estimate that reaches the cap must block the call"


# ---- soft cap: throttle + tell the human, never halt ---------------------------------

def test_soft_cap_over_returns_true_but_never_raises():
    b, _ = _budget(today="5.00", daily_soft="5.0")
    assert b.check_soft_cap() is True          # over: throttle
    # ...and it returned normally — the soft cap is not the stop. Only the hard cap stops.


def test_soft_cap_under_returns_false():
    b, _ = _budget(today="4.99", daily_soft="5.0")
    assert b.check_soft_cap() is False


def test_soft_cap_predicate_form_and_zero_disabled():
    b, _ = _budget(today="10.00", daily_soft="0.0")   # 0 soft cap = disabled, not 'always over'
    assert b.soft_cap_reached() is False
    b2, _ = _budget(today="10.00", daily_soft="5.0")
    assert b2.soft_cap_reached() is True
    assert b2.soft_cap_reached(Decimal("1.00")) is False, "explicit spend argument overrides the read"


def test_aggregate_plan_reservations_cannot_split_past_hard_cap():
    b, db = _budget(month="0", monthly_hard="50")
    b.reserve_plan_expense(1, Decimal("30"))
    with pytest.raises(BudgetExceeded, match="aggregate evaluation budget"):
        b.reserve_plan_expense(2, Decimal("30"))
    assert db.plan_expense == Decimal("30")


def test_sequential_meta_decisions_reserve_expense_independently():
    budget, db = _budget(month="0")
    budget.reserve_plan_expense(7, Decimal("1.25"), meta_mode_decision_id=101)
    budget.reserve_plan_expense(7, Decimal("2.50"), meta_mode_decision_id=102)
    assert db.plan_expense == Decimal("3.75")
    assert set(db.reservations) == {("meta", 101), ("meta", 102)}


def test_reserved_meta_expense_restart_is_idempotent_near_cap():
    budget, db = _budget(month="0", monthly_hard="50")
    db.reserve_meta_mode_expense(101, Decimal("30"))
    budget.reserve_plan_expense(7, Decimal("30"), meta_mode_decision_id=101)
    assert db.plan_expense == Decimal("30")
    db.plan_expense = Decimal("50")
    # The reservation is idempotent, but metered ACT/REFLECT would be new spend. Exact-cap
    # recovery therefore remains blocked until the operator raises the cap.
    with pytest.raises(BudgetExceeded):
        budget.check_hard_cap()
