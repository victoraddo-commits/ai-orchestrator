"""Unit tests for the KAI Bet Second Brain adapter (§28). Pure parts."""
from __future__ import annotations

import unittest

from core.kai_betting.second_brain import lesson_entity, format_lesson


class TestSecondBrainAdapter(unittest.TestCase):
    def test_lesson_entity_bounded(self):
        self.assertEqual(lesson_entity("settle", "1X2"), "kai_bet:settle:1x2")
        self.assertEqual(lesson_entity("gate", ""), "kai_bet:gate:general")
        # same inputs -> same entity (so updates supersede instead of growing)
        self.assertEqual(lesson_entity("settle", "Over/Under"), lesson_entity("settle", "over/under"))

    def test_format_lesson_shape(self):
        fact = format_lesson(
            "settle",
            {"market": "1X2", "selection": "Home", "odds": 2.0, "model_probability": 0.6,
             "value": "moderate", "risk_level": "low"},
            {"decision": "BET", "result": "won", "profit_loss": 20.0, "notes": "paper"},
        )
        for k in ("kind", "market", "selection", "odds", "decision", "result", "profit_loss", "source"):
            self.assertIn(k, fact)
        self.assertEqual(fact["result"], "won")
        self.assertEqual(fact["source"], "kai_bet")


if __name__ == "__main__":
    unittest.main()
