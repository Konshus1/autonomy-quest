from __future__ import annotations

import unittest

from scripts.verify_config import validate_mission_config


def instance(goal, *, target=None, target_query=None, monthly_hard_usd=0):
    measure = {"what": "revenue", "where": "select 1", "goal": goal}
    if target is not None:
        measure["target"] = target
    if target_query:
        measure["target_query"] = target_query
    return {
        "mission": {"objective": "grow revenue", "measure": measure},
        "budget": {"money": {"monthly_hard_usd": monthly_hard_usd}},
    }


class VerifyConfigTests(unittest.TestCase):
    def test_reach_and_maintain_requires_target_ceiling(self):
        missing = validate_mission_config(instance("reach_and_maintain"))
        self.assertFalse(missing.ok)
        self.assertEqual(missing.code, "missing_ceiling")

        ok = validate_mission_config(instance("reach_and_maintain", target=10))
        self.assertTrue(ok.ok)
        self.assertEqual(ok.code, "reach_ceiling")

    def test_maximize_requires_spend_cap_not_target_ceiling(self):
        missing_cap = validate_mission_config(instance("maximize", target=10, monthly_hard_usd=0))
        self.assertFalse(missing_cap.ok)
        self.assertEqual(missing_cap.code, "maximize_without_spend_cap")

        ok = validate_mission_config(instance("maximize", monthly_hard_usd=25))
        self.assertTrue(ok.ok)
        self.assertEqual(ok.code, "maximize_spend_cap")
        self.assertNotIn("ceiling", ok.message.lower())


if __name__ == "__main__":
    unittest.main()
