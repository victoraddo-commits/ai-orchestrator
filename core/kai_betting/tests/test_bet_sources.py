"""Unit tests for KAI Bet source adapters (§21/§23). Pure normalisers."""
from __future__ import annotations

import unittest

from core.kai_betting.data_sources.verify_sources import (
    normalise_markets, results_to_form, goals_for_against,
)


class TestSources(unittest.TestCase):
    def test_normalise_markets(self):
        payload = {"data": {"events": [{"markets": [
            {"id": "1", "desc": "1X2", "group": "Main", "outcomes": [
                {"desc": "Home", "odds": "1.08"}, {"desc": "Draw", "odds": "15.8"}]},
            {"id": "18", "desc": "Over/Under", "specifier": "total=2.5", "outcomes": [{"desc": "Over", "odds": "1.5"}]},
        ]}]}}
        out = normalise_markets(payload)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["name"], "1X2")
        self.assertEqual(out[0]["outcomes"][0]["desc"], "Home")
        self.assertEqual(out[1]["specifier"], "total=2.5")

    def test_normalise_markets_none(self):
        self.assertEqual(normalise_markets(None), [])

    def test_results_to_form(self):
        events = [
            {"idHomeTeam": "10", "intHomeScore": "3", "intAwayScore": "1"},   # W (home)
            {"idHomeTeam": "20", "intHomeScore": "0", "intAwayScore": "0"},   # D (away)
            {"idHomeTeam": "10", "intHomeScore": "0", "intAwayScore": "2"},   # L (home)
        ]
        self.assertEqual(results_to_form(events, "10"), ["W", "D", "L"])
        self.assertEqual(results_to_form(events, "20"), ["L", "D", "W"])  # from team 20's view

    def test_goals_for_against(self):
        events = [
            {"idHomeTeam": "10", "intHomeScore": "3", "intAwayScore": "1"},
            {"idHomeTeam": "20", "intHomeScore": "0", "intAwayScore": "2"},
        ]
        r = goals_for_against(events, "10")
        self.assertEqual(r["games"], 2)
        self.assertAlmostEqual(r["goals_for_pg"], 2.5)   # 3 + 2
        self.assertAlmostEqual(r["goals_against_pg"], 0.5)  # 1 + 0

    def test_missing_scores_skipped(self):
        events = [{"idHomeTeam": "10"}, {"idHomeTeam": "10", "intHomeScore": "1", "intAwayScore": "0"}]
        self.assertEqual(results_to_form(events, "10"), ["W"])


if __name__ == "__main__":
    unittest.main()
