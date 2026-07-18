from __future__ import annotations

import unittest
from decimal import Decimal

from runner.config import Curiosity, CuriosityBudget, CuriosityFrontier, CuriosityRatchet
from runner.curiosity import (
    CuriosityConfigError,
    effective_cycles_per_day,
    should_explore,
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


if __name__ == "__main__":
    unittest.main()
