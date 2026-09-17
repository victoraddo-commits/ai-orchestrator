"""Unit tests for KAI Bet history → screening (§6). Pure parsers + model."""
from __future__ import annotations

import unittest

from core.kai_betting.history import (
    parse_matches, league_avg_goals, team_strength, independent_markets,
)


DOC = {"matches": [
    {"team1": "Bayern Munich", "team2": "Union Berlin", "score": {"ft": [3, 1]}},
    {"team1": "Union Berlin", "team2": "Bayern Munich", "score": {"ft": [0, 2]}},
    {"team1": "Bayern Munich", "team2": "Dortmund", "score": {"ft": [2, 2]}},
    {"team1": "Dortmund", "team2": "Union Berlin", "score": {"ft": [1, 0]}},
    {"team1": "Bayern Munich", "team2": "Leipzig", "score": {}},          # unscored -> skipped
]}


class TestHistory(unittest.TestCase):
    def test_parse_skips_unscored(self):
        m = parse_matches(DOC)
        self.assertEqual(len(m), 4)
        self.assertEqual(m[0]["hg"], 3)

    def test_league_avg(self):
        m = parse_matches(DOC)
        self.assertAlmostEqual(league_avg_goals(m), (4 + 2 + 4 + 1) / 4)

    def test_team_strength_bayern(self):
        m = parse_matches(DOC)
        s = team_strength(m, "Bayern Munich")
        self.assertEqual(s["games"], 3)
        self.assertAlmostEqual(s["goals_for_pg"], (3 + 2 + 2) / 3, places=3)
        self.assertEqual(s["form"][0], "D")  # most recent first (2-2 vs Dortmund)

    def test_team_strength_handles_alias(self):
        m = parse_matches(DOC)
        s = team_strength(m, "Bayern München")  # alias normalises
        self.assertEqual(s["games"], 3)

    def test_independent_markets_probabilities(self):
        m = parse_matches(DOC)
        r = independent_markets("Bayern Munich", "Union Berlin", m)
        mk = r["markets"]
        self.assertAlmostEqual(mk["1"] + mk["X"] + mk["2"], 1.0, places=2)
        # Bayern should be favoured independently of any market price
        self.assertGreater(mk["1"], mk["2"])
        self.assertIn("totals", mk)


if __name__ == "__main__":
    unittest.main()
