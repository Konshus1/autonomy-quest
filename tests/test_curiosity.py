from __future__ import annotations

import unittest
from decimal import Decimal
from typing import Any

from runner.config import Curiosity, CuriosityBudget, CuriosityFrontier, CuriosityRatchet
from runner.curiosity import (
    CuriosityConfigError,
    effective_cycles_per_day,
    should_explore,
    should_probe_incoherence,
    validate_config,
)


def curiosity(**overrides) -> Curiosity:
    base = Curiosity(
        enabled=True,
        budget=CuriosityBudget(cycles_per_day=2, max_cost_usd_per_day=1.0),
        frontier=CuriosityFrontier(
            source="select id, topic from external_catalog where in_scope = true order by priority desc",
            authority="external_catalog",
            target=10,
            goal="reach_and_maintain",
            max_items_per_cycle=1,
        ),
        ratchet=CuriosityRatchet(window_cycles=10, shrink_factor=0.5, floor_cycles_per_day=0),
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


class CuriosityTests(unittest.TestCase):
    def test_disabled_curiosity_is_noop(self):
        c = Curiosity(enabled=False)
        validate_config(c)
        decision = should_explore(
            c,
            spent_today=Decimal("0"),
            mission_soft_cap_reached=False,
            curiosity_cycles_today=0,
            curiosity_cost_today=Decimal("0"),
            promoted_recent=0,
        )

        self.assertFalse(decision.run)

    def test_enabled_curiosity_requires_bounded_budget(self):
        c = curiosity(budget=CuriosityBudget(cycles_per_day=0, max_cost_usd_per_day=1.0))
        with self.assertRaisesRegex(CuriosityConfigError, "cycles_per_day"):
            validate_config(c)

        c = curiosity(budget=CuriosityBudget(cycles_per_day=1, max_cost_usd_per_day=0))
        with self.assertRaisesRegex(CuriosityConfigError, "max_cost_usd"):
            validate_config(c)

    def test_frontier_must_not_be_loop_defined(self):
        c = curiosity(frontier=CuriosityFrontier(
            source="select id, insight from learnings order by confidence desc",
            authority="local learnings",
            target=10,
            goal="reach_and_maintain",
            max_items_per_cycle=1,
        ))

        with self.assertRaisesRegex(CuriosityConfigError, "loop-owned"):
            validate_config(c)

    def test_ratchet_shrinks_without_promoted_curiosity_learning(self):
        c = curiosity()
        self.assertEqual(effective_cycles_per_day(c, promoted_recent=0, cycles_recent=0), 2)
        self.assertEqual(effective_cycles_per_day(c, promoted_recent=1, cycles_recent=10), 2)
        self.assertEqual(effective_cycles_per_day(c, promoted_recent=0, cycles_recent=10), 1)

    def test_cost_pressure_throttles_curiosity_first(self):
        c = curiosity()
        decision = should_explore(
            c,
            spent_today=Decimal("5"),
            mission_soft_cap_reached=True,
            curiosity_cycles_today=0,
            curiosity_cost_today=Decimal("0"),
            promoted_recent=1,
            frontier_current=Decimal("1"),
            frontier_target=Decimal("10"),
        )

        self.assertFalse(decision.run)
        self.assertIn("cost pressure", decision.reason)

    def test_frontier_overshoot_trips_guardian(self):
        c = curiosity()
        decision = should_explore(
            c,
            spent_today=Decimal("0"),
            mission_soft_cap_reached=False,
            curiosity_cycles_today=0,
            curiosity_cost_today=Decimal("0"),
            promoted_recent=1,
            frontier_current=Decimal("16"),
            frontier_target=Decimal("10"),
        )

        self.assertFalse(decision.run)
        self.assertIn("overshoot", decision.reason)

    def test_curiosity_budget_cannot_be_drawn_after_exhaustion(self):
        c = curiosity()
        by_cycles = should_explore(
            c,
            spent_today=Decimal("0"),
            mission_soft_cap_reached=False,
            curiosity_cycles_today=2,
            curiosity_cost_today=Decimal("0"),
            promoted_recent=1,
            frontier_current=Decimal("1"),
            frontier_target=Decimal("10"),
        )
        by_cost = should_explore(
            c,
            spent_today=Decimal("0"),
            mission_soft_cap_reached=False,
            curiosity_cycles_today=0,
            curiosity_cost_today=Decimal("1.00"),
            promoted_recent=1,
            frontier_current=Decimal("1"),
            frontier_target=Decimal("10"),
        )

        self.assertFalse(by_cycles.run)
        self.assertFalse(by_cost.run)


class IncoherenceProbeTests(unittest.TestCase):
    """G1 / BB #2301 — should_probe_incoherence: the incoherence-triggered curiosity gate.

    Budget/ratchet/cost floor is the SAME _budget_gate as the frontier path (one place); the
    trigger adds exactly one check of its own: the outcome must actually contradict the model
    (gate4). No probe on a coherent cycle, no probe past the shared budget.
    """

    def _probe(self, c, **over):
        kwargs: dict[str, Any] = dict(
            predicted_certainty=0.9, actual_success=False,   # incoherent (gate4 fail)
            spent_today=Decimal("0"), mission_soft_cap_reached=False,
            curiosity_cycles_today=0, curiosity_cost_today=Decimal("0"),
            promoted_recent=1, cycles_recent=10,
        )
        kwargs.update(over)
        return should_probe_incoherence(c, **kwargs)

    def test_disabled_curiosity_never_probes(self):
        decision = self._probe(Curiosity(enabled=False))
        self.assertFalse(decision.run)
        self.assertIn("disabled", decision.reason)

    def test_coherent_outcome_never_probes(self):
        c = curiosity()
        # high-certainty prediction that HELD -> coherent -> no probe, even with full budget
        decision = self._probe(c, actual_success=True)
        self.assertFalse(decision.run)
        self.assertIn("coherent", decision.reason)
        # low-certainty miss is coherent too (gate4 only fires on confident contradictions)
        decision = self._probe(c, predicted_certainty=0.3)
        self.assertFalse(decision.run)
        self.assertIn("coherent", decision.reason)
        # no governing principle at all -> coherent -> no probe
        decision = self._probe(c, predicted_certainty=None)
        self.assertFalse(decision.run)

    def test_incoherent_with_budget_probes(self):
        decision = self._probe(curiosity())
        self.assertTrue(decision.run)
        self.assertIn("incoherence probe", decision.reason)
        self.assertEqual(decision.budget_cycles_per_day, 2)

    def test_mission_cost_pressure_throttles_probe_first(self):
        decision = self._probe(curiosity(), mission_soft_cap_reached=True, spent_today=Decimal("5"))
        self.assertFalse(decision.run)
        self.assertIn("cost pressure", decision.reason)

    def test_ratchet_zero_blocks_probe_but_not_escalation(self):
        # cycles_per_day=1, shrink 0.5, floor 0 -> no promotion in window shrinks to int(0.5)=0:
        # the probe is skipped. The escalate verdict is untouched — that's evaluate's job.
        c = curiosity(budget=CuriosityBudget(cycles_per_day=1, max_cost_usd_per_day=1.0))
        decision = self._probe(c, promoted_recent=0, cycles_recent=10)
        self.assertFalse(decision.run)
        self.assertIn("ratchet", decision.reason)

    def test_cycle_and_cost_budgets_bound_probe(self):
        by_cycles = self._probe(curiosity(), curiosity_cycles_today=2)
        by_cost = self._probe(curiosity(), curiosity_cost_today=Decimal("1.00"))
        self.assertFalse(by_cycles.run)
        self.assertFalse(by_cost.run)

    def test_invalid_budget_state_refused(self):
        decision = self._probe(curiosity(), spent_today=Decimal("-1"))
        self.assertFalse(decision.run)
        self.assertIn("invalid budget state", decision.reason)

    def test_unbounded_curiosity_still_fails_loud(self):
        c = curiosity(budget=CuriosityBudget(cycles_per_day=0, max_cost_usd_per_day=1.0))
        with self.assertRaisesRegex(CuriosityConfigError, "cycles_per_day"):
            self._probe(c)


if __name__ == "__main__":
    unittest.main()
