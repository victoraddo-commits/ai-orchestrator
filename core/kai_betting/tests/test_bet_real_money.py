"""Unit tests for the KAI Bet real-money gate (§33). Stdlib unittest."""
from __future__ import annotations

import os
import unittest

from core.kai_betting.real_money import evaluate, REQUIRED_CHECKS, master_switch


class TestRealMoneyGate(unittest.TestCase):
    def test_default_disabled(self):
        os.environ.pop("KAI_BET_REAL_MONEY", None)
        s = evaluate({})
        self.assertFalse(s.enabled)
        self.assertFalse(s.ready)
        self.assertEqual(len(s.checks), len(REQUIRED_CHECKS))

    def test_all_checks_but_switch_off(self):
        os.environ.pop("KAI_BET_REAL_MONEY", None)
        s = evaluate({k: True for k in REQUIRED_CHECKS})
        self.assertTrue(s.ready)
        self.assertFalse(s.enabled)  # master switch still off

    def test_switch_on_but_checks_incomplete(self):
        os.environ["KAI_BET_REAL_MONEY"] = "1"
        try:
            s = evaluate({"model_calibrated": True})
            self.assertFalse(s.enabled)
            self.assertFalse(s.ready)
        finally:
            os.environ.pop("KAI_BET_REAL_MONEY", None)

    def test_switch_on_and_all_green(self):
        os.environ["KAI_BET_REAL_MONEY"] = "1"
        try:
            s = evaluate({k: True for k in REQUIRED_CHECKS})
            self.assertTrue(s.enabled)
            self.assertTrue(master_switch())
        finally:
            os.environ.pop("KAI_BET_REAL_MONEY", None)


if __name__ == "__main__":
    unittest.main()
