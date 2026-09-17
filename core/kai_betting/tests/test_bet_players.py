"""Unit tests for KAI Bet player/squad ingestion (§6.2/§23). Pure parsers."""
from __future__ import annotations

import unittest

from core.kai_betting.data_sources.players import (
    parse_squad, player_stats_available, stats_source_note,
)


class TestPlayers(unittest.TestCase):
    def test_parse_squad(self):
        doc = {"player": [
            {"idPlayer": "1", "strPlayer": "Harry Kane", "strPosition": "Forward",
             "strNationality": "England", "strNumber": "9"},
            {"idPlayer": "2", "strPlayer": "", "strPosition": "GK"},   # dropped (no name)
        ]}
        out = parse_squad(doc)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["name"], "Harry Kane")
        self.assertEqual(out[0]["position"], "Forward")

    def test_parse_squad_empty(self):
        self.assertEqual(parse_squad(None), [])
        self.assertEqual(parse_squad({}), [])

    def test_player_stats_not_available(self):
        self.assertFalse(player_stats_available())
        self.assertIn("unavailable", stats_source_note().lower())


if __name__ == "__main__":
    unittest.main()
