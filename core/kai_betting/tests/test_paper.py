"""Unit tests for KAI Bet paper betting (§17). Stdlib unittest."""
from __future__ import annotations

import unittest

from core.kai_betting.paper import settle_profit, compute_performance


class TestPaperSettlement(unittest.TestCase):
    def test_won_lost_void(self):
        self.assertAlmostEqual(settle_profit(10, 2.0, "won"), 10.0)
        self.assertAlmostEqual(settle_profit(10, 1.5, "won"), 5.0)
        self.assertAlmostEqual(settle_profit(10, 2.0, "lost"), -10.0)
        self.assertEqual(settle_profit(10, 2.0, "void"), 0.0)

    def test_unknown_outcome(self):
        with self.assertRaises(ValueError):
            settle_profit(10, 2.0, "cancelled")

    def test_performance_math(self):
        rows = [
            {"status": "won", "stake": 10, "odds": 2.0, "profit_loss": 10.0},
            {"status": "lost", "stake": 10, "odds": 1.8, "profit_loss": -10.0},
            {"status": "open", "stake": 5, "odds": 3.0, "profit_loss": None},
        ]
        p = compute_performance(rows)
        self.assertEqual(p["bets"], 3)
        self.assertEqual(p["open"], 1)
        self.assertEqual(p["settled"], 2)
        self.assertEqual(p["won"], 1)
        self.assertEqual(p["staked"], 20.0)
        self.assertAlmostEqual(p["profit_loss"], 0.0)
        self.assertAlmostEqual(p["roi"], 0.0)
        self.assertAlmostEqual(p["win_rate"], 0.5)

    def test_performance_empty(self):
        p = compute_performance([])
        self.assertEqual(p["bets"], 0)
        self.assertIsNone(p["roi"])
        self.assertIsNone(p["win_rate"])

    def test_performance_positive_roi(self):
        rows = [
            {"status": "won", "stake": 10, "odds": 3.0, "profit_loss": 20.0},
            {"status": "lost", "stake": 10, "odds": 2.0, "profit_loss": -10.0},
        ]
        p = compute_performance(rows)
        self.assertAlmostEqual(p["roi"], 0.5)


if __name__ == "__main__":
    unittest.main()
