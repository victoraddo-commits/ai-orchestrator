"""Unit tests for KAI Bet risk engine (§26) + no-bet gate (§45). Stdlib unittest."""
from __future__ import annotations

import unittest

from core.kai_betting.value_engine import compute_value
from core.kai_betting.risk_engine import assess_risk, no_bet_gate, RiskResult, GateResult


class TestRiskEngine(unittest.TestCase):
    def test_low_risk_clean_inputs(self):
        r = assess_risk(model_uncertainty=0.1, data_quality=1.0, liquidity=1.0)
        self.assertEqual(r.level, "low")
        self.assertLess(r.total, 40)

    def test_high_risk_dirty_inputs(self):
        r = assess_risk(model_uncertainty=0.9, data_quality=0.2, odds_movement=0.5,
                        liquidity=0.2, correlation_load=0.9,
                        event_flags=["lineup_unconfirmed", "weather", "referee"], bankroll_exposure=0.9)
        self.assertEqual(r.level, "high")
        self.assertGreaterEqual(r.total, 70)

    def test_components_bounded(self):
        r = assess_risk(model_uncertainty=5.0, data_quality=-1.0, odds_movement=100)
        for v in r.components.values():
            self.assertGreaterEqual(v, 0.0)
            self.assertLessEqual(v, 100.0)


class TestNoBetGate(unittest.TestCase):
    def setUp(self):
        # edge 0.15 @ 2.0 -> high value
        self.good_value = compute_value(0.65, 2.0)
        self.none_value = compute_value(0.45, 2.0)
        self.low_risk = assess_risk(model_uncertainty=0.1, data_quality=1.0, liquidity=1.0)
        self.high_risk = assess_risk(model_uncertainty=0.9, data_quality=0.2, liquidity=0.2,
                                     correlation_load=0.9, event_flags=["a", "b", "c", "d"],
                                     bankroll_exposure=0.9)

    def test_bet_on_good_value_low_risk(self):
        g = no_bet_gate(self.good_value, self.low_risk)
        self.assertEqual(g.decision, "BET")

    def test_no_bet_when_no_value(self):
        g = no_bet_gate(self.none_value, self.low_risk)
        self.assertEqual(g.decision, "NO BET")

    def test_no_bet_when_stale(self):
        g = no_bet_gate(self.good_value, self.low_risk, stale_odds=True)
        self.assertEqual(g.decision, "NO BET")
        self.assertIn("stale", " ".join(g.reasons))

    def test_no_bet_when_lineup_unconfirmed(self):
        g = no_bet_gate(self.good_value, self.low_risk, lineup_confirmed=False)
        self.assertEqual(g.decision, "NO BET")

    def test_no_bet_when_data_poor(self):
        g = no_bet_gate(self.good_value, self.low_risk, data_quality=0.3)
        self.assertEqual(g.decision, "NO BET")

    def test_no_bet_when_correlation_exceeded(self):
        g = no_bet_gate(self.good_value, self.low_risk, correlation_ok=False)
        self.assertEqual(g.decision, "NO BET")

    def test_no_bet_when_high_risk(self):
        g = no_bet_gate(self.good_value, self.high_risk)
        self.assertEqual(g.decision, "NO BET")

    def test_watch_on_low_value(self):
        low = compute_value(0.53, 2.0)  # low value
        g = no_bet_gate(low, self.low_risk)
        self.assertEqual(g.decision, "WATCH")

    def test_no_bet_is_valid_outcome(self):
        # overall: a weak candidate must not become a bet
        g = no_bet_gate(compute_value(0.51, 2.0), self.high_risk)
        self.assertIn(g.decision, ("NO BET", "WATCH"))


if __name__ == "__main__":
    unittest.main()
