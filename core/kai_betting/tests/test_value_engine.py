"""Unit tests for the KAI Bet value engine (§13/§14). Stdlib unittest (no deps)."""
from __future__ import annotations

import unittest

from core.kai_betting.value_engine import (
    compute_value, implied_probability, ev_per_unit, kelly_fraction,
    VALUE_LOW, VALUE_MODERATE, VALUE_HIGH,
)


class TestValueEngine(unittest.TestCase):
    def test_implied_probability(self):
        self.assertEqual(implied_probability(2.0), 0.5)
        self.assertAlmostEqual(implied_probability(1.5), 0.6666667, places=6)
        self.assertIsNone(implied_probability(1.0))
        self.assertIsNone(implied_probability(None))

    def test_ev_per_unit(self):
        self.assertAlmostEqual(ev_per_unit(0.5, 2.0), 0.0)
        self.assertAlmostEqual(ev_per_unit(0.6, 2.0), 0.2)
        self.assertAlmostEqual(ev_per_unit(0.4, 2.0), -0.2)

    def test_kelly_fraction(self):
        self.assertAlmostEqual(kelly_fraction(0.6, 2.0), 0.2)
        self.assertEqual(kelly_fraction(0.4, 2.0), 0.0)

    def test_value_none_when_ev_negative(self):
        r = compute_value(0.45, 2.0)
        self.assertEqual(r.value, "none")
        self.assertLess(r.ev, 0)
        self.assertEqual(r.kelly_fraction, 0.0)

    def test_value_bands(self):
        self.assertEqual(compute_value(0.53, 2.0).value, "low")       # edge .03
        self.assertEqual(compute_value(0.57, 2.0).value, "moderate")  # edge .07
        r = compute_value(0.65, 2.0)
        self.assertEqual(r.value, "high")                             # edge .15
        self.assertAlmostEqual(r.edge_adjusted, 0.15)
        self.assertTrue(VALUE_LOW < VALUE_MODERATE < VALUE_HIGH)

    def test_uncertainty_haircuts(self):
        certain = compute_value(0.65, 2.0, uncertainty=0.0)
        unsure = compute_value(0.65, 2.0, uncertainty=0.5)
        self.assertLess(unsure.edge_adjusted, certain.edge_adjusted)
        self.assertLess(unsure.kelly_fraction, certain.kelly_fraction)

    def test_kelly_cap(self):
        r = compute_value(0.9, 2.0, uncertainty=0.0, kelly_cap=0.1)
        self.assertLessEqual(r.kelly_fraction, 0.1)

    def test_fair_odds_and_sensitivity(self):
        r = compute_value(0.5, 2.0)
        self.assertAlmostEqual(r.fair_odds, 2.0)
        self.assertAlmostEqual(r.price_sensitivity, 0.5)

    def test_bad_inputs(self):
        with self.assertRaises(ValueError):
            compute_value(1.2, 2.0)
        with self.assertRaises(ValueError):
            compute_value(0.5, 1.0)


if __name__ == "__main__":
    unittest.main()
