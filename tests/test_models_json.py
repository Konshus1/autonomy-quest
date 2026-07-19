"""Verify data/models.json is a trustworthy price cache for the gateway.

This is a guard against the exact bug the file's own header warns about: a price cache that
is present but wrong (or empty) silently corrupts the budget. The test exercises the REAL
runner.gateway.Gateway._cost code path — not a reimplementation — so if the field contract
_cost depends on ever changes, this test breaks loudly.

Run:  python -m unittest tests.test_models_json -v   (from the repo root)
"""

from __future__ import annotations

import json
import unittest
from decimal import Decimal
from pathlib import Path

from runner.gateway import Gateway  # the production object under test

REPO = Path(__file__).resolve().parent.parent
MODELS_JSON = REPO / "data" / "models.json"


def _price_map():
    # Construct the price map EXACTLY as Gateway.__init__ does:
    #   self.prices = {m["id"]: m for m in json.loads(_DATA.read_text())["models"]}
    return {m["id"]: m for m in json.loads(MODELS_JSON.read_text())["models"]}


def _cost(prices, model, tin, tout) -> Decimal:
    # Invoke the ACTUAL production _cost against a bare Gateway instance whose only state is
    # .prices. This proves the seeded file makes the real code produce a real number.
    g = Gateway.__new__(Gateway)
    g.prices = prices
    return Gateway._cost(g, model, tin, tout)


class TestModelsJson(unittest.TestCase):
    def setUp(self):
        self.doc = json.loads(MODELS_JSON.read_text())
        self.models = self.doc["models"]
        self.prices = _price_map()

    # (a) wrapper shape + required fields --------------------------------------
    def test_wrapper_shape(self):
        self.assertIsInstance(self.doc, dict)
        self.assertIn("models", self.doc)
        self.assertIsInstance(self.models, list)
        self.assertGreater(len(self.models), 0, "price cache must not be empty")

    def test_required_fields_per_entry(self):
        seen = set()
        for m in self.models:
            self.assertIn("id", m)
            mid = m["id"]
            self.assertIsInstance(mid, str)
            self.assertTrue(mid, "empty id")
            self.assertNotIn(mid, seen, f"duplicate id {mid}")
            seen.add(mid)
            # price_in is the field _cost keys off ("if not p.get('price_in')").
            for f in ("price_in", "price_out"):
                self.assertIn(f, m, f"{mid} missing {f}")
                self.assertIsInstance(m[f], (int, float), f"{mid}.{f} not numeric")
                self.assertGreater(m[f], 0, f"{mid}.{f} must be > 0")

    def test_prices_within_sane_bounds(self):
        # Unit-confusion tripwire: per-MILLION-token USD prices live roughly in [0.001, 200].
        # A per-token value left unmultiplied (~1e-7) or a 1000x error would fail here.
        for m in self.models:
            for f in ("price_in", "price_out"):
                self.assertGreaterEqual(m[f], 0.001, f"{m['id']}.{f} implausibly low (unit bug?)")
                self.assertLessEqual(m[f], 200, f"{m['id']}.{f} implausibly high (unit bug?)")

    # (b) the real _cost path returns a non-zero, plausible cost ---------------
    def test_known_model_prices_nonzero(self):
        # Pick any seeded model and prove the "no verified price" warning would NOT fire.
        mid = self.models[0]["id"]
        cost = _cost(self.prices, mid, 10_000, 2_000)
        self.assertIsInstance(cost, Decimal)
        self.assertGreater(cost, Decimal("0"), f"{mid} priced at 0 — warning would fire")

    def test_cost_matches_formula(self):
        # Cross-check the real _cost against the documented contract for a specific entry.
        m = self.prices[self.models[0]["id"]]
        tin, tout = 10_000, 2_000
        expected = (Decimal(str(m["price_in"])) * tin
                    + Decimal(str(m["price_out"])) * tout) / Decimal(1_000_000)
        self.assertEqual(_cost(self.prices, m["id"], tin, tout), expected)

    def test_unpriced_model_records_zero(self):
        # Negative control: an id NOT in the cache must fall through to the honest-zero branch,
        # proving the non-zero result above is caused by the SEED, not by _cost always returning >0.
        self.assertEqual(_cost(self.prices, "nonexistent/model-xyz", 10_000, 2_000), Decimal("0"))

    def test_every_seeded_model_prices_nonzero(self):
        for m in self.models:
            self.assertGreater(
                _cost(self.prices, m["id"], 1_000, 1_000), Decimal("0"),
                f"{m['id']} would trigger the 'no verified price' warning",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
