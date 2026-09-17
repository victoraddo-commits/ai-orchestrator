"""Unit tests for the KAI Bet screening engine (§6). Stdlib unittest."""
from __future__ import annotations

import unittest

from core.kai_betting.screening import (
    markets_from_rates, team_rates, form_points, blend, selection_probability, score_matrix,
)


class TestScreening(unittest.TestCase):
    def test_probabilities_sum_to_one(self):
        m = markets_from_rates(1.8, 1.2)
        self.assertAlmostEqual(m["1"] + m["X"] + m["2"], 1.0, places=3)
        self.assertAlmostEqual(m["btts_yes"] + m["btts_no"], 1.0, places=4)

    def test_stronger_home_wins_more(self):
        strong = markets_from_rates(2.5, 0.8)
        weak = markets_from_rates(0.8, 2.5)
        self.assertGreater(strong["1"], weak["1"])
        self.assertGreater(weak["2"], strong["2"])

    def test_totals_monotonic(self):
        m = markets_from_rates(1.8, 1.4)
        over25 = m["totals"][2.5]["over"]
        over35 = m["totals"][3.5]["over"]
        self.assertGreater(over25, over35)
        self.assertAlmostEqual(m["totals"][2.5]["over"] + m["totals"][2.5]["under"], 1.0, places=6)

    def test_score_matrix_normalised(self):
        m = score_matrix(1.5, 1.5)
        total = sum(sum(row) for row in m)
        self.assertAlmostEqual(total, 1.0, places=3)

    def test_team_rates_home_boost(self):
        h = team_rates(2.0, 1.0, 1.35, home=True)
        a = team_rates(2.0, 1.0, 1.35, home=False)
        self.assertGreater(h, a)

    def test_form_points_recency(self):
        self.assertGreater(form_points(["W", "L"]), form_points(["L", "W"]))  # recency-weighted
        self.assertEqual(form_points([]), 0.0)
        self.assertAlmostEqual(form_points(["W"]), 3.0)

    def test_blend_endpoints(self):
        self.assertAlmostEqual(blend(0.7, 0.5, 1.0), 0.7)   # trust KAI fully
        self.assertAlmostEqual(blend(0.7, 0.5, 0.0), 0.5)   # copy market
        self.assertAlmostEqual(blend(0.7, 0.5, 0.5), 0.6)

    def test_selection_probability_mapping(self):
        m = markets_from_rates(1.8, 1.2)
        self.assertIsNotNone(selection_probability(m, "1x2", "Home"))
        self.assertIsNotNone(selection_probability(m, "over_under", "over", 2.5))
        self.assertIsNotNone(selection_probability(m, "btts", "yes"))
        self.assertIsNone(selection_probability(m, "player_shots", "over", 3.5))


if __name__ == "__main__":
    unittest.main()
