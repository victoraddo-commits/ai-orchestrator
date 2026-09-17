"""Unit tests for the KAI Bet market taxonomy (§44 no-hallucination). Stdlib unittest."""
from __future__ import annotations

import unittest

from core.kai_betting.markets import (
    family_for, is_modelled, modelled_keys, reasoning_context, FAMILIES,
)


class TestMarketTaxonomy(unittest.TestCase):
    def test_core_families_modelled(self):
        for k in ("1x2", "double_chance", "over_under", "btts", "handicap", "asian_handicap"):
            self.assertTrue(is_modelled(k), k)

    def test_player_and_team_props_not_modelled(self):
        for k in ("player_shots", "player_shots_on_target", "player_assists",
                  "team_assists", "goals_in_a_row", "team_shots_on_target"):
            self.assertFalse(is_modelled(k), k)

    def test_sportybet_id_lookup(self):
        self.assertEqual(family_for("800262").key, "team_assists")
        self.assertEqual(family_for("770").key, "player_assists")
        self.assertEqual(family_for("776").key, "player_shots")
        self.assertEqual(family_for("800285").key, "player_shots_on_target")
        self.assertEqual(family_for("60010").key, "goals_in_a_row")
        self.assertEqual(family_for("16").key, "asian_handicap")

    def test_unknown_market_is_honest(self):
        ctx = reasoning_context("some_unknown_market")
        self.assertIn("unknown", ctx.lower())
        self.assertIn("273", ctx)

    def test_reasoning_context_unmodelled_flags_data_need(self):
        ctx = reasoning_context("team_assists")
        self.assertIn("NOT yet modelled", ctx)
        self.assertIn("assist", ctx.lower())

    def test_modelled_keys_subset(self):
        mk = set(modelled_keys())
        self.assertTrue(mk.issubset(set(FAMILIES.keys())))
        self.assertIn("over_under", mk)


if __name__ == "__main__":
    unittest.main()
